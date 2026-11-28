"""Claim-bound UI PNG publication. The owning PDS transition completes the job."""
import base64
import hashlib
import os
from pathlib import Path
import stat
import struct
import tempfile
import time
import fcntl
from .filex_read import digest
from .workflow_cinematic import HASH, OFFICIAL, MAX_PNG, MAX_BODY, WorkflowCinematic, scoped_writer, keys, match, need, serve as serve_internal
from .workflow_cinematic_project import clean
from .workflow_cinematic_sequence import exact_claim, strict_json
from .workflow_save_reservation import save_handoff

BASE='/v1/ui/ownership/workflow-asset/'
STAGED='/v1/ui/ownership/staged'
WRITER='/v1/workflow/ui/assets'
MODES=('generate','import')
ROUTES=(STAGED,*(BASE+mode+tail for mode in MODES for tail in ('','/read','/result')))
DESIGN='category.add.4573d0a2e2c659fc0bba561ba5774ac8'
TITLE='UI 디자인'
FIELDS={'schemaVersion','projectId','officialRequestId','kind','title','sha256','bytes','width','height','origin','designSha256'}


def ui_design(snapshot):
    doc=snapshot.get('document',{})
    hierarchy=doc.get('hierarchy',{})
    groups=hierarchy.get('groups',[])
    occurrences=[g.get('id') for g in groups for cid in g.get('categoryIds',[]) if cid==DESIGN]
    need(doc.get('projectId')=='ProjectBS' and occurrences==['group.art-design'] and DESIGN not in hierarchy.get('archivedCategoryIds',[]))
    found=[s for s in doc.get('sections',[]) if s.get('id')==DESIGN]
    need(len(found)==1 and found[0].get('title')==TITLE)
    fields=found[0].get('fields',[])
    need(len(fields)==1 and fields[0].get('id')=='content')
    body=fields[0].get('value')
    need(type(body)is str and body.startswith('# '+TITLE+'\n') and clean(body.strip(),12000,24000) and len(body.encode())<=24000)
    return {'categoryId':DESIGN,'sha256':hashlib.sha256(body.encode()).hexdigest(),'markdown':body}


def decode(data,sha):
    need(type(data)is str and 0<len(data)<=4*((MAX_PNG+2)//3) and match(sha,HASH))
    raw=base64.b64decode(data,validate=True)
    need(0<len(raw)<=MAX_PNG and hashlib.sha256(raw).hexdigest()==sha)
    dimensions(raw)
    return raw


def dimensions(raw):
    need(len(raw)>=57 and raw[:16]==b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' and raw[24:29]==b'\x08\x06\x00\x00\x00')
    width,height=struct.unpack_from('>II',raw,16)
    need(0<width<=4096 and 0<height<=4096)
    return width,height


def stage_read(path):
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as f:
        st=os.fstat(f.fileno())
        need(stat.S_ISREG(st.st_mode) and st.st_uid==os.getuid() and st.st_nlink==1 and st.st_mode&0o077==0)
        raw=f.read(MAX_PNG+1)
    need(0<len(raw)<=MAX_PNG)
    return raw


class WorkflowUiAsset(WorkflowCinematic):
    def __init__(self,planner,mode,concept_reader,*,writer_factory=None,stage_dir=None):
        super().__init__(planner,writer_factory=writer_factory or (lambda:scoped_writer('ui')))
        self.mode=mode; self.concept_reader=concept_reader
        self.stage_dir=Path(stage_dir or Path.home()/'Library/Application Support/NPCGen/ui-intake')
        self.kind='ui.asset.'+mode
        self.schema='ngs.ui-asset-'+mode+'.v1'
        self.key='ui_asset_'+mode
        self.fields=('kind','title','prompt' if mode=='generate' else 'sha256')
    def ready(self):
        if self.mode=='generate':
            ui_design(self.concept_reader())
        with self.planner.snapshot():pass
        with self.writer_factory() as tx:
            c=tx.capabilities()
            return type(c)is dict and c.get('namespace')=='canonical-files/binari/ui/' and c.get('workflowUiAssetV1') is True
    def stage_root(self):
        p=self.stage_dir
        p.mkdir(mode=0o700,parents=True,exist_ok=True)
        st=p.lstat()
        need(stat.S_ISDIR(st.st_mode) and st.st_mode&0o077==0 and st.st_uid==os.getuid() and p.resolve()==p)
        return p
    def stage(self,p):
        need(keys(p,('sha256','base64')))
        sha=p['sha256'];raw=decode(p['base64'],sha)
        root=self.stage_root()
        fd=os.open(root/'.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX)
            now=time.time();count=0
            for item in root.iterdir():
                if match(item.name,HASH):
                    st=item.lstat()
                    if stat.S_ISREG(st.st_mode) and now-st.st_mtime>7*86400:item.unlink()
                    else:count+=1
            dest=root/sha
            if dest.exists():need(stage_read(dest)==raw)
            else:
                need(count<64)
                with tempfile.NamedTemporaryFile(dir=root,prefix='.pending-',delete=False) as f:
                    name=Path(f.name)
                    try:
                        f.write(raw);f.flush();os.fsync(f.fileno());os.replace(name,dest)
                    finally:name.unlink(missing_ok=True)
        finally:os.close(fd)
        return {'ok':True,'sha256':sha}
    def graph(self,db,oid):
        r=db.execute('SELECT * FROM workflow_runs WHERE official_request_id=?',(oid,)).fetchone()
        need(r is not None and r['request_type']==self.kind and r['schema_version']==self.schema and r['project_id']=='binari'
            and r['state']=='planned' and r['reason'] in (None,'RS_COMPLETED'))
        raw=r['payload'].encode();p=strict_json(raw)
        need(keys(p,('schemaVersion','projectId',*self.fields)) and p['schemaVersion']==self.schema and p['projectId']=='binari'
             and hashlib.sha256(raw).hexdigest()==r['payload_sha256'] and p['kind'] in ('logo','button') and clean(p['title'],120,480))
        need(clean(p['prompt'],2000,4000) if self.mode=='generate' else match(p['sha256'],HASH))
        inp={k:p[k] for k in self.fields}
        tasks=db.execute('SELECT * FROM workflow_tasks WHERE official_request_id=? LIMIT 2',(oid,)).fetchall()
        need(len(tasks)==1);t=tasks[0]
        need(t['task_id']=='workflow.task.'+digest([oid,self.key]) and t['task_key']==self.key and t['task_type']==self.kind
             and t['worker_type']=='image' and t['depends_on_task_id'] is None and strict_json(t['task_input'].encode())==inp)
        return r,t,inp
    def result(self,p):
        need(keys(p,('officialRequestId',)) and match(p['officialRequestId'],OFFICIAL))
        oid=p['officialRequestId'];ref=f'binari/ui/assets/{oid}/manifest.json'
        with self.planner.snapshot() as db:
            _,t,inp=self.graph(db,oid)
            need(t['status']=='completed' and t['result_ref']==ref)
        with self.writer_factory() as tx:
            m=tx.call('POST',WRITER+'/result',p,missing=True)
        if m is None:raise FileNotFoundError('ui result not found')
        need(keys(m,FIELDS) and m['officialRequestId']==oid and m['title']==inp['title'] and m['kind']==inp['kind']
             and (m['origin']=='imagegen' if self.mode=='generate' else m['origin']=='manual' and m['sha256']==inp['sha256']))
        return {'ok':True,'resultRef':ref,'manifest':m}
    def operate(self,op,p):
        if op=='staged':return self.stage(p)
        if op=='result':return self.result(p)
        generated=self.mode=='generate'
        need(op in ('read','publish') and keys(p,('claim','artifact') if generated and op=='publish' else ('claim',)))
        with exact_claim(self.planner,p['claim'],self.graph) as (oid,inp):
            design=ui_design(self.concept_reader()) if generated else None
            if op=='read':return {**inp,'design':design} if generated else inp
            if generated:
                a=p['artifact'];need(keys(a,('base64','sha256','designSha256')) and a['designSha256']==design['sha256'])
                sha=a['sha256'];raw=decode(a['base64'],sha)
            else:
                sha=inp['sha256'];raw=stage_read(self.stage_root()/sha)
                need(hashlib.sha256(raw).hexdigest()==sha)
            width,height=dimensions(raw)
            m={'schemaVersion':'ngs.ui-asset-result.v1','projectId':'binari','officialRequestId':oid,'kind':inp['kind'],'title':inp['title'],
               'sha256':sha,'bytes':len(raw),'width':width,'height':height,'origin':'imagegen' if generated else 'manual',
               'designSha256':design['sha256'] if generated else None}
            ref=f'binari/ui/assets/{oid}/manifest.json'
            with save_handoff(self.planner,p['claim'],require_reservation=True) as reservation:
                with self.writer_factory() as tx:
                    need(tx.call('POST',WRITER,{'manifest':m,'base64':base64.b64encode(raw).decode()})==m)
                reservation.complete(ref)
            return {'ok':True,'resultRef':ref}


def serve(handler):
    pathname=handler.path.split('?',1)[0]
    mode='import' if pathname==STAGED else next(mode for mode in MODES if pathname in [BASE+mode+x for x in ('','/read','/result')])
    path=BASE+mode
    return serve_internal(handler,attribute='workflow_ui_'+mode,path=path,capability='workflow.ui-asset-'+mode+'.v1',
                          body_limits={path:MAX_BODY,STAGED:MAX_BODY})
