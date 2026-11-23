"""Private local planner claim verifier. No database writes or legacy reads."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat

from .filex_read import canonical,digest

IDENTITY=('officialRequestId','taskId','projectId','npcId','payloadSha256')
SCHEMA='ngs.npc-one-stop.replace.v1'
NEW_SCHEMA='ngs.npc-one-stop.v1'
SELECTION_NEW='ngs.npc-one-stop.selection.v1'
SELECTION_REPLACE='ngs.npc-one-stop.selection.replace.v1'
REGENERATE_TYPE='npc.animation.regenerate'
REGENERATE_SCHEMA='ngs.npc-animation.regenerate.v1'
SELECTION_SCHEMAS=(SELECTION_NEW,SELECTION_REPLACE)
REPLACE_SCHEMAS=(SCHEMA,SELECTION_REPLACE)
NPC_SCHEMAS=(SCHEMA,NEW_SCHEMA,*SELECTION_SCHEMAS)
MOTION_ID=r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}'
POSES=('idle','combat')
DIRECTIONS=('n','ne','e','se','s','sw','w','nw')
HASH=r'[a-f0-9]{64}'
CLAIM_KEYS=set(IDENTITY)|{'claimStamp'}
SAFE_INT=2**53-1


class ClaimDenied(Exception):
    pass


def require(ok):
    if not ok:raise ClaimDenied('WORKFLOW_DELETE_CLAIM_DENIED')


def matches(value,pattern):
    return type(value) is str and re.fullmatch(pattern,value) is not None


def motion_slot(preset_id):
    return hashlib.sha256(preset_id.encode('utf-8')).hexdigest()[:16]


def selection_rows(payload):
    rows=payload['motionSelection']
    require(type(rows) is list and bool(rows))
    ids=set()
    slots={}
    for row in rows:
        require(type(row) is dict and set(row)=={'presetId','posePreset','assetSetSha256'}
                and matches(row['presetId'],MOTION_ID) and row['posePreset'] in POSES
                and matches(row['assetSetSha256'],HASH))
        folded=row['presetId'].lower()
        slot=motion_slot(row['presetId'])
        require(folded not in ids and slot not in slots)
        ids.add(folded)
        slots[slot]=row
    return slots

class PrivatePlannerClaim:
    def __init__(self,path,dev,inode):
        self.path=Path(path)
        require(self.path.is_absolute() and str(self.path)==path and type(dev) is int and type(inode) is int)
        self.identity=(dev,inode)
        with self.snapshot():pass

    def check_path(self):
        p=self.path
        require(p.resolve(strict=True)==p)
        parent=p.parent.lstat();db=p.lstat()
        require(stat.S_ISDIR(parent.st_mode) and stat.S_IMODE(parent.st_mode)==0o700 and parent.st_uid==os.getuid()
                and stat.S_ISREG(db.st_mode) and stat.S_IMODE(db.st_mode)==0o600 and db.st_uid==os.getuid()
                and db.st_nlink==1 and (db.st_dev,db.st_ino)==self.identity)
        # No files are created by the verifier. Reject WAL and a stale SHM;
        # rollback-journal peers cannot commit a competing state transition
        # while the verified read transaction is held.
        for suffix in ('-wal','-shm'):
            side=Path(str(p)+suffix)
            require(not side.exists() and not side.is_symlink())

    @contextmanager
    def snapshot(self):
        self.check_path()
        db=sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=2,isolation_level=None)
        try:
            db.row_factory=sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            require(db.execute('PRAGMA journal_mode').fetchone()[0]=='delete')
            require([row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                ==['workflow_runs','workflow_tasks'])
            self.check_path()
            yield db
            self.check_path()
        finally:
            db.close()

    @contextmanager
    def authorized(self,claim):
        with self._selected(claim,'delete') as (identity,_,selection):
            yield identity,selection

    @contextmanager
    def authorized_preset(self,claim):
        with self._selected(claim,'preset') as authenticated:
            yield authenticated

    @contextmanager
    def authorized_representative(self,claim):
        with self._selected(claim,'planning_representative') as authenticated:
            yield authenticated

    @contextmanager
    def authorized_idle_plan(self,claim):
        with self._selected(claim,'planning_idle') as authenticated:
            yield authenticated

    @contextmanager
    def authorized_document_chain(self,claim,stage):
        key={'overview':'planning_overview','portrait-art':'planning_portrait_art',
             'representative':'planning_representative','idle':'planning_idle'}[stage]
        with self._selected(claim,key,chain_result=True) as authenticated:
            yield authenticated

    @contextmanager
    def authorized_portrait(self,claim):
        with self._selected(claim,'portrait_representative') as authenticated:
            yield authenticated

    @contextmanager
    def authorized_idle_portrait(self,claim):
        with self._selected(claim,'portrait_idle_reference') as authenticated:
            yield authenticated

    @contextmanager
    def authorized_direction_portrait(self,claim):
        require(type(claim) is dict and type(claim.get('officialRequestId')) is str)
        oid=claim['officialRequestId']
        directions=[d for d in ('n','ne','e','se','s','sw','w','nw')
                    if claim.get('taskId')=='workflow.task.'+digest([oid,'portrait_'+d])]
        require(len(directions)==1)
        with self._selected(claim,'portrait_'+directions[0],direction=directions[0],chain_result=True) as (identity,character,pixel_direction):
            yield {**identity,'direction':directions[0],**({'referenceStage':'portrait_pixel_idle_reference'} if pixel_direction else {})},character

    @contextmanager
    def authorized_media(self,claim,kind):
        require(kind in ('animation','frames') and type(claim) is dict
                and matches(claim.get('officialRequestId'),r'request\.official\.[a-f0-9]{64}')
                and matches(claim.get('taskId'),r'workflow\.task\.[a-f0-9]{64}'))
        oid=claim['officialRequestId']
        legacy={'workflow.task.'+digest([oid,f'{kind}_{motion}_{d}']):(motion,d)
                for motion in ('walk','run') for d in DIRECTIONS}
        motion,d=legacy.get(claim['taskId'],(None,None))
        key=f'{kind}_{motion}_{d}' if motion is not None else None
        with self._selected(claim,key,media=(kind,motion,d)) as (identity,_,binding):
            yield {**identity,**binding}

    @contextmanager
    def authorized_static_pixel(self,claim):
        require(type(claim) is dict and matches(claim.get('officialRequestId'),r'request\.official\.[a-f0-9]{64}'))
        oid=claim['officialRequestId']
        choices={'workflow.task.'+digest([oid,'portrait_pixel_idle_'+(d or 'reference')]):d
                 for d in (None,'n','ne','e','se','s','sw','w','nw')}
        require(type(claim.get('taskId')) is str and claim['taskId'] in choices)
        direction=choices[claim['taskId']]
        with self._selected(claim,'portrait_pixel_idle_'+(direction or 'reference'),pixel=(direction,)) as (identity,_):
            yield {**identity,'posePreset':'idle','direction':direction}

    @contextmanager
    def _selected(self,claim,key,*,media=None,direction=None,chain_result=False,pixel=None):
        is_npc_task=key!='delete'
        task_type,worker=(('npc.portrait-pixel','postprocess') if pixel is not None else {'animation':('npc.animation-native-png','animation'),
                           'frames':('npc.frames-postprocess','postprocess')}[media[0]] if media else ('npc.portrait-direction','image') if direction else {
            'delete':('npc.delete-folder','file'),
            'preset':('npc.preset','document'),
            'planning_overview':('npc.plan-overview','document'),
            'planning_portrait_art':('npc.plan-portrait-art','document'),
            'planning_representative':('npc.plan-representative','document'),
            'planning_idle':('npc.plan-idle','document'),
            'portrait_representative':('npc.portrait-representative','image'),
            'portrait_idle_reference':('npc.portrait-idle-reference','image'),
        }[key])
        require(type(claim) is dict and set(claim)==CLAIM_KEYS
                and matches(claim['officialRequestId'],r'request\.official\.[a-f0-9]{64}')
                and matches(claim['taskId'],r'workflow\.task\.[a-f0-9]{64}')
                and matches(claim['npcId'],r'(?:npc|character)\.[A-Za-z0-9][A-Za-z0-9._-]{0,120}')
                and matches(claim['payloadSha256'],r'[a-f0-9]{64}')
                and claim['projectId']=='binari'
                and type(claim['claimStamp']) is int and 0<=claim['claimStamp']<=SAFE_INT)
        oid=claim['officialRequestId'];task=claim['taskId'];owner=claim['npcId']
        selected_media=media is not None and key is None
        require(key is not None or selected_media)
        with self.snapshot() as db:
            slot=None
            if selected_media:
                found=db.execute('SELECT task_key FROM workflow_tasks WHERE task_id=? AND official_request_id=?',
                                 (task,oid)).fetchone()
                require(found is not None)
                key=found['task_key']
                match=re.fullmatch(rf'{media[0]}_motion_([a-f0-9]{{16}})_({"|".join(DIRECTIONS)})',key)
                require(match is not None)
                slot,media_direction=match.groups()
            require(task=='workflow.task.'+digest([oid,key]))
            row=db.execute("""SELECT r.*,t.task_key,t.task_input,t.depends_on_task_id,t.updated_at AS task_stamp
                FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
                WHERE t.task_id=? AND t.official_request_id=? AND t.status='running'
                AND t.task_type=? AND t.worker_type=? AND t.task_key=?
                AND t.result_ref IS NULL AND (? OR t.depends_on_task_id IS NULL) AND r.state='planned'""",
                (task,oid,task_type,worker,key,is_npc_task)).fetchone()
            regeneration=(row is not None and selected_media and row['request_type']==REGENERATE_TYPE
                          and row['schema_version']==REGENERATE_SCHEMA)
            require(row is not None and row['project_id']=='binari'
                    and (regeneration or row['request_type']=='npc.workflow'
                         and row['schema_version'] in (NPC_SCHEMAS if is_npc_task else REPLACE_SCHEMAS))
                    and row['task_stamp']==claim['claimStamp']
                    and type(row['registered_at']) is int and 0<=row['registered_at']<=SAFE_INT
                    and row['task_stamp']>=row['registered_at'] and row['reason'] is None
                    and row['payload_sha256']==claim['payloadSha256'])
            payload=json.loads(row['payload'])
            selection=regeneration or row['schema_version'] in SELECTION_SCHEMAS
            replacement=row['schema_version'] in REPLACE_SCHEMAS
            if regeneration:
                require(type(payload) is dict and set(payload)=={'schemaVersion','projectId','npcId','motionSelection'}
                        and payload['schemaVersion']==REGENERATE_SCHEMA and payload['projectId']=='binari'
                        and payload['npcId']==owner)
                slots=selection_rows(payload)
                c={'npcId':owner}
            else:
                fields={'schemaVersion','projectId','intent','character','sourceRefs'}
                require(type(payload) is dict
                        and payload.get('schemaVersion')==row['schema_version'] and payload.get('projectId')=='binari'
                        and type(payload.get('intent')) is str and 5<=len(payload['intent'])<=3500
                        and bool(payload['intent'].strip()) and type(payload.get('sourceRefs')) is list
                        and bool(payload['sourceRefs'])
                        and all(type(x) is str and 1<=len(x)<=300 and bool(x.strip()) for x in payload['sourceRefs']))
                if selection:
                    require(set(payload)==fields|{'motionSelection'})
                    slots=selection_rows(payload)
                else:
                    fields.add('includeCombat')
                    require(fields<=payload.keys()<=fields|{'motionPresets'} and payload['includeCombat'] is False
                            and ('motionPresets' not in payload or payload['motionPresets']=={'walk':'walk','run':'run'}))
                    slots={}
                c=payload['character']
                require(type(c) is dict and set(c)=={'npcId','name','slug','role','actId','chapterId'} and c['npcId']==owner
                        and matches(c['slug'],r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}' if replacement
                                    else r'[A-Za-z0-9._:-]{1,200}')
                        and all(matches(c[k],r'[A-Za-z0-9._:-]{1,200}') for k in ('actId','chapterId'))
                        and type(c['name']) is str and 1<=len(c['name'])<=60 and bool(c['name'].strip())
                        and type(c['role']) is str and 1<=len(c['role'])<=300 and bool(c['role'].strip()))
                if owner.startswith('character.'):
                    require(replacement and 12<=len(owner)<=90 and matches(owner,r'character\.[a-z0-9]+(?:[-._][a-z0-9]+)*')
                            and c['slug']==owner.removeprefix('character.') and c['actId'] in ('act1','act01')
                            and matches(c['chapterId'],r'(?:ch|chapter0)[1-5]'))
                else:
                    require(owner.startswith('npc.'))
            delete_predecessor=None
            if key=='preset' and owner.startswith('character.'):
                delete_predecessor='workflow.task.'+digest([oid,'delete'])
                delete_input=canonical({'npcId':owner,'scope':'exact-npc-folder','preserveRequestHistory':True,
                                        'noOldDataReuse':True}).decode()
                require(row['depends_on_task_id']==delete_predecessor and db.execute("""SELECT 1 FROM workflow_tasks
                    WHERE official_request_id=? AND task_id=? AND task_key='delete' AND task_type='npc.delete-folder'
                    AND worker_type='file' AND status='completed' AND result_ref=task_id AND depends_on_task_id IS NULL
                    AND task_input=?""",(oid,delete_predecessor,delete_input)).fetchone() is not None)
            if media:
                require(selection==selected_media and (not selection or slot in slots))
            expected={'npcId':owner}
            if direction is not None:expected['direction']=direction
            pixel_direction=(direction is not None and not media and
                             row['depends_on_task_id']=='workflow.task.'+digest([oid,'portrait_pixel_idle_reference']))
            if pixel_direction or (key=='portrait_idle_reference' and row['task_input']==canonical(
                    {'npcId':owner,'imageStyle':'pixel-sprite.v1'}).decode()):
                expected['imageStyle']='pixel-sprite.v1'

            if key=='preset':
                expected.update(mode='fresh-replacement' if replacement else 'new',noOldDataReuse=True)
            elif key=='delete':
                expected.update(scope='exact-npc-folder',preserveRequestHistory=True,noOldDataReuse=True)
            media_binding=None
            if media:
                kind,motion,d=media
                if selection:
                    chosen=slots[slot]
                    d=media_direction
                    motion=chosen['presetId']
                    motion_fields={'motion':motion,'posePreset':chosen['posePreset'],'assetSetSha256':chosen['assetSetSha256']}
                    stem=f'motion_{slot}_{d}'
                else:
                    motion_fields={'motion':motion}
                    stem=f'{motion}_{d}'
                media_binding={**motion_fields,'direction':d}
                expected.update(media_binding)
                expected.update(output='native-png') if kind=='animation' else expected.update(input='native-png')
                if regeneration and kind=='animation':
                    require(row['depends_on_task_id'] is None)
                else:
                    if kind=='animation':
                        parent_key='portrait_'+d
                        parent_type,parent_worker='npc.portrait-direction','image'
                        parent_input={'npcId':owner,'direction':d}
                    else:
                        parent_key='animation_'+stem
                        parent_type,parent_worker='npc.animation-native-png','animation'
                        parent_input={'npcId':owner,**media_binding,'output':'native-png'}
                    parent='workflow.task.'+digest([oid,parent_key])
                    prior=db.execute("""SELECT task_input,depends_on_task_id FROM workflow_tasks
                        WHERE task_id=? AND official_request_id=? AND task_key=? AND task_type=?
                        AND worker_type=? AND status='completed'""",
                        (parent,oid,parent_key,parent_type,parent_worker)).fetchone()
                    if prior and kind=='animation' and prior['depends_on_task_id']=='workflow.task.'+digest([oid,'portrait_pixel_idle_reference']):
                        parent_input['imageStyle']='pixel-sprite.v1'
                    require(row['depends_on_task_id']==parent and prior is not None
                            and prior['task_input']==canonical(parent_input).decode())
                    if regeneration:
                        require(prior['depends_on_task_id'] is None)
                if regeneration:
                    media_binding['regeneration']=True
            if pixel is not None:
                d,=pixel
                expected.update(posePreset='idle',direction=d)
                pk='portrait_'+d if d else 'portrait_idle_reference'
                pt='npc.portrait-direction' if d else 'npc.portrait-idle-reference'
                pi={'npcId':owner,'imageStyle':'pixel-sprite.v1',**({'direction':d} if d else {})}
                pid='workflow.task.'+digest([oid,pk])
                require(row['depends_on_task_id']==pid and db.execute("""SELECT 1 FROM workflow_tasks
                    WHERE task_id=? AND official_request_id=? AND task_key=? AND task_type=?
                    AND worker_type='image' AND status='completed' AND task_input=?""",
                    (pid,oid,pk,pt,canonical(pi).decode())).fetchone() is not None)
            raw=canonical(payload)
            require(len(raw)<=65536 and raw.decode()==row['payload'] and digest(payload)==row['payload_sha256']
                    and row['task_input']==canonical(expected).decode())
            chain=False
            if key in ('planning_overview','planning_portrait_art','planning_representative','planning_idle'):
                expected_new={('planning_overview','npc.plan-overview'),('planning_portrait_art','npc.plan-portrait-art')}
                stage_rows=db.execute('''SELECT task_id,task_key,task_type,worker_type,task_input,depends_on_task_id
                    FROM workflow_tasks WHERE official_request_id=?
                    AND task_key IN ('planning_overview','planning_portrait_art')''',(oid,)).fetchall()
                if stage_rows:
                    slots={x['task_key']:x for x in stage_rows}
                    require(len(stage_rows)==2 and {(x['task_key'],x['task_type']) for x in stage_rows}==expected_new)
                    for k,upstream in (('planning_overview','preset'),('planning_portrait_art','planning_overview')):
                        x=slots[k]
                        require(x['task_id']=='workflow.task.'+digest([oid,k]) and x['worker_type']=='document'
                                and x['task_input']==canonical({'npcId':owner}).decode()
                                and x['depends_on_task_id']=='workflow.task.'+digest([oid,upstream]))
                    chain=True
                if key in ('planning_overview','planning_portrait_art'):require(chain)
            parents={
                'portrait_representative':('planning_representative','npc.plan-representative','document'),
                'planning_idle':('planning_representative','npc.plan-representative','document'),
                'portrait_idle_reference':('planning_idle','npc.plan-idle','document'),
                'planning_overview':('preset','npc.preset','document'),
                'planning_portrait_art':('planning_overview','npc.plan-overview','document'),
            }
            if chain:parents['planning_representative']=('planning_portrait_art','npc.plan-portrait-art','document')
            parent_row=('portrait_idle_reference','npc.portrait-idle-reference','image') if direction and not media else parents.get(key)
            def completed(k,kind,lane,inp=None):
                rid='workflow.task.'+digest([oid,k])
                if inp is None:inp={'npcId':owner}
                if k=='preset':
                    inp.update(mode='fresh-replacement' if replacement else 'new',noOldDataReuse=True)
                require(db.execute('''SELECT 1 FROM workflow_tasks
                    WHERE task_id=? AND official_request_id=? AND task_key=? AND task_type=? AND worker_type=?
                    AND status='completed' AND task_input=?''',
                    (rid,oid,k,kind,lane,canonical(inp).decode())).fetchone() is not None)
                return rid
            if pixel_direction:
                require(row['depends_on_task_id']==completed('portrait_pixel_idle_reference','npc.portrait-pixel','postprocess',
                        {'npcId':owner,'posePreset':'idle','direction':None}))
            elif parent_row:require(row['depends_on_task_id']==completed(*parent_row))
            if key=='portrait_idle_reference':completed('portrait_representative','npc.portrait-representative','image')
            identity={k:claim[k] for k in IDENTITY}
            if key=='delete':
                yield identity,c,tuple(slots.values())
            elif key=='preset':
                yield identity,c,delete_predecessor
            elif media_binding is not None:
                yield identity,c,media_binding
            elif chain_result:
                yield identity,c,pixel_direction if direction else chain
            else:
                yield identity,c
