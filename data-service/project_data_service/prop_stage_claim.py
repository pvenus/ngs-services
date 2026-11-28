"""Reconstruct the small formal prop DAG before exposing any intermediate bytes."""
from contextlib import contextmanager
import re

from .filex_read import canonical,digest
from .workflow_delete_claim import ClaimDenied,PrivatePlannerClaim,SAFE_INT
from .workflow_motion_2d import strict_json
from .workflow_prop_package import Denied,keys,match,need,pin,prop_id

P='binari'
GENERATE='prop.generate'
REGENERATE='prop.regenerate'
APPEND='prop.animation.generate'
SCALE='prop.scale.generate'
CANDLE_CHROMA_PROFILE='candle-chroma-headroom-v1'
CANDLE_CHROMA_PROP='candle-brass-petal-unlit-clean'
FAMILIES={GENERATE:'ngs.prop-generate.v1',REGENERATE:'ngs.prop-regenerate.v1',APPEND:'ngs.prop-animation-generate.v1',SCALE:'ngs.prop-scale-generate.v1'}
DESIGN='prop_design';BASE='prop_base';MOTION='prop_motion';DETAIL='prop_detail';PIXEL='prop_pixel';PIXEL_SOURCE='prop_pixel_source';PIXEL_DETAIL='prop_pixel_detail';SPIRIT='prop_spirit'
MOTION_DETAIL='prop_animation_detail';MOTION_PIXEL='prop_animation_pixel';PUBLISH='prop_pack_publish'
ROLES={DESIGN:('design','document','design-spec'),BASE:('base','image','base-png'),
       MOTION:('motion','animation','i2v-rgb'),DETAIL:('detail','postprocess','detail-rgba'),
       PIXEL_SOURCE:('pixel-source','image','pixel-source-png'),PIXEL_DETAIL:('pixel-detail','postprocess','pixel-detail-rgba'),
       PIXEL:('pixel','postprocess','pixel-rgba'),SPIRIT:('spirit','postprocess','spirit-rgba'),MOTION_DETAIL:('motion-detail','postprocess','animation-detail-rgba'),
       MOTION_PIXEL:('motion-pixel','postprocess','animation-pixel-rgba'),PUBLISH:('publish','postprocess','package')}
SCALE_KEYS=tuple(f'prop_scale_{n:03d}' for n in (5,10,20,30,40,50,60,70,80,90,100))
ROLES.update((key,('derive','postprocess','scale-pair')) for key in SCALE_KEYS)
CLAIM=('officialRequestId','taskId','projectId','payloadSha256','claimStamp')
OFFICIAL=r'request\.official\.[a-f0-9]{64}'
HASH=r'[a-f0-9]{64}'
TASK=r'workflow\.task\.[a-f0-9]{64}'

def task_id(oid,key):return 'workflow.task.'+digest([oid,key])
def ref_name(key,sha):
    need(key in ROLES and match(sha,HASH))
    return 'prop.'+ROLES[key][2]+'.'+sha

def valid_ref(key,value):return type(value)is str and match(value[-64:],HASH) and value==ref_name(key,value[-64:])
def text(v,maximum):
    return type(v)is str and v.strip().strip('\ufeff').strip()==v and 0<len(v.encode('utf-8'))<=maximum and not any(
      (0<=ord(c)<=8 or ord(c) in (11,12) or 14<=ord(c)<=31 or 127<=ord(c)<=159) for c in v)

def payload_valid(p,kind):
    required={'schemaVersion','projectId','propId'}
    if kind in (REGENERATE,APPEND,SCALE):required.add('source')
    if kind==APPEND:
        required.update(('motionId','prompt'))
        if 'renderProfile' in p:required.add('renderProfile')
    elif kind!=SCALE:required.update(('intent','designContextSha256'))
    if kind==SCALE or kind==GENERATE and 'scalePercents' in p:
        from .prop_scale import percents
        required.add('scalePercents');need(percents(p.get('scalePercents')) and (kind==SCALE or p.get('presentationProfile')=='spirit-v2'))
    if kind not in (APPEND,SCALE) and 'presentationProfile' in p:
        required.add('presentationProfile');need(p['presentationProfile'] in ('spirit-v1','spirit-v2'))
    if kind not in (APPEND,SCALE) and 'motion' in p:required.add('motion')
    if kind==GENERATE and 'reference' in p:required.add('reference')
    need(keys(p,required) and p['schemaVersion']==FAMILIES[kind] and p['projectId']==P and prop_id(p['propId']))
    if 'reference' in p:need(pin(p['reference'])['propId']!=p['propId'])
    if 'source' in p:pin({'propId':p['propId'],**p['source']});need(keys(p['source'],('revision','sha256')) and p['source']['revision']<SAFE_INT-1)
    if kind==APPEND:
        if 'renderProfile' in p:need(p['renderProfile']==CANDLE_CHROMA_PROFILE and p['propId']==CANDLE_CHROMA_PROP)
        need(prop_id(p['motionId']) and text(p['prompt'],2000) and not re.search(r'\bembedding:',p['prompt'],re.I))
    elif kind!=SCALE:need(text(p['intent'],4000) and match(p['designContextSha256'],HASH) and p.get('motion','still') in ('still','subtle'))

def build_graph(oid,kind,p,*,split_scales=True):
    payload_valid(p,kind);rows={}
    make_id=lambda key:task_id(oid,key)
    scope={'propId':p['propId']}
    if 'presentationProfile' in p:scope['presentationProfile']=p['presentationProfile']
    if 'source' in p:scope['source']=p['source']
    if kind not in (APPEND,SCALE):scope['designContextSha256']=p['designContextSha256']
    def add(key,parent,extra,role=None):
        base,worker,_=ROLES[key];t=make_id(key)
        rows[key]={'task_id':t,'task_key':key,'task_type':'prop.scale.derive' if key in SCALE_KEYS else kind+'.'+(role or base),'worker_type':worker,
                   'depends_on_task_id':make_id(parent) if parent else None,'task_input':canonical({**scope,**extra}).decode()}
    def add_scales(parent):
        if not split_scales:return parent
        for n in sorted(p.get('scalePercents',[])):
            key=f'prop_scale_{n:03d}'
            add(key,parent,{'scalePercent':n})
            parent=key
        return parent
    if kind==SCALE:
        parent=add_scales(None)
        add(PUBLISH,parent,{'scalePercents':p['scalePercents'],'dependsOnTaskIds':[row['task_id'] for row in rows.values()]})
    elif kind==APPEND:
        id=p['motionId']
        add(MOTION,None,{'motionId':id,'prompt':p['prompt']},'raw')
        add(MOTION_DETAIL,MOTION,{'motionId':id,'rawTaskId':make_id(MOTION)},'detail')
        add(MOTION_PIXEL,MOTION_DETAIL,{'motionId':id,'detailTaskId':make_id(MOTION_DETAIL)},'pixel')
        add(PUBLISH,MOTION_PIXEL,{'dependsOnTaskIds':[x['task_id'] for x in rows.values()]})
    else:
        design=make_id(DESIGN);base=make_id(BASE);m=p.get('motion','still')
        add(DESIGN,None,{'intent':p['intent'],'motion':m})
        add(BASE,DESIGN,{'designTaskId':design,**({'reference':p['reference']} if kind==GENERATE and 'reference' in p else {})})
        add(DETAIL,BASE,{'designTaskId':design,'baseTaskId':base,'sourceTaskId':base})
        v2=p.get('presentationProfile')=='spirit-v2'
        if v2:
            add(PIXEL_SOURCE,DETAIL,{'designTaskId':design,'detailTaskId':make_id(DETAIL)})
            add(PIXEL_DETAIL,PIXEL_SOURCE,{'designTaskId':design,'sourceTaskId':make_id(PIXEL_SOURCE)})
        add(PIXEL,PIXEL_DETAIL if v2 else DETAIL,{'designTaskId':design,'detailTaskId':make_id(PIXEL_DETAIL if v2 else DETAIL),
            **({'geometryTaskId':make_id(DETAIL)} if v2 else {})})
        if 'presentationProfile' in p:add(SPIRIT,DETAIL,{'designTaskId':design,'detailTaskId':make_id(DETAIL)})
        if m=='subtle':
            add(MOTION,BASE,{'designTaskId':design,'baseTaskId':base})
            add(MOTION_DETAIL,MOTION,{'designTaskId':design,'baseTaskId':base,'sourceTaskId':make_id(MOTION)})
            add(MOTION_PIXEL,MOTION_DETAIL,{'designTaskId':design,'detailTaskId':make_id(MOTION_DETAIL)})
        scale_parent=add_scales(PIXEL_DETAIL)
        add(PUBLISH,MOTION_PIXEL if m=='subtle' else PIXEL,{'dependsOnTaskIds':[x['task_id'] for x in rows.values()],**({'scalePercents':p['scalePercents']} if 'scalePercents' in p else {})})
        if 'presentationProfile' in p:
            parents=([make_id(PIXEL)] if m=='subtle' else [])+[make_id(SPIRIT)]
            if split_scales and 'scalePercents' in p:parents.append(make_id(scale_parent))
            rows[PUBLISH].update({f'depends_on_task_id_{i+2}':dep for i,dep in enumerate(parents)})
    return rows

def from_run(db,run):
    need(run is not None and run['state']=='planned' and run['reason'] in (None,'RS_COMPLETED') and
         run['project_id']==P and run['request_type'] in FAMILIES and run['schema_version']==FAMILIES[run['request_type']])
    p=strict_json(run['payload'].encode());need(type(p)is dict and run['payload']==canonical(p).decode() and digest(p)==run['payload_sha256'])
    rows=db.execute('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 23',(run['official_request_id'],)).fetchall()
    expected=build_graph(run['official_request_id'],run['request_type'],p,split_scales=any(r['task_key'] in SCALE_KEYS for r in rows))
    need(len(rows)==len(expected))
    exact_rows={r['task_key']:dict(r) for r in rows}
    need(len(exact_rows)==len(expected) and all(k in exact_rows and
         all(exact_rows[k].get(f)==v for f,v in want.items()) and
         all(exact_rows[k].get(f'depends_on_task_id_{i}')==want.get(f'depends_on_task_id_{i}') for i in range(2,6)) for k,want in expected.items()))
    return p,{k:exact_rows[k] for k in expected}

class PropClaimAuthority:
    def __init__(self,claim):
        need(isinstance(claim,PrivatePlannerClaim));self.claim=claim
    @contextmanager
    def selected(self,v,stages):
        need(keys(v,CLAIM) and v['projectId']==P and match(v['officialRequestId'],OFFICIAL) and
             match(v['taskId'],TASK) and match(v['payloadSha256'],HASH) and type(v['claimStamp']) is int and
             0<v['claimStamp']<=SAFE_INT)
        try:
            with self.claim.snapshot() as db:
                oid=v['officialRequestId'];run=db.execute('SELECT * FROM workflow_runs WHERE official_request_id=?',(oid,)).fetchone()
                p,rows=from_run(db,run)
                need(run['reason'] is None and run['payload_sha256']==v['payloadSha256'] and 0<=run['registered_at']<=v['claimStamp'])
                found=[k for k in stages if k in rows and rows[k]['task_id']==v['taskId']]
                need(len(found)==1);key=found[0];row=rows[key]
                need(row['status']=='running' and row['result_ref'] is None and row['updated_at']==v['claimStamp'])
                if row['depends_on_task_id'] is not None:
                    predecessor=next(x for x in rows.values() if x['task_id']==row['depends_on_task_id'])
                    need(predecessor['status']=='completed' and valid_ref(predecessor['task_key'],predecessor['result_ref']))
                if key==PUBLISH:
                    need(all(r['status']=='completed' and valid_ref(k,r['result_ref']) for k,r in rows.items() if k!=PUBLISH))
                result=dict(run),p,rows,key
            yield result
        except ClaimDenied:raise Denied('WORKFLOW_PROP_PACKAGE_DENIED') from None
    def authorized_pin(self,q):
        pin(q)
        try:
            with self.claim.snapshot() as db:
                # This reader is only for committed task evidence/canonical worker source.
                # Actual immutable files and metadata are independently checked by Filex.
                from .workflow_prop_animation_remove import KEY as REMOVE,graph as remove_graph
                rs=db.execute("SELECT task_key,official_request_id FROM workflow_tasks WHERE task_key IN (?,?) AND status='completed' AND result_ref=? LIMIT 2",
                    (PUBLISH,REMOVE,ref_name(PUBLISH,q['sha256']))).fetchall()
                need(len(rs)==1)
                if rs[0]['task_key']==REMOVE:
                    _,t,inp=remove_graph(db,rs[0]['official_request_id'])
                    need(t['result_ref']==ref_name(PUBLISH,q['sha256']) and inp['propId']==q['propId'] and
                         q['revision']==inp['source']['revision']+1)
                    return
                run=db.execute('SELECT * FROM workflow_runs WHERE official_request_id=?',(rs[0]['official_request_id'],)).fetchone()
                p,rows=from_run(db,run)
                need(rows[PUBLISH]['result_ref']==ref_name(PUBLISH,q['sha256']) and p['propId']==q['propId'] and
                    q['revision']==(1 if run['request_type']==GENERATE else p['source']['revision']+1))
        except ClaimDenied:raise Denied('WORKFLOW_PROP_PACKAGE_DENIED') from None
