"""Private local planner claim verifier. No database writes or legacy reads."""
from contextlib import contextmanager
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
CLAIM_KEYS=set(IDENTITY)|{'claimStamp'}
SAFE_INT=2**53-1


class ClaimDenied(Exception):
    pass


def require(ok):
    if not ok:raise ClaimDenied('WORKFLOW_DELETE_CLAIM_DENIED')


def matches(value,pattern):
    return type(value) is str and re.fullmatch(pattern,value) is not None


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
        with self._selected(claim,'delete') as (identity,_):
            yield identity

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
        require(kind in ('animation','frames') and type(claim) is dict)
        require(matches(claim.get('officialRequestId'),r'request\.official\.[a-f0-9]{64}'))
        oid=claim['officialRequestId']
        allowed={('workflow.task.'+digest([oid,f'{kind}_{motion}_{direction}'])):
                 (motion,direction) for motion in ('walk','run') for direction in ('n','ne','e','se','s','sw','w','nw')}
        require(type(claim.get('taskId')) is str and claim['taskId'] in allowed)
        motion,direction=allowed[claim['taskId']]
        with self._selected(claim,f'{kind}_{motion}_{direction}',media=(kind,motion,direction)) as (identity,_):
            yield {**identity,'motion':motion,'direction':direction}

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
        require(task=='workflow.task.'+digest([oid,key]))
        with self.snapshot() as db:
            row=db.execute("""SELECT r.*,t.task_key,t.task_input,t.depends_on_task_id,t.updated_at AS task_stamp
                FROM workflow_tasks t JOIN workflow_runs r USING(official_request_id)
                WHERE t.task_id=? AND t.official_request_id=? AND t.status='running'
                AND t.task_type=? AND t.worker_type=? AND t.task_key=?
                AND t.result_ref IS NULL AND (? OR t.depends_on_task_id IS NULL) AND r.state='planned'""",
                (task,oid,task_type,worker,key,is_npc_task)).fetchone()
            require(row is not None and row['project_id']=='binari' and row['request_type']=='npc.workflow'
                    and row['schema_version'] in ((SCHEMA,NEW_SCHEMA) if is_npc_task else (SCHEMA,)) and row['task_stamp']==claim['claimStamp']
                    and type(row['registered_at']) is int and 0<=row['registered_at']<=SAFE_INT
                    and row['task_stamp']>=row['registered_at'] and row['reason'] is None
                    and row['payload_sha256']==claim['payloadSha256'])
            payload=json.loads(row['payload']); before={'schemaVersion','projectId','intent','character','sourceRefs','includeCombat'}
            require(type(payload) is dict and before<=payload.keys()<=before|{'motionPresets'}
                    and payload['schemaVersion']==row['schema_version'] and payload['projectId']=='binari'
                    and payload['includeCombat'] is False and type(payload['intent']) is str
                    and 5<=len(payload['intent'])<=3500 and bool(payload['intent'].strip())
                    and type(payload['sourceRefs']) is list and bool(payload['sourceRefs'])
                    and all(type(s) is str and 1<=len(s)<=300 and bool(s.strip()) for s in payload['sourceRefs'])
                    and ('motionPresets' not in payload or payload['motionPresets']=={'walk':'walk','run':'run'}))
            c=payload['character']
            require(type(c) is dict and set(c)=={'npcId','name','slug','role','actId','chapterId'} and c['npcId']==owner
                    and matches(c['slug'],r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}' if row['schema_version']==SCHEMA
                                else r'[A-Za-z0-9._:-]{1,200}')
                    and all(matches(c[k],r'[A-Za-z0-9._:-]{1,200}') for k in ('actId','chapterId'))
                    and type(c['name']) is str and 1<=len(c['name'])<=60 and bool(c['name'].strip())
                    and type(c['role']) is str and 1<=len(c['role'])<=300 and bool(c['role'].strip()))
            expected={'npcId':owner}
            if direction is not None:expected['direction']=direction
            pixel_direction=(direction is not None and not media and
                             row['depends_on_task_id']=='workflow.task.'+digest([oid,'portrait_pixel_idle_reference']))
            if pixel_direction or (key=='portrait_idle_reference' and row['task_input']==canonical(
                    {'npcId':owner,'imageStyle':'pixel-sprite.v1'}).decode()):
                expected['imageStyle']='pixel-sprite.v1'

            if key=='preset':
                expected.update(mode='fresh-replacement' if row['schema_version']==SCHEMA else 'new',noOldDataReuse=True)
            elif key=='delete':
                expected.update(scope='exact-npc-folder',preserveRequestHistory=True,noOldDataReuse=True)
            if media:
                kind,motion,direction=media
                expected.update(motion=motion,direction=direction)
                expected.update(output='native-png') if kind=='animation' else expected.update(input='native-png')
                if kind=='animation':
                    parent_key='portrait_'+direction
                    parent_type,parent_worker='npc.portrait-direction','image'
                    parent_input={'npcId':owner,'direction':direction}
                else:
                    parent_key='animation_'+motion+'_'+direction
                    parent_type,parent_worker='npc.animation-native-png','animation'
                    parent_input={'npcId':owner,'direction':direction,'motion':motion,'output':'native-png'}
                parent='workflow.task.'+digest([oid,parent_key])
                prior=db.execute("""SELECT task_input,depends_on_task_id FROM workflow_tasks
                    WHERE task_id=? AND official_request_id=? AND task_key=? AND task_type=?
                    AND worker_type=? AND status='completed'""",
                    (parent,oid,parent_key,parent_type,parent_worker)).fetchone()
                if prior and kind=='animation' and prior['depends_on_task_id']=='workflow.task.'+digest([oid,'portrait_pixel_idle_reference']):
                    parent_input['imageStyle']='pixel-sprite.v1'
                require(row['depends_on_task_id']==parent and prior is not None
                        and prior['task_input']==canonical(parent_input).decode())
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
                    inp.update(mode='fresh-replacement' if row['schema_version']==SCHEMA else 'new',noOldDataReuse=True)
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
            yield (identity,c,pixel_direction if direction else chain) if chain_result else (identity,c)
