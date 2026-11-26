"""Scoped source-image publication and non-destructive sequence removal."""
from hashlib import sha256
from .filex_read import digest
from .workflow_cinematic import (WorkflowCinematic, BAD_TEXT, JS_ENDS, HASH, OFFICIAL, need, keys, match,
                                 serve as serve_cinematic)
from .workflow_cinematic_sequence import exact_claim, strict_json, sequence_graph, ref as sequence_ref

ORIGIN='/v1/cinematics/ownership/workflow-'
WRITER='/v1/workflow/cinematics-'
SOURCE='source'
BACKGROUND='background'
SOURCE_MODES=(SOURCE,BACKGROUND)
ARCHIVE='sequence-remove'
CONFIG={
 SOURCE:{'type':'cinematic.source.generate','schema':'ngs.cinematic-source-generate.v1',
         'key':'cinematic_source_generate','worker':'image','input':('title','prompt'),
         'capability':'workflow.cinematic-source-write.v1','filex':'workflowCinematicSourceV1'},
 BACKGROUND:{'type':'background.source.generate','schema':'ngs.background-source-generate.v1',
             'key':'background_source_generate','worker':'image','input':('title','timeOfDay','season','mood','color','location','requirements','additionalNotes'),
             'capability':'workflow.background-source-write.v1','filex':'workflowBackgroundSourceV1'},
 ARCHIVE:{'type':'cinematic.sequences.remove','schema':'ngs.cinematic-sequences-remove.v1',
          'key':'cinematic_sequences_remove','worker':'file','input':('sequenceIds',),
          'capability':'workflow.cinematic-sequence-remove.v1','filex':'workflowCinematicSequenceRemoveV1'},
}
RESULT_ARCHIVE='binari/cinematics/sequence-archive.json'
ROUTES=(ORIGIN+SOURCE,ORIGIN+SOURCE+'/read',ORIGIN+SOURCE+'/result',ORIGIN+SOURCE+'/reconcile',
        *(ORIGIN+BACKGROUND+suffix for suffix in ('','/read','/result','/reconcile')),
        ORIGIN+ARCHIVE,ORIGIN+ARCHIVE+'/result')


def clean(v,chars,bytes_max):
    return (type(v)is str and 1<=len(v)<=chars and len(v.encode('utf-8'))<=bytes_max and
            v.strip(JS_ENDS)==v and BAD_TEXT.search(v) is None)


def validate_input(mode,v):
    need(keys(v,CONFIG[mode]['input']))
    if mode==SOURCE:need(clean(v['title'],120,480) and clean(v['prompt'],2000,4000))
    elif mode==BACKGROUND:
        limits={'title':120,'timeOfDay':80,'season':80,'mood':160,'color':120,'location':160,'requirements':900,'additionalNotes':900}
        need(all((k=='additionalNotes' and v[k]=='') or clean(v[k],n,n*4) for k,n in limits.items()))
    else:
        ids=v['sequenceIds']
        need(type(ids)is list and 1<=len(ids)<=30 and all(match(x,OFFICIAL) for x in ids) and len(set(ids))==len(ids))
    return v


def graph(mode,db,oid):
    need(match(oid,OFFICIAL));c=CONFIG[mode]
    r=db.execute('SELECT * FROM workflow_runs WHERE official_request_id=?',(oid,)).fetchone()
    need(r is not None and r['request_type']==c['type'] and r['schema_version']==c['schema'] and r['project_id']=='binari'
         and r['state']=='planned' and r['reason'] in (None,'RS_COMPLETED'))
    raw=r['payload'].encode('utf8');p=strict_json(raw)
    need(keys(p,('schemaVersion','projectId',*c['input'])) and p['schemaVersion']==c['schema'] and p['projectId']=='binari'
         and sha256(raw).hexdigest()==r['payload_sha256'])
    inp=validate_input(mode,{k:p[k] for k in c['input']})
    tasks=db.execute('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 2',(oid,)).fetchall()
    need(len(tasks)==1);t=tasks[0]
    candidate=validate_input(mode,strict_json(t['task_input'].encode('utf8')))
    need(t['task_id']=='workflow.task.'+digest([oid,c['key']]) and t['task_key']==c['key'] and
         t['task_type']==c['type'] and t['worker_type']==c['worker'] and t['depends_on_task_id'] is None and candidate==inp)
    return r,t,inp


def source_result(m,oid,title,mode=SOURCE):
    need(keys(m,('schemaVersion','projectId','officialRequestId','title','sha256','bytes','width','height')) and
         m['schemaVersion']==('ngs.background-source-result.v1' if mode==BACKGROUND else 'ngs.cinematic-source-result.v1') and m['projectId']=='binari' and m['officialRequestId']==oid and
         m['title']==title and match(m['sha256'],HASH) and type(m['bytes'])is int and 0<m['bytes']<=8*1024*1024 and
         type(m['width'])is int and type(m['height'])is int and m['width']==1536 and m['height']==1024)
    return 'binari/cinematics/'+('backgrounds' if mode==BACKGROUND else 'sources')+'/'+m['sha256']+'.json'


class WorkflowCinematicProject(WorkflowCinematic):
    def __init__(self,planner,mode,*,writer_factory=None):
        need(mode in CONFIG)
        super().__init__(planner,writer_factory=writer_factory)
        self.mode=mode
    def ready(self):
        with self.planner.snapshot():pass
        with self.writer_factory() as tx:
            c=tx.capabilities()
            return type(c)is dict and c.get('namespace')=='canonical-files/binari/cinematics/' and c.get(CONFIG[self.mode]['filex']) is True
    def provider(self,endpoint,p,missing=False):
        need(endpoint in ('','/result','/reconcile'))
        with self.writer_factory() as tx:
            r=tx.call('POST',WRITER+self.mode+endpoint,p,missing=missing)
        if r is None:raise FileNotFoundError('cinematic project result not found')
        return r
    def graph(self,db,oid):return graph(self.mode,db,oid)
    def result(self,p):
        need(keys(p,('officialRequestId',)) and match(p['officialRequestId'],OFFICIAL))
        oid=p['officialRequestId']
        with self.planner.snapshot() as db:
            _,task,inp=self.graph(db,oid)
            need(task['status']=='completed')
            if self.mode in SOURCE_MODES:
                m=self.provider('/result',p,missing=True);ref=source_result(m,oid,inp['title'],self.mode);out={'manifest':m}
            else:
                expected={**p,**inp};need(self.provider('/result',expected,missing=True)==expected)
                ref=RESULT_ARCHIVE;out={**inp}
            need(task['result_ref']==ref)
        return {'ok':True,'resultRef':ref,**out}
    def operate(self,op,p):
        if op=='result':return self.result(p)
        need((op in ('read','publish','reconcile') if self.mode in SOURCE_MODES else op=='publish') and
             keys(p,('claim','artifact') if self.mode in SOURCE_MODES and op=='publish' else ('claim',)))
        with exact_claim(self.planner,p['claim'],self.graph) as (oid,inp):
            if op=='read':return inp
            if op=='reconcile':
                value=self.provider('/reconcile',{'officialRequestId':oid,'claimStamp':p['claim']['claimStamp']})
                if value=={'state':'absent_fenced'}:return value
                need(keys(value,('state','manifest')) and value['state']=='published')
                return {'state':'published','resultRef':source_result(value['manifest'],oid,inp['title'],self.mode)}
            if self.mode in SOURCE_MODES:
                a=p['artifact'];need(keys(a,('sha256','bytes','width','height','base64')) and type(a['base64'])is str and
                                    0<len(a['base64'])<=4*((8*1024*1024+2)//3))
                m={**{k:v for k,v in a.items() if k!='base64'},'schemaVersion':('ngs.background-source-result.v1' if self.mode==BACKGROUND else 'ngs.cinematic-source-result.v1'),
                   'projectId':'binari','officialRequestId':oid,'title':inp['title']}
                ref=source_result(m,oid,inp['title'],self.mode)
                need(self.provider('',{'manifest':m,'base64':a['base64'],'claimStamp':p['claim']['claimStamp']})==m)
            else:
                with self.planner.snapshot() as db:
                    for i in inp['sequenceIds']:
                        _,t,_=sequence_graph(db,i)
                        need(t['status']=='completed' and t['result_ref']==sequence_ref(i))
                archive={'officialRequestId':oid,**inp}
                need(self.provider('',archive)==archive);ref=RESULT_ARCHIVE
            return {'ok':True,'resultRef':ref}

def serve(handler):
    pathname=handler.path.split('?',1)[0]
    mode=next((x for x in SOURCE_MODES if pathname in tuple(ORIGIN+x+y for y in ('','/read','/result','/reconcile'))),ARCHIVE)
    return serve_cinematic(handler,attribute='workflow_cinematic_'+mode.replace('-','_'),path=ORIGIN+mode,
                           capability=CONFIG[mode]['capability'])
