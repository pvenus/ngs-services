"""Bind CLI-authored assembly geometry to authoritative Filex sources and official claims."""
from .filex_read import canonical,digest
from .filex_writer_binding import writer as connect
from .workflow_cinematic import WorkflowCinematic,CinematicConflict,need,serve as serve_workflow
from .workflow_cinematic_sequence import exact_claim,strict_json
from .assembly_contract import KINDS,V2,OID,keys,match,payload,state,graph_reaches,playable

PATH='/v1/assemblies/ownership/workflow-assembly'
CAP='workflow.assembly.v1'
ROUTES=(PATH,*(PATH+'/'+op for op in ('read','result','list')))
WRITER='/v1/workflow/assemblies'
TASK='assembly_metadata'

def graph(db,oid):
    need(match(oid,OID))
    r=db.execute('SELECT * FROM workflow_runs WHERE official_request_id=?',(oid,)).fetchone()
    need(r is not None and r['request_type'] in KINDS and r['schema_version'] in KINDS[r['request_type']] and
         r['state']=='planned' and r['reason'] in (None,'RS_COMPLETED') and r['project_id']=='binari')
    p=strict_json(r['payload'].encode())
    need(payload(p,r['request_type']) and canonical(p).decode()==r['payload'] and digest(p)==r['payload_sha256'])
    rows=db.execute('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 2',(oid,)).fetchall()
    need(len(rows)==1);t=rows[0]
    need(t['task_id']=='workflow.task.'+digest([oid,TASK]) and t['task_key']==TASK and t['task_type']==r['request_type'] and
         t['worker_type']=='document' and t['depends_on_task_id'] is None and
         all(t[col] is None for col in t.keys() if col.startswith('depends_on_task_id_')) and t['task_input']==r['payload'])
    return r,t,p

def identity(oid,p):return p.get('assemblyId',oid),p.get('expectedRevision',0)+1
def ref(aid,rev):return f'binari/cinematics/assemblies/{aid}/{rev}.json'

def default_puzzle(graph,bindings,puzzle):
    reach=graph_reaches(graph,graph['startRegionId'])
    altar='zone-2' if 'zone-2' in reach else graph['startRegionId']
    well='zone-4'
    if not (well in graph_reaches(graph,altar) and altar in graph_reaches(graph,well)):well=altar
    return {'id':puzzle['id'],'type':puzzle['type'],
            'layout':{'altar':{'regionId':altar,'x':45,'y':73},'well':{'regionId':well,'x':75,'y':68}},
            'attachments':{'bowl':{'x':5,'y':-10},'lid':{'x':5,'y':-10}},'bindings':bindings}

def expected_puzzles(p):
    if p['schemaVersion']==V2:return p['puzzles']
    return [{'id':'ritual-well-water','type':p['puzzleType']}]

class WorkflowAssembly(WorkflowCinematic):
    def __init__(self,planner,*,writer_factory=None,prop_factory=None):
        super().__init__(planner,writer_factory=writer_factory)
        self.prop_factory=prop_factory or (lambda:connect('props'))
    def ready(self):
        with self.planner.snapshot():pass
        with self.writer_factory() as cin,self.prop_factory() as prop:
            return cin.capabilities().get('workflowAssemblyV1') is True and prop.capabilities().get('workflowPropAssemblyBindingsV1') is True
    def cine(self,op,p,missing=False):
        with self.writer_factory() as tx:
            try:
                out=tx.call('POST',WRITER+('/'+op if op else ''),p,missing=True)
                if out is None and not missing:raise FileNotFoundError('assembly source missing')
                return out
            except ValueError as e:
                if str(e)=='KNOWLEDGE_SOURCE_CHANGED':raise CinematicConflict('ASSEMBLY_REVISION_CONFLICT') from None
                raise
    def prop(self,p):
        with self.prop_factory() as tx:
            out=tx.call('POST','/v1/workflow/props/assembly-bindings',p,missing=True)
            if out is None:raise CinematicConflict('ASSEMBLY_PROP_UNAVAILABLE')
            return out
    def check(self,oid,stamp,p,existing):
        aid,rev=identity(oid,p)
        need(state(existing) and existing['officialRequestId']==oid and existing['updatedAt']==stamp and
             existing['assemblyId']==aid and existing['revision']==rev)
        if 'assemblyId' in p:need(existing['puzzles']==p['puzzles'])
        else:
            need(existing['title']==p['title'] and existing['map']['mapId']==p['mapId'] and
                 [(x['id'],x['type']) for x in existing['puzzles']]==[(x['id'],x['type']) for x in expected_puzzles(p)])
        return existing
    def candidate(self,oid,stamp,p):
        aid,rev=identity(oid,p)
        existing=self.cine('revision',{'assemblyId':aid,'revision':rev},missing=True)
        if existing is not None:
            if existing['officialRequestId']!=oid:raise CinematicConflict('ASSEMBLY_REVISION_CONFLICT')
            context=self.model_context(self.cine('map',existing['map'])) if 'assemblyId' not in p else None
            need(existing['updatedAt']<=stamp)
            return self.check(oid,existing['updatedAt'],p,existing),context,True
        if 'assemblyId' in p:
            prior=self.cine('head',{'assemblyId':aid},missing=True)
            if prior is None or prior['revision']!=p['expectedRevision']:raise CinematicConflict('ASSEMBLY_REVISION_CONFLICT')
            title=prior['title'];mapping=prior['map'];rows=p['puzzles']
            # The stored revision is publication provenance, not the live map dependency.
            m=self.cine('map',{'mapId':mapping['mapId']})
            if not playable(m['graph'],rows):raise CinematicConflict('ASSEMBLY_ROUTE_NOT_PLAYABLE')
            unique={tuple(sorted(v.items())):v for x in rows for v in x['bindings'].values()}
            need(self.prop({'pins':list(unique.values())})=={'ok':True})
        else:
            m=self.cine('map',{'mapId':p['mapId']});title=p['title']
            mapping={k:m[k] for k in ('mapId','revision','officialRequestId')}
            out=self.prop({});need(keys(out,('bindings',)))
            rows=[default_puzzle(m['graph'],out['bindings'],x) for x in expected_puzzles(p)]
            need(playable(m['graph'],rows))
        result=self.check(oid,stamp,p,{'schemaVersion':'ngs.assembly.v1','projectId':'binari','assemblyId':aid,
            'revision':rev,'updatedAt':stamp,'officialRequestId':oid,'title':title,'map':mapping,'puzzles':rows})
        return result,self.model_context(m) if 'assemblyId' not in p else None,False
    @staticmethod
    def model_context(m):
        return {k:m['graph'][k] for k in ('regions','connections','startRegionId')}
    def operate(self,op,p):
        if op=='list':
            need(keys(p,()))
            return self.cine('list',{})
        if op=='result':
            need(keys(p,('officialRequestId',)))
            oid=p['officialRequestId']
            with self.planner.snapshot() as db:
                _,t,b=graph(db,oid);aid,rev=identity(oid,b)
                need(t['status']=='completed' and t['result_ref']==ref(aid,rev))
                existing=self.cine('revision',{'assemblyId':aid,'revision':rev})
                self.check(oid,existing['updatedAt'],b,existing)
            return {'ok':True,'resultRef':ref(aid,rev)}
        need(op in ('read','publish') and keys(p,('claim',) if op=='read' else ('claim','state')))
        with exact_claim(self.planner,p['claim'],graph) as (oid,b):
            expected,context,replay=self.candidate(oid,p['claim']['claimStamp'],b)
            if op=='read':return {'state':expected,**({'context':context} if context is not None else {})}
            submitted=p['state']
            if not state(submitted):raise CinematicConflict('ASSEMBLY_INPUT_CHANGED')
            if replay or context is None:
                if submitted!=expected:raise CinematicConflict('ASSEMBLY_INPUT_CHANGED')
            else:
                immutable=lambda value:{**value,'puzzles':[
                    {k:v for k,v in puzzle.items() if k not in ('layout','attachments')} for puzzle in value['puzzles']]}
                if immutable(submitted)!=immutable(expected):raise CinematicConflict('ASSEMBLY_INPUT_CHANGED')
                if not playable(context,submitted['puzzles']):raise CinematicConflict('ASSEMBLY_ROUTE_NOT_PLAYABLE')
            need(self.cine('',submitted)==submitted)
            return {'ok':True,'resultRef':ref(submitted['assemblyId'],submitted['revision'])}

def serve(handler):
    return serve_workflow(handler,attribute='workflow_assembly',path=PATH,capability=CAP,body_limits={PATH:65536})
