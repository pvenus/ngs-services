"""One official full JSON metadata replacement, through typed storage adapters."""
import time
from .filex_read import canonical,digest
from .workflow_cinematic import CinematicConflict,need,keys,match,serve as serve_workflow
from .workflow_cinematic_sequence import exact_claim,strict_json,OFFICIAL,SAFE_INT
from .workflow_map import WorkflowMap,graph_valid,ref as map_ref

PATH='/v1/storage/workflow-file-update'
CAP='workflow.file-update.v1'
ROUTES=(PATH,PATH+'/result')
TYPE='file.update'
SCHEMA='ngs.file-update.v1'
KEY='file_update'
MAP_ROOT='canonical-files/binari/cinematics/maps'

def valid_pin(p):
    return keys(p,('revision','officialRequestId')) and type(p['revision']) is int and 1<=p['revision']<SAFE_INT and match(p['officialRequestId'],OFFICIAL)

class MapMetadata:
    def validate(self,content):
        return graph_valid(content)
    def result_ref(self,mid,rev):
        return map_ref(mid,rev)
    def read(self,provider,mid,rev):
        return provider.call_map('/revision',{'mapId':mid,'revision':rev})
    def write(self,provider,oid,p):
        content=p['content'];mid=p['target']['path'];expected=p['expected']
        ids=list(dict.fromkeys(r['backgroundId'] for r in content['regions']))
        refs=provider.call_map('/context',{'backgroundIds':ids})
        by_id={r['id']:r['sha256'] for r in refs['backgrounds']}
        need(all(by_id.get(r['backgroundId'])==r['imageSha256'] for r in content['regions']))
        result={'schemaVersion':'ngs.map-metadata.v1','projectId':'binari','mapId':mid,'revision':expected['revision']+1,
                'updatedAt':int(time.time()*1000),'officialRequestId':oid,'graph':content}
        return provider.call_map('/metadata-update',{'expected':expected,'state':result})

ADAPTERS={MAP_ROOT:MapMetadata()}

def planned(db,oid):
    need(match(oid,OFFICIAL))
    run=db.execute('SELECT * FROM workflow_runs WHERE official_request_id=?',(oid,)).fetchone()
    need(run is not None and run['request_type']==TYPE and run['schema_version']==SCHEMA and
         run['project_id']=='binari' and run['state']=='planned' and run['reason'] in (None,'RS_COMPLETED'))
    raw=run['payload'].encode()
    p=strict_json(raw)
    need(len(raw)<=60*1024 and keys(p,('schemaVersion','projectId','target','expected','content')) and
         p['schemaVersion']==SCHEMA and p['projectId']=='binari' and canonical(p)==raw and digest(p)==run['payload_sha256'])
    target=p['target']
    need(keys(target,('root','path','kind')) and type(target['root']) is str and target['root'] in ADAPTERS and
         target['kind']=='metadata' and match(target['path'],OFFICIAL) and target['path']!=oid and valid_pin(p['expected']))
    adapter=ADAPTERS[target['root']]
    adapter.validate(p['content'])
    tasks=db.execute('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 2',(oid,)).fetchall()
    need(len(tasks)==1);task=tasks[0]
    need(task['task_id']=='workflow.task.'+digest([oid,KEY]) and task['task_key']==KEY and
         task['task_type']==TYPE and task['worker_type']=='file' and
         all(task[col] is None for col in task.keys() if col.startswith('depends_on_task_id')) and
         task['task_input'].encode()==canonical({k:p[k] for k in ('target','expected','content')}))
    return run,task,(p,adapter)

class WorkflowFileUpdate(WorkflowMap):
    def ready(self):
        with self.planner.snapshot():pass
        with self.writer_factory() as tx:return tx.capabilities().get('workflowFileUpdateV1') is True
    def operate(self,op,packet):
        if op=='result':
            need(keys(packet,('officialRequestId',)))
            oid=packet['officialRequestId']
            with self.planner.snapshot() as db:
                _,task,(p,adapter)=planned(db,oid)
                mid=p['target']['path'];rev=p['expected']['revision']+1
                result_ref=adapter.result_ref(mid,rev)
                need(task['status']=='completed' and task['result_ref']==result_ref)
            result=adapter.read(self,mid,rev)
        else:
            need(op=='publish' and keys(packet,('claim',)))
            with exact_claim(self.planner,packet['claim'],planned) as (oid,(p,adapter)):
                mid=p['target']['path'];rev=p['expected']['revision']+1
                result_ref=adapter.result_ref(mid,rev)
                try:result=adapter.write(self,oid,p)
                except CinematicConflict as e:
                    if str(e)=='MAP_REVISION_CONFLICT':raise CinematicConflict('FILE_UPDATE_REVISION_CONFLICT') from None
                    raise
        need(result.get('mapId')==mid and result.get('revision')==rev and result.get('officialRequestId')==oid and result.get('graph')==p['content'])
        return {'ok':True,'resultRef':result_ref}

def serve(handler):
    return serve_workflow(handler,attribute='workflow_file_update',path=PATH,capability=CAP)
