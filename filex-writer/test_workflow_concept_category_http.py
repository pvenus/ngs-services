import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'data-service'))
from concept_store import ConceptStore,encoded,digest
from server import handler
from project_data_service.concept_filex import split,FilexConcept
from project_data_service.filex_cas_transport import FilexCASTransport
from project_data_service.http_server import Handler
from project_data_service.workflow_delete_claim import PrivatePlannerClaim
from project_data_service.workflow_concept_category import CategoryWorkflow,PATH,ROOT,SCHEMA,task

class Tests(unittest.TestCase):
    def service(self,h):
        s=ThreadingHTTPServer(('127.0.0.1',0),h)
        t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
        self.addCleanup(t.join);self.addCleanup(s.server_close);self.addCleanup(s.shutdown)
        return s
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        root=Path(tmp.name).resolve();data=root/'concept';data.mkdir();cookie=root/'token';cookie.write_text('f'*32);cookie.chmod(0o600)
        self.db=root/'planner.sqlite'
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
            CREATE TABLE workflow_runs(official_request_id TEXT PRIMARY KEY,project_id TEXT,request_type TEXT,schema_version TEXT,payload TEXT,payload_sha256 TEXT,registered_at INTEGER,state TEXT,updated_at INTEGER,reason TEXT);
            CREATE TABLE workflow_tasks(task_id TEXT PRIMARY KEY,official_request_id TEXT,task_key TEXT,task_type TEXT,worker_type TEXT,task_input TEXT,result_ref TEXT,updated_at INTEGER,depends_on_task_id TEXT,status TEXT);
            """)
        self.db.chmod(0o600)
        self.oid='request.official.'+'1'*64
        self.writer=ConceptStore(data);self.data=data
        d=json.loads((ROOT/'seed.json').read_text());d['schemaVersion']=2;d['projectId']='ProjectBS'
        self.custom='category.add.'+'2'*32;self.other='category.add.'+'5'*32
        sm={'schemaVersion':'npc-concept-summary.v1','keywords':['fixture'],'sentence':'fixture'}
        field={'id':'fixture','label':'세부\n이름','value':'한글\n```js\ncode\n```\n','status':'source','sources':['fixture ref'],'priority':1,'summary':sm}
        for i,c in enumerate((self.custom,self.other),9):
            d['sections'].append(dict(id=c,title='Test '+str(i),description='intro',summary=sm,priority=i,fields=[field]))
        d['hierarchy']={'schemaVersion':'npc-concept-groups.v1',
            'groups':[{'id':'sample','title':'sample','categoryIds':[s['id']for s in d['sections'] if s['id']!=self.custom]}],
            'archivedCategoryIds':[self.custom],'archivedItems':[{'categoryId':self.custom,'itemId':'fixture'}]}
        self.original=d
        for k,v in split(dict(revision=1,savedAt='fixture',note='fixture',document=d)).items():
            p=data/k;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(encoded(v))
        fx=self.service(handler(self.writer,'f'*32))
        cf=dict(schemaVersion='pds-filex-cas.v1',projectId='binari',enabled=True,endpoint=f'http://127.0.0.1:{fx.server_port}',tokenFile=str(cookie))
        self.source=FilexConcept(transport_factory=lambda:FilexCASTransport(cf))
        st=self.db.stat()
        self.pds=self.service(Handler)
        self.pds.workflow_concept_category=CategoryWorkflow(self.source,PrivatePlannerClaim(str(self.db),st.st_dev,st.st_ino))
    def claim(self,op,cid,title,key,*,oid=None,parent=None,group=None,headings=None):
        oid=oid or self.oid
        p=dict(schemaVersion=SCHEMA,projectId='binari',operation=op,categoryId=cid,groupId=group,title=title,
               outline='새 마스터 본문' if op in ('add','replace') else '',requirements='기존 내용을 유지하고 Markdown 표현만 정리' if op=='update' else '')
        if headings is not None:p['requiredSections']=headings
        tid=task(oid,key);lane='file' if key=='category_delete' else 'document'
        with sqlite3.connect(self.db) as db:
            db.execute('INSERT OR IGNORE INTO workflow_runs VALUES(?,?,?,?,?,?,1,\'planned\',1,NULL)',
                       (oid,'binari','concept.category.workflow',SCHEMA,encoded(p).decode(),digest(p)))
            db.execute("INSERT INTO workflow_tasks(task_id,official_request_id,task_key,task_type,worker_type,task_input,result_ref,updated_at,depends_on_task_id,status) VALUES(?,?,?,?,?,?,NULL,2,?,'running')",
                       (tid,oid,key,'concept.category-delete' if lane=='file' else 'concept.category-generate',lane,encoded({'categoryId':cid}).decode(),parent))
        return dict(officialRequestId=oid,taskId=tid,projectId='binari',categoryId=cid,payloadSha256=digest(p),claimStamp=2)
    def complete(self,c):
        with sqlite3.connect(self.db) as db:db.execute("UPDATE workflow_tasks SET status='completed' WHERE task_id=?",(c['taskId'],))
    def request(self,p=None,suffix=''):
        r=Request(f'http://127.0.0.1:{self.pds.server_port}'+PATH+suffix,data=None if p is None else encoded(p),headers={'Content-Type':'application/json'})
        try:
            with urlopen(r,timeout=10) as response:return response.status,json.load(response)
        except HTTPError as e:
            with e:return e.code,json.load(e)
    def publish(self,c,title,content):
        return self.request(dict(claim=c,artifact=dict(schemaVersion='workflow.concept-category-markdown.v1',
                 categoryId=c['categoryId'],title=title,content=content)))
    def test_migrate_builtin_and_archived_losslessly_resume_no_authority_duplicate(self):
        self.assertEqual(self.request(),(200,{'enabled':True,'schemaVersion':'workflow.concept-category-write.v1'}))
        for i,cid in enumerate(['expression',self.custom]):
            prior=next(x for x in self.original['sections'] if x['id']==cid);title=prior['title']
            c=self.claim('migrate',cid,title,'category_generate',oid='request.official.'+str(i+2)*64)
            code,val=self.request({'claim':c},'/read');self.assertEqual(code,200)
            self.assertNotIn(self.other,val['source']);self.assertEqual(val['title'],title)
            self.assertEqual(self.publish(c,title,val['source']+'bad')[0],403)
            self.assertTrue((self.data/'sections'/cid/'metadata.json').exists())
            # Interrupt before physical transition, then recover from durable staged content.
            if i==0:
                real=self.writer.recover_markdown
                def fail_if_prepared():
                    if (self.data/'.concept-markdown.json').exists():raise OSError('fixture interruption')
                    real()
                with patch.object(self.writer,'recover_markdown',fail_if_prepared):
                    self.assertEqual(self.publish(c,title,val['source'])[0],409)
                    self.assertTrue((self.data/'sections'/cid/'metadata.json').exists())
            self.assertEqual(self.publish(c,title,val['source']),(200,{'ok':True}))
            dir=self.data/'sections'/cid
            self.assertEqual([p.name for p in dir.rglob('*') if p.is_file()],['content.md'])
            self.assertEqual((dir/'content.md').read_text(),val['source'])
            self.assertEqual(self.source.current()['document'],self.original)
            self.assertEqual(self.source.snapshot()['markdownSections'][cid]['mode'],'structured')
        # No fallback to stale JSON after exact Markdown authority is registered.
        (self.data/'sections'/'expression'/'content.md').write_text('tampered')
        with self.assertRaises(ValueError):self.source.current()
    def test_replace_one_category_scoped_backup_then_add_and_denial(self):
        built=self.original['sections'][0]
        protected=self.claim('remove',built['id'],built['title'],'category_delete',oid='request.official.'+'9'*64)
        self.assertEqual(self.request({'claim':protected}),(403,{'error':'CONCEPT_REQUIRED_CATEGORY_REMOVAL_DENIED'}))
        c=self.claim('replace',self.custom,'Test 9','category_delete')
        old_other=(self.data/'sections'/self.other/'fields'/'fixture.json').read_bytes()
        self.assertEqual(self.request({'claim':c}),(200,{'ok':True}))
        self.assertEqual(self.request({'claim':c}),(200,{'ok':True}))
        self.assertFalse(any(p.is_file() for p in (self.data/'sections'/self.custom).rglob('*')))
        gen=self.claim('replace',self.custom,'Test 9','category_generate',parent=c['taskId'])
        self.assertEqual(self.request({'claim':gen},'/read')[0],403)
        self.complete(c)
        code,val=self.request({'claim':gen},'/read');self.assertEqual(code,200)
        self.assertIn('fixture ref',val['source']);self.assertIn('한글',val['source']);self.assertNotIn(self.other,val['source'])
        self.assertEqual(self.publish(gen,'Test 9','# Test 9\n\n새 내용.'),(200,{'ok':True}))
        current=self.source.current()['document'];self.assertEqual(current['hierarchy']['archivedCategoryIds'],[self.custom])
        self.assertEqual((self.data/'sections'/self.other/'fields'/'fixture.json').read_bytes(),old_other)
        self.assertEqual(current['sections'][8]['fields'][0]['value'],'# Test 9\n\n새 내용.')
        cid='category.add.'+'a'*32
        add=self.claim('add',cid,'New section','category_generate',group='sample',oid='request.official.'+'7'*64)
        self.assertEqual(self.publish(add,'New section','## 본문\n\n규칙.'),(200,{'ok':True}))
        self.assertEqual(self.source.current()['document']['hierarchy']['groups'][0]['categoryIds'][-1],cid)

    def test_remove_only_one_custom_category_with_recoverable_copy(self):
        target=next(s for s in self.original['sections'] if s['id']==self.custom)
        c=self.claim('remove',self.custom,target['title'],'category_delete')
        self.assertEqual(self.request({'claim':c}),(200,{'ok':True}))
        prior_bytes=(self.data/'sections'/self.other/'fields'/'fixture.json').read_bytes()
        restored=self.writer.markdown_previous(c['taskId'])
        self.assertEqual(restored['operation'],'remove')
        self.assertIn(target['fields'][0]['value'],restored['source'])
        self.assertNotIn(self.custom,self.source.snapshot()['documents']['metadata.json']['sectionIds'])
        self.assertEqual(self.source.current()['document']['hierarchy']['archivedCategoryIds'],[])
        self.assertEqual((self.data/'sections'/self.other/'fields'/'fixture.json').read_bytes(),prior_bytes)

    def attach_single_target(self):
        from project_data_service.single_target_delete import SingleTargetDelete
        from project_data_service.workflow_concept_category import CONCEPT_ROOT
        self.pds.single_target_delete=SingleTargetDelete({CONCEPT_ROOT:self.pds.workflow_concept_category.storage_provider()})
    def generic_request(self,claim):
        from project_data_service.single_target_delete import PATH
        from project_data_service.workflow_concept_category import CONCEPT_ROOT
        p={'claim':claim,'target':{'root':CONCEPT_ROOT,'path':'sections/'+claim['categoryId'],'kind':'directory'}}
        req=Request(f'http://127.0.0.1:{self.pds.server_port}'+PATH,data=encoded(p),
                    headers={'Content-Type':'application/json'})
        try:
            with urlopen(req,timeout=10) as r:return r.status,json.load(r)
        except HTTPError as exc:
            with exc:return exc.code,json.load(exc)
    def test_generic_master_replace_one_real_move_then_no_sibling_rewrite_and_legacy_proof(self):
        import os
        from subprocess import check_output
        self.attach_single_target()
        chosen=self.data/'sections'/self.custom
        untouched=self.data/'sections'/self.other/'metadata.json';preserved=untouched.read_bytes()
        aid=self.claim('replace',self.custom,'Test 9','category_delete')
        former=(chosen/'fields'/'fixture.json').read_bytes()
        original_rename=os.rename;directory_moves=[]
        def rename(src,dst,*args,**kwargs):
            if src==self.custom and dst=='target':directory_moves.append((src,dst))
            return original_rename(src,dst,*args,**kwargs)
        with patch('single_target_delete.os.rename',side_effect=rename):
            worker=os.environ.get('WORKFLOW_FILE_SINGLE_TARGET_SOURCE')
            if worker:
                with sqlite3.connect(self.db) as db:
                    db.execute('ALTER TABLE workflow_tasks ADD retry_count INTEGER NOT NULL DEFAULT 0')
                    db.execute("UPDATE workflow_tasks SET status='pending' WHERE task_id=?",(aid['taskId'],))
                fragment="""import{runDeleteFromPath,conceptDelete}from'./worker.mjs';
                import{requestLocalJson,localPorts}from'./local-ports.mjs';
                const [dbPath,officialRequestId,taskId,port]=process.argv.slice(1);
                const pds=localPorts({singleTarget:true,transport:o=>requestLocalJson({...o,port:Number(port)})});
                if(!await pds.capability(undefined,conceptDelete))throw Error('fixture capability');
                console.log(JSON.stringify(await runDeleteFromPath({dbPath,officialRequestId,taskId,pds,operation:conceptDelete})));"""
                result=json.loads(check_output(['node','--input-type=module','-e',fragment,
                    str(self.db),self.oid,aid['taskId'],str(self.pds.server_port)],cwd=worker))
                self.assertEqual(result['state'],'completed')
                with sqlite3.connect(self.db) as db:
                    self.assertEqual(db.execute('SELECT status,result_ref FROM workflow_tasks WHERE task_id=?',(aid['taskId'],)).fetchone(),
                                     ('completed',None))
                with self.source.factory() as tx:receipt=tx.receipt(aid['taskId'])['storageDelete']
            else:
                code,receipt=self.generic_request(aid);self.assertEqual(code,200)
                self.assertEqual(self.generic_request(aid),(200,receipt))
                self.complete(aid)
        self.assertEqual(directory_moves,[(self.custom,'target')])
        self.assertFalse(chosen.exists())
        self.assertEqual((self.data/'.storage-quarantine'/receipt['recoveryId']/'target'/'fields'/'fixture.json').read_bytes(),former)
        self.assertEqual(untouched.read_bytes(),preserved)
        doc=self.source.current()['document']
        self.assertEqual(next(x for x in doc['sections'] if x['id']==self.other)['priority'],9)
        gen=self.claim('replace',self.custom,'Test 9','category_generate',parent=aid['taskId'])
        status,old=self.request({'claim':gen},'/read')
        self.assertEqual(status,200);self.assertIn('한글',old['source'])
        self.assertEqual(self.publish(gen,'Test 9','# replacement\n\nComplete.'),(200,{'ok':True}))
        self.assertEqual(untouched.read_bytes(),preserved)
        self.assertEqual(self.source.current()['document']['hierarchy']['archivedCategoryIds'],[self.custom])
    def test_generic_master_catalog_fault_is_fail_closed_then_reconciles_move_once(self):
        self.attach_single_target()
        selected=self.data/'sections'/self.custom
        marker=self.data/'metadata.json';old=marker.read_bytes()
        c=self.claim('remove',self.custom,'Test 9','category_delete')
        actual=self.writer.write
        def fail_catalog(path,content):
            if path==marker and not selected.exists():raise OSError('fixture catalog unavailable')
            return actual(path,content)
        with patch.object(self.writer,'write',side_effect=fail_catalog):
            self.assertEqual(self.generic_request(c)[0],409)
            self.assertEqual(marker.read_bytes(),old)
            self.assertFalse(selected.exists())
            with self.assertRaises(ValueError):self.source.snapshot()
        code,out=self.generic_request(c);self.assertEqual(code,200)
        self.assertEqual(self.generic_request(c),(200,out))
        self.assertNotIn(self.custom,self.source.snapshot()['documents']['metadata.json']['sectionIds'])

    def test_character_migration_leaves_macos_sidebar_metadata_untouched(self):
        target=next(s for s in self.original['sections']if s['id']=='character')
        dir=self.data/'sections'/'character'
        sidecar=dir/'.DS_Store';sidecar.write_bytes(b'fixture macOS metadata')
        before=self.source.current()['document']
        c=self.claim('migrate','character',target['title'],'category_generate')
        status,out=self.request({'claim':c},'/read');self.assertEqual(status,200)
        undeclared=dir/'unknown.bin';undeclared.write_bytes(b'not a whitelisted sidecar')
        self.assertEqual(self.publish(c,target['title'],out['source'])[0],409)
        self.assertFalse((dir/'content.md').exists())
        self.assertEqual(undeclared.read_bytes(),b'not a whitelisted sidecar')
        undeclared.unlink()
        self.assertEqual(self.publish(c,target['title'],out['source']),(200,{'ok':True}))
        self.assertEqual(sidecar.read_bytes(),b'fixture macOS metadata')
        self.assertEqual(self.source.current()['document'],before)
        self.assertFalse((dir/'metadata.json').exists())

    def overwrite(self,c,title,content,source_hash):
        return self.request(dict(claim=c,artifact=dict(schemaVersion='workflow.concept-category-markdown.v1',
                 categoryId=c['categoryId'],title=title,content=content,expectedSourceSha256=source_hash)))

    def test_update_structured_master_entire_current_read_and_one_scoped_overwrite(self):
        from hashlib import sha256
        from single_target_delete import SingleTargetDelete
        c=self.claim('migrate',self.custom,'Test 9','category_generate')
        st,source=self.request({'claim':c},'/read');self.assertEqual(st,200)
        self.assertEqual(self.publish(c,'Test 9',source['source']),(200,{'ok':True}))
        path=self.data/'sections'/self.custom/'content.md'
        old=path.read_bytes()
        peer=self.data/'sections'/self.other/'metadata.json';peer_bytes=peer.read_bytes()
        previous=self.source.current()['document']
        upd=self.claim('update',self.custom,'Test 9','category_generate',oid='request.official.'+'c'*64)
        stale=self.claim('update',self.custom,'Test 9','category_generate',oid='request.official.'+'d'*64)
        st,basis=self.request({'claim':upd},'/read');self.assertEqual(st,200)
        self.assertEqual(basis,{'categoryId':self.custom,'title':'Test 9','source':old.decode(),
                                'sourceSha256':sha256(old).hexdigest()})
        self.assertEqual(self.overwrite(upd,'Test 9','# missing legacy fields',basis['sourceSha256'])[0],403)
        self.assertEqual(path.read_bytes(),old)
        self.assertEqual(self.overwrite(upd,'Test 9',basis['source'],'0'*64)[0],403)
        self.assertEqual(path.read_bytes(),old)
        body=basis['source'].replace('한글','수정했어요')
        writes=[];original=self.writer.write
        def track(path_arg,content):
            if path_arg==path:
                self.assertTrue(path.exists())
                self.assertEqual(path.read_bytes(),old)
                writes.append(content)
            return original(path_arg,content)
        with patch.object(self.writer,'write',side_effect=track),patch.object(SingleTargetDelete,'remove_locked',side_effect=AssertionError('delete forbidden')):
            self.assertEqual(self.overwrite(upd,'Test 9',body,basis['sourceSha256']),(200,{'ok':True}))
            self.assertEqual(self.overwrite(upd,'Test 9',body,basis['sourceSha256']),(200,{'ok':True}))
        self.assertEqual(writes,[body.encode()]);self.assertEqual(path.read_bytes(),body.encode())
        self.assertEqual(self.overwrite(stale,'Test 9',body,basis['sourceSha256'])[0],403)
        self.assertEqual(peer.read_bytes(),peer_bytes)
        nextdoc=self.source.current()['document']
        self.assertEqual(nextdoc['hierarchy'],previous['hierarchy'])
        oldfields=next(s for s in previous['sections']if s['id']==self.custom)['fields']
        newfields=next(s for s in nextdoc['sections']if s['id']==self.custom)['fields']
        self.assertEqual(newfields,[{**f,'value':f['value'].replace('한글','수정했어요')}for f in oldfields])

    def test_add_intake_requires_well_formed_section_titles_but_storage_does_not_parse_headings(self):
        new='category.add.'+'e'*32
        invalid=self.claim('add',new,'New section','category_generate',group='sample',
            headings=['범위','범위'],oid='request.official.'+'e'*64)
        self.assertEqual(self.publish(invalid,'New section','## 범위')[0],403)
        c=self.claim('add',new,'New section','category_generate',group='sample',headings=['범위','규칙'])
        # Worker owns deterministic template. Storage is agnostic to Markdown headers.
        body='## 범위\n\n본문만'
        self.assertEqual(self.publish(c,'New section',body),(200,{'ok':True}))
        self.assertEqual((self.data/'sections'/new/'content.md').read_text(),body)
