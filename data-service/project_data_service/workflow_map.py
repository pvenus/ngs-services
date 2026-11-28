"""Document-worker metadata publication via the existing PDS/Filex writer."""
import time
from .workflow_save_reservation import optionally_save
from .filex_read import canonical,digest
from .filex_writer_binding import writer as connect
from .assembly_contract import pin as prop_pin,image_scale
from .workflow_cinematic import WorkflowCinematic,CinematicConflict,need,keys,match,serve as serve_workflow
from .workflow_cinematic_sequence import exact_claim,strict_json,OFFICIAL,SAFE_INT

PATH='/v1/maps/ownership/workflow-map'
WRITER='/v1/workflow/maps'
CAP='workflow.map-metadata.v1'
ROUTES=(PATH,PATH+'/read',PATH+'/image',PATH+'/result')
V2='ngs.map-generate.v2'
V3='ngs.map-generate.v3'
DECOR_STAGE='map_decorations'
VISIBILITY=('both','pixel','cinematic')
STAGES=('map_start','map_portals','map_collisions')
TYPES={'map.generate':('ngs.map-generate.v1',V2,V3),'map.update':('ngs.map-update.v1',)}
BG=r'background\.[a-f0-9]{64}'

def text(v,n):return type(v)is str and 0<len(v)<=n and v==v.strip() and not any(ord(c)<32 and c not in '\n\r\t' or ord(c)==127 for c in v)
def notes(v):return v=='' or text(v,1000) and len(v.encode('utf-16-le'))<=2000
def point(v):
    return keys(v,('x','y')) and all(type(x)is int and 0<=x<=1000 for x in v.values())
def valid_perspective(v):
    if v is None:return True
    if not keys(v,('near','far')):return False
    if not all(keys(a,('point','actorHeight','viewWidth')) and point(a['point']) and
               type(a['actorHeight']) is int and 10<=a['actorHeight']<=650 and
               type(a['viewWidth']) is int and 200<=a['viewWidth']<=1000 for a in v.values()):return False
    return sum((v['near']['point'][k]-v['far']['point'][k])**2 for k in ('x','y'))>=100
def preserve_perspective(prior,current):
    previous={r['id']:r for r in prior['regions']}
    def keep(r):
        old=previous.get(r['id'],{})
        if 'perspective' not in r and 'perspective' in old and old.get('imageSha256')==r['imageSha256'] and old.get('backgroundId')==r['backgroundId']:
            return {**r,'perspective':old['perspective']}
        return r
    return {**current,'regions':[keep(r) for r in current['regions']]}

def rect(v):
    return (keys(v,('x','y','width','height')) and all(type(x)is int for x in v.values()) and
            0<=v['x'] and 0<=v['y'] and 0<v['width']<=1000-v['x'] and 0<v['height']<=1000-v['y'])
def validate_decorations(v,regions):
    need(type(v)is list and len(v)<=48)
    found=set()
    fields={'id','regionId','prop','anchor','scale','visibility'}
    for d in v:
        need(type(d)is dict and fields<=set(d)<=fields|{'rotation','flipX','flipY','zOrder','imageScalePercent'} and
             ('rotation' not in d or type(d['rotation'])is int and 0<=d['rotation']<360) and
             ('imageScalePercent' not in d or image_scale(d['imageScalePercent'])) and
             ('zOrder' not in d or type(d['zOrder'])is int and -1000<=d['zOrder']<=1000) and
             all(k not in d or type(d[k])is bool for k in ('flipX','flipY')) and
             match(d['id'],r'[a-z][a-z0-9-]{0,31}') and d['id'] not in found and
             type(d['regionId'])is str and d['regionId'] in regions and prop_pin(d['prop']) and
             point(d['anchor']) and type(d['scale'])is int and 10<=d['scale']<=300 and d['visibility'] in VISIBILITY)
        found.add(d['id'])
    return v

def decoration_design(v):
    need(keys(v,('mode','notes','candidates')) and v['mode'] in ('none','suggest') and notes(v['notes']) and
         type(v['candidates'])is list and (not v['candidates'] if v['mode']=='none' else 1<=len(v['candidates'])<=12))
    found=set()
    for row in v['candidates']:
        need(keys(row,('prop','visibility')) and prop_pin(row['prop']) and row['visibility'] in VISIBILITY and
             row['prop']['propId'] not in found)
        found.add(row['prop']['propId'])

def graph_valid(v,partial=False):
    fields=('title','startRegionId','regions','connections')
    if type(v)is dict and 'startPoint' in v:
        need(not partial and point(v['startPoint']))
        fields+=('startPoint',)
    if type(v)is dict and 'decorations' in v:
        need(not partial)
        fields+=('decorations',)
    need((keys(v,fields) or keys(v,(*fields,'colliders'))) and text(v['title'],100))
    regions=v['regions']; edges=v['connections']
    need(type(regions)is list and 1<=len(regions)<=24 and type(edges)is list and len(edges)<=96)
    bg=set()
    for r in regions:
        region_fields=('id','name','description','backgroundId','imageSha256')
        need((keys(r,region_fields) or not partial and keys(r,(*region_fields,'perspective')) and valid_perspective(r['perspective'])) and match(r['id'],r'[a-z][a-z0-9-]{0,31}') and
             r['id'] not in bg and text(r['name'],100) and text(r['description'],1000) and
             match(r['backgroundId'],BG) and match(r['imageSha256'],r'[a-f0-9]{64}'))
        bg.add(r['id'])
    if 'decorations' in v:validate_decorations(v['decorations'],bg)
    need(type(v['startRegionId'])is str and v['startRegionId'] in bg)
    chosen=set()
    for e in edges:
        need(keys(e,('id','from','to','hit','spawn','label')) and match(e['id'],r'[a-z][a-z0-9-]{0,31}') and e['id'] not in chosen and
             type(e['from'])is str and type(e['to'])is str and e['from'] in bg and e['to'] in bg and e['from']!=e['to'] and text(e['label'],100) and
             (rect(e['hit']) or partial and e['hit'] is None) and (point(e['spawn']) or partial and e['spawn'] is None))
        chosen.add(e['id'])
    colliders=v.get('colliders',[])
    need(type(colliders)is list and len(colliders)<=96)
    collider_ids=set()
    for c in colliders:
        fields=('id','regionId','rect')
        geometry=keys(c,fields) or (keys(c,(*fields,'shape','rotation')) and
                    c['shape'] in ('rect','ellipse') and type(c['rotation']) is int and 0<=c['rotation']<360)
        need(geometry and match(c['id'],r'[a-z][a-z0-9-]{0,31}') and
             c['id'] not in collider_ids and type(c['regionId'])is str and c['regionId'] in bg and rect(c['rect']))
        collider_ids.add(c['id'])
    return v
def graph(db,oid,task_id=None):
    need(match(oid,OFFICIAL))
    r=db.execute('SELECT * FROM workflow_runs WHERE official_request_id=?',(oid,)).fetchone()
    need(r is not None and r['request_type'] in TYPES and r['schema_version'] in TYPES[r['request_type']] and
         r['project_id']=='binari' and r['state']=='planned' and r['reason'] in (None,'RS_COMPLETED'))
    p=strict_json(r['payload'].encode()); gen=r['request_type']=='map.generate'
    v3=r['schema_version']==V3
    staged=r['schema_version'] in (V2,V3)
    need(keys(p,('schemaVersion','projectId','graph',*(('intent',) if gen else ('mapId','expectedRevision')),
                 *(('design',) if staged else ()),*(('decorationDesign',) if v3 else ()))) and
         p['schemaVersion']==r['schema_version'] and p['projectId']=='binari' and digest(p)==r['payload_sha256'])
    graph_valid(p['graph'],gen)
    need((2<=len(p['graph']['regions'])<=12 and p['graph']['connections'] and not p['graph'].get('colliders') and
          all(e['hit'] is None and e['spawn'] is None for e in p['graph']['connections']) and text(p['intent'],2000)) if gen else match(p['mapId'],OFFICIAL) and
         type(p['expectedRevision'])is int and 1<=p['expectedRevision']<SAFE_INT)
    if staged:
        design=p['design']
        need(keys(design,('startNotes','portalNotes','collisionNotes','collisionMode')) and
             design['collisionMode'] in ('suggest','none') and
             all(notes(design[k]) for k in ('startNotes','portalNotes','collisionNotes')))
    if v3:decoration_design(p['decorationDesign'])
    has_decor=v3 and p['decorationDesign']['mode']=='suggest'
    stages=(*STAGES,DECOR_STAGE) if has_decor else STAGES if staged else ('map_metadata',)
    ids=['workflow.task.'+digest([oid,key]) for key in stages]
    rows=db.execute('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 5',(oid,)).fetchall()
    by_id={t['task_id']:t for t in rows}
    need(len(rows)==len(ids) and set(by_id)==set(ids) and (task_id is None or task_id in by_id))
    idx=ids.index(task_id) if task_id else len(ids)-1
    prior={}
    for i,key in enumerate(stages):
        t=by_id[ids[i]]
        need(t['task_key']==key and t['task_type']==r['request_type'] and t['worker_type']=='document' and
             t['depends_on_task_id']==(ids[i-1] if i else None) and
             all(t[col] is None for col in t.keys() if col.startswith('depends_on_task_id_')) and
             strict_json(t['task_input'].encode())==p)
        if i<idx:
            need(t['status']=='completed' and type(t['map_part_json']) is str)
            raw=t['map_part_json'];v=strict_json(raw.encode())
            need(canonical(v).decode()==raw and t['result_ref']=='workflow.map-stage.'+digest(v))
            if key=='map_start':
                need(keys(v,('startRegionId','startPoint')) and v['startRegionId']==p['graph']['startRegionId'] and point(v['startPoint']))
            if key=='map_portals':match_coordinates(p['graph'],v)
            if key=='map_collisions':
                need(keys(v,('colliders',)) and (p['design']['collisionMode']!='none' or not v['colliders']))
                # Reuse graph validation with the already proven portal/start output.
                mapped=match_coordinates(p['graph'],{'coordinates':prior['coordinates']})
                graph_valid({**p['graph'],'startPoint':prior['startPoint'],'colliders':v['colliders'],
                    'connections':[{**e,'hit':mapped[e['id']]['hit'],'spawn':mapped[e['id']]['spawn']} for e in p['graph']['connections']]})
            prior.update(v)
    return r,by_id[ids[idx]],(p,stages[idx],prior)
def match_coordinates(source,value):
    need(keys(value,('coordinates',)) and type(value['coordinates']) is list and len(value['coordinates'])==len(source['connections']))
    points={}
    for e in value['coordinates']:
        need(keys(e,('id','hit','spawn')) and type(e['id']) is str and e['id'] not in points and rect(e['hit']) and point(e['spawn']))
        points[e['id']]=e
    need(set(points)=={e['id'] for e in source['connections']})
    return points
def graph_matches(basis,value,prior):
    graph_valid(value)
    source=basis['graph']
    if 'intent' not in basis:
        need(source==value)
        return
    need(len(source['connections'])==len(value['connections']))
    masked={**value,'connections':[{**e,'hit':None,'spawn':None} for e in value['connections']]}
    if basis['schemaVersion'] not in (V2,V3):
        need(masked==source)
        return
    if basis['schemaVersion']==V3:
        design=basis['decorationDesign'];d=value.get('decorations')
        if design['mode']=='none':need(d==[])
        else:
            possible={c['prop']['propId']:c for c in design['candidates']}
            need(type(d)is list and len(d)>0 and value.get('colliders')==prior['colliders'] and
                 all(possible.get(x['prop']['propId'])=={'prop':x['prop'],'visibility':x['visibility']} for x in d))
    else:need('decorations' not in value)
    decorations={'colliders','startPoint','decorations'}
    need({k:v for k,v in masked.items() if k not in decorations}==
         {k:v for k,v in source.items() if k not in decorations} and
         'colliders' in value and (basis['design']['collisionMode']!='none' or not value['colliders']) and
         value['startRegionId']==prior['startRegionId'] and value.get('startPoint')==prior['startPoint'])
    points=match_coordinates(source,{'coordinates':prior['coordinates']})
    need(all({k:e[k] for k in ('id','hit','spawn')}==points[e['id']] for e in value['connections']))
def identity(oid,p):return p.get('mapId',oid),p.get('expectedRevision',0)+1
def ref(mid,rev):return f'binari/cinematics/maps/{mid}/{rev}.json'

def changed_decoration_pins(previous,current):
    def binding(row):return {k:row[k] for k in ('prop','visibility','imageScalePercent') if k in row}
    old={d['id']:binding(d) for d in previous.get('decorations',())}
    unique={(tuple(sorted(row['prop'].items())),row['visibility'],row.get('imageScalePercent')):binding(row) for row in current.get('decorations',())
            if old.get(row['id'])!=binding(row)}
    return list(unique.values())

class WorkflowMap(WorkflowCinematic):
    def __init__(self,planner,*,writer_factory=None,prop_factory=None):
        super().__init__(planner,writer_factory=writer_factory)
        self.prop_factory=prop_factory or (lambda:connect('props'))
    def verify_props(self,pins):
        if not pins:return
        with self.prop_factory() as tx:
            out=tx.call('POST','/v1/workflow/props/assembly-bindings',{'decorationPins':pins},missing=True)
        if out is None:raise CinematicConflict('MAP_DECORATION_PROP_UNAVAILABLE')
        need(out=={'ok':True})
    def ready(self):
        with self.planner.snapshot():pass
        with self.writer_factory() as tx:return tx.capabilities().get('workflowMapMetadataV1') is True
    def call_map(self,path,p):
        with self.writer_factory() as tx:
            try:return tx.call('POST',WRITER+path,p)
            except ValueError as e:
                if str(e)=='KNOWLEDGE_SOURCE_CHANGED':raise CinematicConflict('MAP_REVISION_CONFLICT') from None
                raise
    def operate(self,op,p):
        if op=='result':
            need(keys(p,('officialRequestId',)))
            oid=p['officialRequestId']
            with self.planner.snapshot() as db:
                _,t,(b,_,prior)=graph(db,oid)
                mid,rev=identity(oid,b)
                need(t['status']=='completed' and t['result_ref']==ref(mid,rev))
            result=self.call_map('/revision',{'mapId':mid,'revision':rev})
            need(result.get('officialRequestId')==oid)
            graph_matches(b,result['graph'],prior)
            return {'ok':True,'resultRef':ref(mid,rev)}
        need(op in ('read','publish','image') and keys(p,('claim',) if op=='read' else ('claim','imageSha256') if op=='image' else ('claim','graph')))
        with exact_claim(self.planner,p['claim'],lambda db,oid:graph(db,oid,p['claim']['taskId'])) as (oid,(b,stage,prior)), optionally_save(self.planner,p['claim'],op=='publish'):
            if op=='image':
                need('intent' in b and match(p['imageSha256'],r'[a-f0-9]{64}') and
                     p['imageSha256'] in {r['imageSha256'] for r in b['graph']['regions']})
                with self.writer_factory() as tx:
                    return tx.call('POST','/v1/workflow/cinematics-background/asset',{'sourceSha256':p['imageSha256']})
            ids=list(dict.fromkeys(r['backgroundId'] for r in b['graph']['regions'] if r['backgroundId'] is not None))
            refs=self.call_map('/context',{'backgroundIds':ids}) if ids else {'backgrounds':[]}
            by_id={r['id']:r['sha256'] for r in refs['backgrounds']}
            need(all(by_id.get(r['backgroundId'])==r['imageSha256'] for r in b['graph']['regions']))
            suggest=b.get('decorationDesign',{}).get('mode')=='suggest'
            if op=='read':
                if stage==DECOR_STAGE:self.verify_props(b['decorationDesign']['candidates'])
                return refs
            need(stage==DECOR_STAGE if suggest else stage in ('map_metadata','map_collisions'))
            graph_matches(b,p['graph'],prior)
            mid,rev=identity(oid,b)
            if 'intent' not in b:
                previous=self.call_map('/revision',{'mapId':mid,'revision':rev-1})['graph']
                if 'decorations' in previous and 'decorations' not in p['graph']:
                    raise CinematicConflict('MAP_DECORATIONS_REQUIRED_FOR_UPDATE')
                if preserve_perspective(previous,p['graph'])!=p['graph']:
                    raise CinematicConflict('MAP_PERSPECTIVE_REQUIRED_FOR_UPDATE')
                self.verify_props(changed_decoration_pins(previous,p['graph']))
            else:self.verify_props(changed_decoration_pins({},p['graph']))
            result=self.call_map('',{'schemaVersion':'ngs.map-metadata.v1','projectId':'binari','mapId':mid,
                   'revision':rev,'updatedAt':int(time.time()*1000),'officialRequestId':oid,'graph':p['graph']})
            need(result.get('mapId')==mid and result.get('revision')==rev and result.get('officialRequestId')==oid and result.get('graph')==p['graph'])
            return {'ok':True,'resultRef':ref(mid,rev)}

def serve(handler):
    return serve_workflow(handler,attribute='workflow_map',path=PATH,capability=CAP,body_limits={PATH:65536})
