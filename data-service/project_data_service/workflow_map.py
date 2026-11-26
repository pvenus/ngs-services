"""Document-worker metadata publication via the existing PDS/Filex writer."""
import time
from .filex_read import canonical,digest
from .workflow_cinematic import WorkflowCinematic,CinematicConflict,need,keys,match,serve as serve_workflow
from .workflow_cinematic_sequence import exact_claim,strict_json,OFFICIAL,SAFE_INT

PATH='/v1/maps/ownership/workflow-map'
WRITER='/v1/workflow/maps'
CAP='workflow.map-metadata.v1'
ROUTES=(PATH,PATH+'/read',PATH+'/image',PATH+'/result')
TYPES={'map.generate':'ngs.map-generate.v1','map.update':'ngs.map-update.v1'}
BG=r'background\.[a-f0-9]{64}'

def text(v,n):return type(v)is str and 0<len(v)<=n and v==v.strip() and not any(ord(c)<32 and c not in '\n\r\t' or ord(c)==127 for c in v)
def point(v):
    return keys(v,('x','y')) and all(type(x)is int and 0<=x<=1000 for x in v.values())
def rect(v):
    return (keys(v,('x','y','width','height')) and all(type(x)is int for x in v.values()) and
            0<=v['x'] and 0<=v['y'] and 0<v['width']<=1000-v['x'] and 0<v['height']<=1000-v['y'])
def graph_valid(v,partial=False):
    need(keys(v,('title','startRegionId','regions','connections')) and text(v['title'],100))
    regions=v['regions']; edges=v['connections']
    need(type(regions)is list and 1<=len(regions)<=24 and type(edges)is list and len(edges)<=96)
    bg=set()
    for r in regions:
        need(keys(r,('id','name','description','backgroundId','imageSha256')) and match(r['id'],r'[a-z][a-z0-9-]{0,31}') and
             r['id'] not in bg and text(r['name'],100) and text(r['description'],1000) and
             match(r['backgroundId'],BG) and match(r['imageSha256'],r'[a-f0-9]{64}'))
        bg.add(r['id'])
    need(type(v['startRegionId'])is str and v['startRegionId'] in bg)
    chosen=set()
    for e in edges:
        need(keys(e,('id','from','to','hit','spawn','label')) and match(e['id'],r'[a-z][a-z0-9-]{0,31}') and e['id'] not in chosen and
             type(e['from'])is str and type(e['to'])is str and e['from'] in bg and e['to'] in bg and e['from']!=e['to'] and text(e['label'],100) and
             (rect(e['hit']) or partial and e['hit'] is None) and (point(e['spawn']) or partial and e['spawn'] is None))
        chosen.add(e['id'])
    return v
def graph(db,oid):
    need(match(oid,OFFICIAL))
    r=db.execute('SELECT * FROM workflow_runs WHERE official_request_id=?',(oid,)).fetchone()
    need(r is not None and r['request_type'] in TYPES and r['schema_version']==TYPES[r['request_type']] and
         r['project_id']=='binari' and r['state']=='planned' and r['reason'] in (None,'RS_COMPLETED'))
    p=strict_json(r['payload'].encode()); gen=r['request_type']=='map.generate'
    need(keys(p,('schemaVersion','projectId','graph',*(('intent',) if gen else ('mapId','expectedRevision')))) and
         p['schemaVersion']==r['schema_version'] and p['projectId']=='binari' and digest(p)==r['payload_sha256'])
    graph_valid(p['graph'],gen)
    need((2<=len(p['graph']['regions'])<=12 and p['graph']['connections'] and
          all(e['hit'] is None and e['spawn'] is None for e in p['graph']['connections']) and text(p['intent'],2000)) if gen else match(p['mapId'],OFFICIAL) and
         type(p['expectedRevision'])is int and 1<=p['expectedRevision']<SAFE_INT)
    rows=db.execute('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 2',(oid,)).fetchall();need(len(rows)==1)
    t=rows[0]
    need(t['task_id']=='workflow.task.'+digest([oid,'map_metadata']) and t['task_key']=='map_metadata' and
         t['task_type']==r['request_type'] and t['worker_type']=='document' and t['depends_on_task_id'] is None and
         strict_json(t['task_input'].encode())==p)
    return r,t,p
def graph_matches(source,value,generate=False):
    graph_valid(value)
    if not generate:
        need(source==value)
        return
    need(len(source['connections'])==len(value['connections']))
    masked={**value,'connections':[{**e,'hit':None,'spawn':None} for e in value['connections']]}
    need(masked==source)
def identity(oid,p):return p.get('mapId',oid),p.get('expectedRevision',0)+1
def ref(mid,rev):return f'binari/cinematics/maps/{mid}/{rev}.json'

class WorkflowMap(WorkflowCinematic):
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
                _,t,b=graph(db,oid)
                mid,rev=identity(oid,b)
                need(t['status']=='completed' and t['result_ref']==ref(mid,rev))
            result=self.call_map('/revision',{'mapId':mid,'revision':rev})
            need(result.get('officialRequestId')==oid)
            graph_matches(b['graph'],result['graph'],'intent' in b)
            return {'ok':True,'resultRef':ref(mid,rev)}
        need(op in ('read','publish','image') and keys(p,('claim',) if op=='read' else ('claim','imageSha256') if op=='image' else ('claim','graph')))
        with exact_claim(self.planner,p['claim'],graph) as (oid,b):
            if op=='image':
                need('intent' in b and match(p['imageSha256'],r'[a-f0-9]{64}') and
                     p['imageSha256'] in {r['imageSha256'] for r in b['graph']['regions']})
                with self.writer_factory() as tx:
                    return tx.call('POST','/v1/workflow/cinematics-background/asset',{'sourceSha256':p['imageSha256']})
            ids=list(dict.fromkeys(r['backgroundId'] for r in b['graph']['regions'] if r['backgroundId'] is not None))
            refs=self.call_map('/context',{'backgroundIds':ids}) if ids else {'backgrounds':[]}
            by_id={r['id']:r['sha256'] for r in refs['backgrounds']}
            need(all(by_id.get(r['backgroundId'])==r['imageSha256'] for r in b['graph']['regions']))
            if op=='read':return refs
            graph_matches(b['graph'],p['graph'],'intent' in b)
            mid,rev=identity(oid,b)
            result=self.call_map('',{'schemaVersion':'ngs.map-metadata.v1','projectId':'binari','mapId':mid,
                   'revision':rev,'updatedAt':int(time.time()*1000),'officialRequestId':oid,'graph':p['graph']})
            need(result.get('mapId')==mid and result.get('revision')==rev and result.get('officialRequestId')==oid and result.get('graph')==p['graph'])
            return {'ok':True,'resultRef':ref(mid,rev)}

def serve(handler):
    return serve_workflow(handler,attribute='workflow_map',path=PATH,capability=CAP,body_limits={PATH:65536})
