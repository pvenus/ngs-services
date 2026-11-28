"""Scoped source-image publication and non-destructive sequence removal."""
import base64
from hashlib import sha256
from .workflow_save_reservation import optionally_save
from .filex_read import digest
from .workflow_cinematic import (WorkflowCinematic, BAD_TEXT, JS_ENDS, HASH, OFFICIAL, need, keys, match,
                                 serve as serve_cinematic)
from .workflow_cinematic_sequence import exact_claim, strict_json, sequence_graph, ref as sequence_ref

ORIGIN='/v1/cinematics/ownership/workflow-'
WRITER='/v1/workflow/cinematics-'
SOURCE='source'
BACKGROUND='background'
PIXEL='background-pixel'
CINE_BG='background-cinematic-source'
DRAFT='background-pixel-draft'
PIXEL_MODES=(CINE_BG,DRAFT,PIXEL)
SOURCE_MODES=(SOURCE,BACKGROUND,*PIXEL_MODES)
PIXEL_FIELDS=('title','timeOfDay','season','mood','color','location','requirements','additionalNotes')
PIXEL_SUFFIX={CINE_BG:' · 시네마틱 원본',DRAFT:' · 픽셀 생성본',PIXEL:' · 픽셀 보정'}
ARCHIVE='sequence-remove'
CONFIG={
 SOURCE:{'type':'cinematic.source.generate','schema':'ngs.cinematic-source-generate.v1',
         'key':'cinematic_source_generate','worker':'image','input':('title','prompt'),
         'capability':'workflow.cinematic-source-write.v1','filex':'workflowCinematicSourceV1'},
 BACKGROUND:{'type':'background.source.generate','schema':'ngs.background-source-generate.v1',
             'key':'background_source_generate','worker':'image','input':('title','timeOfDay','season','mood','color','location','requirements','additionalNotes'),
             'capability':'workflow.background-source-write.v1','filex':'workflowBackgroundSourceV1'},
 **{mode:{'type':'background.pixel.workflow','schema':'ngs.background-pixel-workflow.v1','key':key,'worker':worker,
          'input':PIXEL_FIELDS,'capability':'workflow.background-pixel-write.v1','filex':'workflowBackgroundPixelV1'}
    for mode,key,worker in zip(PIXEL_MODES,('background_cinematic_source','background_pixel_generate','background_pixel_postprocess'),
                               ('image','image','postprocess'))},
 ARCHIVE:{'type':'cinematic.sequences.remove','schema':'ngs.cinematic-sequences-remove.v1',
          'key':'cinematic_sequences_remove','worker':'file','input':('sequenceIds',),
          'capability':'workflow.cinematic-sequence-remove.v1','filex':'workflowCinematicSequenceRemoveV1'},
}
RESULT_ARCHIVE='binari/cinematics/sequence-archive.json'
ROUTES=(ORIGIN+SOURCE,ORIGIN+SOURCE+'/read',ORIGIN+SOURCE+'/result',ORIGIN+SOURCE+'/reconcile',
        *(ORIGIN+BACKGROUND+suffix for suffix in ('','/read','/result','/reconcile')),
        *(ORIGIN+mode+suffix for mode in PIXEL_MODES for suffix in ('','/read','/asset','/result','/reconcile')),
        ORIGIN+ARCHIVE,ORIGIN+ARCHIVE+'/result')


def clean(v,chars,bytes_max):
    return (type(v)is str and 1<=len(v)<=chars and len(v.encode('utf-8'))<=bytes_max and
            v.strip(JS_ENDS)==v and BAD_TEXT.search(v) is None)


def validate_input(mode,v):
    need(keys(v,CONFIG[mode]['input']))
    if mode==SOURCE:need(clean(v['title'],120,480) and clean(v['prompt'],2000,4000))
    elif mode==BACKGROUND or mode in PIXEL_MODES:
        limits={'title':105 if mode in PIXEL_MODES else 120,'timeOfDay':80,'season':80,'mood':160,'color':120,'location':160,'requirements':900,'additionalNotes':900}
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
    tasks=db.execute('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 4',(oid,)).fetchall()
    modes=PIXEL_MODES if mode in PIXEL_MODES else (mode,)
    need(len(tasks)==len(modes))
    by_key={t['task_key']:t for t in tasks};previous=None
    for stage in modes:
        spec=CONFIG[stage]; key=spec['key']; t=by_key.get(key)
        need(t is not None and t['task_id']=='workflow.task.'+digest([oid,key]) and
             t['task_type']==c['type'] and t['worker_type']==spec['worker'] and
             t['depends_on_task_id']==previous and validate_input(stage,strict_json(t['task_input'].encode('utf8')))==inp)
        previous=t['task_id']
    t=by_key[c['key']]
    return r,t,inp


def source_result(m,oid,title,mode=SOURCE):
    need(keys(m,('schemaVersion','projectId','officialRequestId','title','sha256','bytes','width','height')) and
         m['schemaVersion']==('ngs.background-source-result.v1' if (mode==BACKGROUND or mode in PIXEL_MODES) else 'ngs.cinematic-source-result.v1') and m['projectId']=='binari' and m['officialRequestId']==oid and
         m['title']==title and match(m['sha256'],HASH) and type(m['bytes'])is int and 0<m['bytes']<=8*1024*1024 and
         type(m['width'])is int and type(m['height'])is int and m['width']==1536 and
         m['height'] in ((1024,) if mode==BACKGROUND or mode in PIXEL_MODES else (768,1024)))
    return 'binari/cinematics/'+('backgrounds' if (mode==BACKGROUND or mode in PIXEL_MODES) else 'sources')+'/'+m['sha256']+'.json'


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
            r=tx.call('POST',WRITER+(BACKGROUND if self.mode in PIXEL_MODES else self.mode)+endpoint,p,missing=missing)
        if r is None:raise FileNotFoundError('cinematic project result not found')
        return r
    def graph(self,db,oid):return graph(self.mode,db,oid)
    @staticmethod
    def publication_id(oid,mode):
        return 'request.official.'+digest([oid,CONFIG[mode]['key']]) if mode in (CINE_BG,DRAFT) else oid
    def predecessor(self,oid,inp):
        index=PIXEL_MODES.index(self.mode)
        need(index>0)
        stage=PIXEL_MODES[index-1]
        with self.planner.snapshot() as db:
            _,task,_=graph(stage,db,oid)
            need(task['status']=='completed')
        source_id=self.publication_id(oid,stage)
        m=self.provider('/result',{'officialRequestId':source_id},missing=True)
        need(task['result_ref']==source_result(m,source_id,inp['title']+PIXEL_SUFFIX[stage],stage))
        return m['sha256']

    def pixel_source(self,digest,include_bytes=False):
        with self.writer_factory() as tx:
            row=tx.call('POST',WRITER+BACKGROUND+('/asset' if include_bytes else '/source-status'),{'sourceSha256':digest},missing=True)
        if row is None:raise FileNotFoundError('background pixel source not found')
        need(keys(row,('title','sourceSha256','base64') if include_bytes else ('title','sourceSha256')) and row['sourceSha256']==digest
             and clean(row['title'],120,480))
        if include_bytes:
            raw=base64.b64decode(row['base64'],validate=True)
            need(len(raw)<=8*1024*1024 and sha256(raw).hexdigest()==digest)
            return raw
        return row['title']

    def result(self,p):
        need(keys(p,('officialRequestId',)) and match(p['officialRequestId'],OFFICIAL))
        oid=p['officialRequestId']
        with self.planner.snapshot() as db:
            _,task,inp=self.graph(db,oid)
            need(task['status']=='completed')
        if self.mode in SOURCE_MODES:
            save_id=self.publication_id(oid,self.mode)
            m=self.provider('/result',{'officialRequestId':save_id},missing=True)
            title=inp['title']+PIXEL_SUFFIX.get(self.mode,'')
            ref=source_result(m,save_id,title,self.mode);out={'manifest':m}
        else:
            expected={**p,**inp};need(self.provider('/result',expected,missing=True)==expected)
            ref=RESULT_ARCHIVE;out={**inp}
        need(task['result_ref']==ref)
        return {'ok':True,'resultRef':ref,**out}
    def operate(self,op,p):
        if op=='result':return self.result(p)
        need((op in ('read','asset','publish','reconcile') if self.mode in (DRAFT,PIXEL) else ('read','publish','reconcile') if self.mode in SOURCE_MODES else op=='publish') and
             keys(p,('claim','artifact') if self.mode in SOURCE_MODES and op=='publish' else ('claim',)))
        with exact_claim(self.planner,p['claim'],self.graph) as (oid,inp), optionally_save(self.planner,p['claim'],op=='publish'):
            if op=='read':
                if self.mode not in (DRAFT,PIXEL):return inp
                previous=self.predecessor(oid,inp)
                out={**inp,'sourceSha256':previous}
                if self.mode==DRAFT:out['base64']=base64.b64encode(self.pixel_source(previous,True)).decode('ascii')
                return out
            source=self.predecessor(oid,inp) if self.mode in (DRAFT,PIXEL) and op in ('asset','publish') else None
            if op=='asset':return self.pixel_source(source,include_bytes=True)
            save_id=self.publication_id(oid,self.mode)
            title=None if self.mode==ARCHIVE else inp['title']+PIXEL_SUFFIX.get(self.mode,'')

            if op=='reconcile':
                value=self.provider('/reconcile',{'officialRequestId':save_id,'claimStamp':p['claim']['claimStamp']})
                if value=={'state':'absent_fenced'}:return value
                need(keys(value,('state','manifest')) and value['state']=='published')
                m=value['manifest']
                return {'state':'published','resultRef':source_result(m,save_id,title,self.mode)}
            if self.mode in SOURCE_MODES:
                a=p['artifact'];need(keys(a,('sha256','bytes','width','height','base64')) and type(a['base64'])is str and
                                    0<len(a['base64'])<=4*((8*1024*1024+2)//3))
                m={**{k:v for k,v in a.items() if k!='base64'},'schemaVersion':('ngs.background-source-result.v1' if (self.mode==BACKGROUND or self.mode in PIXEL_MODES) else 'ngs.cinematic-source-result.v1'),
                   'projectId':'binari','officialRequestId':save_id,'title':title}
                if source is not None:need(m['sha256']!=source)
                ref=source_result(m,save_id,title,self.mode)
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
    mode=next((x for x in SOURCE_MODES if pathname in tuple(ORIGIN+x+y for y in ('','/read','/result','/reconcile','/asset'))),ARCHIVE)
    return serve_cinematic(handler,attribute='workflow_cinematic_'+mode.replace('-','_'),path=ORIGIN+mode,
                           capability=CONFIG[mode]['capability'])
