"""One official full JSON metadata replacement, through typed storage adapters."""
import time
from .workflow_save_reservation import save_handoff
from .filex_read import canonical,digest
from .workflow_cinematic import WorkflowCinematic,CinematicConflict,need,keys,match,serve as serve_workflow
from .workflow_cinematic_sequence import exact_claim,strict_json,OFFICIAL,SAFE_INT
from .filex_writer_binding import writer as connect
from .assembly_contract import content as assembly_content,MAX_REV as ASSEMBLY_MAX_REV,SPIRIT_TYPE,playable,affected_conditions_exist,definitions_preserved,state as assembly_state
from .workflow_assembly import WRITER as ASSEMBLY_WRITER,ref as assembly_ref
from .workflow_map import WRITER as MAP_WRITER,graph_valid, preserve_perspective,changed_decoration_pins,ref as map_ref

PATH='/v1/storage/workflow-file-update'
CAP='workflow.file-update.v1'
ROUTES=(PATH,PATH+'/result')
TYPE='file.update'
SCHEMA='ngs.file-update.v1'
KEY='file_update'
MAP_ROOT='canonical-files/binari/cinematics/maps'
ASSEMBLY_ROOT='canonical-files/binari/cinematics/assemblies'

def valid_pin(p):
    return keys(p,('revision','officialRequestId')) and type(p['revision']) is int and 1<=p['revision']<SAFE_INT and match(p['officialRequestId'],OFFICIAL)

class MapMetadata:
    def accepts_id(self,mid):
        return match(mid,OFFICIAL)
    def validate(self,content):
        return graph_valid(content)
    def result_ref(self,mid,rev):
        return map_ref(mid,rev)
    def read(self,provider,mid,rev):
        return provider.storage(MAP_WRITER+'/revision',{'mapId':mid,'revision':rev})
    def logical_content(self,prior,content):
        result=preserve_perspective(prior,content)
        if 'decorations' in prior and 'decorations' not in result:result['decorations']=prior['decorations']
        return graph_valid(result)
    def write(self,provider,oid,p):
        mid=p['target']['path'];expected=p['expected'];rev=expected['revision']+1
        prior=provider.storage(MAP_WRITER+'/revision',{'mapId':mid,'revision':rev-1},missing=True)
        if prior is None or prior['officialRequestId']!=expected['officialRequestId']:
            raise CinematicConflict('FILE_UPDATE_REVISION_CONFLICT')
        content=self.logical_content(prior['graph'],p['content'])
        replay=provider.storage(MAP_WRITER+'/revision',{'mapId':mid,'revision':rev},missing=True)
        if replay is not None:
            if not self.matches(replay,oid,mid,rev,content):raise CinematicConflict('FILE_UPDATE_REVISION_CONFLICT')
            return replay
        ids=list(dict.fromkeys(r['backgroundId'] for r in content['regions']))
        refs=provider.storage(MAP_WRITER+'/context',{'backgroundIds':ids})
        by_id={r['id']:r['sha256'] for r in refs['backgrounds']}
        need(all(by_id.get(r['backgroundId'])==r['imageSha256'] for r in content['regions']))
        provider.verify_prop_pins(changed_decoration_pins(prior['graph'],content),
                                 unavailable='FILE_UPDATE_MAP_DECORATION_PROP_UNAVAILABLE',decorations=True)
        result={'schemaVersion':'ngs.map-metadata.v1','projectId':'binari','mapId':mid,'revision':rev,
                'updatedAt':int(time.time()*1000),'officialRequestId':oid,'graph':content}
        return provider.storage(MAP_WRITER+'/metadata-update',{'expected':expected,'state':result})
    def matches(self,result,oid,mid,rev,content,*,provider=None):
        if provider is not None:
            prior=self.read(provider,mid,rev-1)
            content=self.logical_content(prior['graph'],content)
        return (type(result) is dict and result.get('mapId')==mid and result.get('revision')==rev and
                result.get('officialRequestId')==oid and result.get('graph')==content)

class AssemblyMetadata:
    def accepts_id(self,aid):
        return match(aid,OFFICIAL)
    def validate(self,content):
        need(assembly_content(content))
    def result_ref(self,aid,rev):
        return assembly_ref(aid,rev)
    def read(self,provider,aid,rev):
        return provider.storage(ASSEMBLY_WRITER+'/revision',{'assemblyId':aid,'revision':rev})
    def matches(self,result,oid,aid,rev,content,*,provider=None):
        return (assembly_state(result) and result['assemblyId']==aid and result['revision']==rev and
                result['officialRequestId']==oid and result['title']==content['title'] and
                result['map']['mapId']==content['mapId'] and result['puzzles']==content['puzzles'])
    def write(self,provider,oid,p):
        aid=p['target']['path'];expected=p['expected'];content=p['content'];rev=expected['revision']+1
        prior=provider.storage(ASSEMBLY_WRITER+'/revision',{'assemblyId':aid,'revision':expected['revision']},missing=True)
        if prior is None or prior['officialRequestId']!=expected['officialRequestId']:
            raise CinematicConflict('FILE_UPDATE_REVISION_CONFLICT')
        if content['title']!=prior['title'] or content['mapId']!=prior['map']['mapId']:
            raise CinematicConflict('FILE_UPDATE_ASSEMBLY_IDENTITY_IMMUTABLE')
        replay=provider.storage(ASSEMBLY_WRITER+'/revision',{'assemblyId':aid,'revision':rev},missing=True)
        if replay is not None:
            if not self.matches(replay,oid,aid,rev,content) or replay['map']!=prior['map']:
                raise CinematicConflict('FILE_UPDATE_REVISION_CONFLICT')
            return replay
        rows=content['puzzles'];mid=content['mapId']
        if not definitions_preserved(prior['puzzles'],rows):
            raise CinematicConflict('FILE_UPDATE_ASSEMBLY_DEFINITION_IMMUTABLE')
        graph=provider.storage(ASSEMBLY_WRITER+'/map',{'mapId':mid})['graph']
        if not affected_conditions_exist(prior['puzzles'],rows,graph):
            raise CinematicConflict('FILE_UPDATE_ASSEMBLY_PORTAL_MISSING')
        if not playable(graph,rows):raise CinematicConflict('FILE_UPDATE_ASSEMBLY_ROUTE_NOT_PLAYABLE')
        unique={tuple(sorted(pin.items())):pin for x in rows if x['type']!=SPIRIT_TYPE for pin in x['bindings'].values()}
        provider.verify_prop_pins(list(unique.values()))
        prior_spirit={x['id']:x['bindings']['spirit'] for x in prior['puzzles'] if x['type']==SPIRIT_TYPE}
        changed={tuple(sorted(x['bindings']['spirit'].items())):x['bindings']['spirit'] for x in rows
                 if x['type']==SPIRIT_TYPE and prior_spirit.get(x['id'])!=x['bindings']['spirit']}
        provider.verify_prop_pins([{'prop':pin,'visibility':'both'} for pin in changed.values()],
                                 unavailable='FILE_UPDATE_ASSEMBLY_SPIRIT_PROP_UNAVAILABLE',decorations=True)
        next_state={**prior,'revision':rev,'updatedAt':int(time.time()*1000),'officialRequestId':oid,'puzzles':rows}
        return provider.storage(ASSEMBLY_WRITER+'/metadata-update',{'expected':expected,'state':next_state})

ADAPTERS={MAP_ROOT:MapMetadata(),ASSEMBLY_ROOT:AssemblyMetadata()}

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
         target['kind']=='metadata' and target['path']!=oid and valid_pin(p['expected']))
    adapter=ADAPTERS[target['root']]
    need(adapter.accepts_id(target['path']) and
         (target['root']!=ASSEMBLY_ROOT or p['expected']['revision']<ASSEMBLY_MAX_REV))
    adapter.validate(p['content'])
    tasks=db.execute('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 2',(oid,)).fetchall()
    need(len(tasks)==1);task=tasks[0]
    need(task['task_id']=='workflow.task.'+digest([oid,KEY]) and task['task_key']==KEY and
         task['task_type']==TYPE and task['worker_type']=='file' and
         all(task[col] is None for col in task.keys() if col.startswith('depends_on_task_id')) and
         task['task_input'].encode()==canonical({k:p[k] for k in ('target','expected','content')}))
    return run,task,(p,adapter)

class WorkflowFileUpdate(WorkflowCinematic):
    def __init__(self,planner,*,writer_factory=None,prop_factory=None):
        super().__init__(planner,writer_factory=writer_factory)
        self.prop_factory=prop_factory or (lambda:connect('props'))
    def verify_prop_pins(self,pins,*,unavailable='FILE_UPDATE_ASSEMBLY_PROP_UNAVAILABLE',decorations=False):
        if not pins:return
        with self.prop_factory() as tx:
            out=tx.call('POST','/v1/workflow/props/assembly-bindings',{'decorationPins' if decorations else 'pins':pins},missing=True)
        if out is None:raise CinematicConflict(unavailable)
        need(out=={'ok':True})
    def storage(self,path,data,*,missing=False):
        with self.writer_factory() as tx:
            try:return tx.call('POST',path,data,missing=missing) if missing else tx.call('POST',path,data)
            except ValueError as e:
                if str(e)=='KNOWLEDGE_SOURCE_CHANGED':raise CinematicConflict('FILE_UPDATE_REVISION_CONFLICT') from None
                raise
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
            with exact_claim(self.planner,packet['claim'],planned) as (oid,(p,adapter)), save_handoff(self.planner,packet['claim']):
                mid=p['target']['path'];rev=p['expected']['revision']+1
                result_ref=adapter.result_ref(mid,rev)
                result=adapter.write(self,oid,p)
        need(adapter.matches(result,oid,mid,rev,p['content'],provider=self))
        return {'ok':True,'resultRef':result_ref}

def serve(handler):
    return serve_workflow(handler,attribute='workflow_file_update',path=PATH,capability=CAP)
