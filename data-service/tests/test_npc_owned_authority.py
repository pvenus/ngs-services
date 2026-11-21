import base64,copy,json,pathlib,tempfile,threading,unittest,sys,hashlib
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'filex-writer'))
from server import handler
from npc_store import NPCStore,encoded
from fenced_store import FencedOwnedNPCStore
from project_data_service.filex_npc_files import encode,PREFIX
from project_data_service.filex_npc_store import FilexNPCStore
from project_data_service.filex_npc_authority import FilexCurrentNPC
from project_data_service.npc_ownership import request_scope,make_transport,GUARD
from project_data_service.filex_read import digest
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name).resolve();self.data=self.root/'data';self.data.mkdir();token=self.root/'token';token.write_text('isolated-'+'a'*40);token.chmod(0o600)
  self.doc={'characters':[{'id':'npc','name':'preserve','planningCategories':[{'id':'art','content':'old'}]}]};files=encode(self.doc);files['metadata.json']['_pdsCurrent']={'revision':1,'savedAt':'test','note':'initial','documentHash':digest(self.doc)}
  old=NPCStore(self.data);old.batch('seed','h',[{'path':PREFIX+k,'expectedHash':None,'body':v}for k,v in files.items()],{PREFIX+k:None for k in files})
  self.writer=FencedOwnedNPCStore(self.data);self.generation=self.writer.bootstrap('npc',self.writer.snapshot()['sourceVersion'])['generationId'];self.http=ThreadingHTTPServer(('127.0.0.1',0),handler(self.writer,token.read_text()));self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
  self.config={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','scope':'npc','enabled':True,'ownershipMode':'npc-owned-v1','endpoint':'http://127.0.0.1:'+str(self.http.server_port),'tokenFile':str(token)}
  self.source=FilexCurrentNPC(FilexNPCStore(lambda:make_transport(self.config)))
  with make_transport(self.config) as t:self.guard=t.admit('npc',self.generation,'parent.request')
 def tearDown(self):self.http.shutdown();self.http.server_close();self.thread.join();self.tmp.cleanup()
 def headers(self,stage='child.request'):return {'X-NPC-Ownership-Guard':base64.b64encode(json.dumps(self.guard).encode()).decode(),'X-NPC-Stage-Request-Id':stage}
 def test_child_save_guard_and_readback(self):
  after=copy.deepcopy(self.doc);after['characters'][0]['planningCategories'][0]['content']='updated'
  with request_scope(self.headers()):
   result=self.source.save(1,'child request',after);self.assertEqual(result['revision'],2)
  self.assertIsNone(GUARD.get());self.assertEqual(self.source.current()['document'],after)
  records=list((self.data/'.requests').glob('*.json'));owned=[json.loads(p.read_text())for p in records if json.loads(p.read_text()).get('parentRequestId')]
  self.assertEqual(owned[0]['parentRequestId'],'parent.request');self.assertEqual(owned[0]['stageRequestId'],'child.request')
 def test_no_guard_write_denied_read_allowed(self):
  self.assertEqual(self.source.current()['revision'],1)
  with self.assertRaisesRegex(ValueError,'GUARD_REQUIRED'):self.source.save(1,'no guard',self.doc)
 def test_parent_finish_stale_child_rejected(self):
  with request_scope(self.headers()):
   with make_transport(self.config) as t:t.finish()
   with self.assertRaises(ValueError):self.source.save(1,'late child',self.doc)
 def test_pds_http_admission_and_origin_guard(self):
  from project_data_service.http_server import Handler
  from types import SimpleNamespace
  import urllib.request,urllib.error
  http=ThreadingHTTPServer(('127.0.0.1',0),Handler);http.app=SimpleNamespace(domains=SimpleNamespace(npcs=self.source));thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start();url='http://127.0.0.1:'+str(http.server_port)
  try:
   value=json.load(urllib.request.urlopen(url+'/v1/npcs/ownership/generation?npcId=npc'));self.assertEqual(value['generationId'],self.generation)
   p={'npcId':'npc','generationId':self.generation,'requestId':'another.parent'};req=urllib.request.Request(url+'/v1/npcs/ownership/admit',data=json.dumps(p).encode(),headers={'Content-Type':'application/json'})
   guard=json.load(urllib.request.urlopen(req));self.assertEqual(guard['requestId'],'another.parent')
   req=urllib.request.Request(url+'/v1/npcs/ownership/finish',data=json.dumps({'guard':guard}).encode(),headers={'Content-Type':'application/json'});self.assertTrue(json.load(urllib.request.urlopen(req))['released'])
   req=urllib.request.Request(url+'/v1/npcs/ownership/generation?npcId=npc',headers={'Origin':url})
   with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(req)
   self.assertEqual(e.exception.code,403)
  finally:http.shutdown();http.server_close();thread.join()
 def test_independent_process_downloads_only_from_pds(self):
  import subprocess
  from project_data_service.http_server import Handler
  from types import SimpleNamespace
  content=b'\x00\x00\x00\x18ftypmp42-isolated';aid=hashlib.sha256(content).hexdigest()+'.mp4'
  self.writer.put_asset(aid,content,self.guard);key=PREFIX+'npc/animations/idle/run/n.json'
  self.writer.batch('publish.fixture','h',[{'path':key,'expectedHash':None,'body':{'assetId':aid}}],{key:None},self.guard)
  http=ThreadingHTTPServer(('127.0.0.1',0),Handler);http.app=SimpleNamespace(domains=SimpleNamespace(npcs=self.source));thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start();url='http://127.0.0.1:'+str(http.server_port)
  try:
   consumer=self.root/'independent-consumer';consumer.mkdir()
   code="import urllib.request,hashlib,pathlib,sys; b=urllib.request.urlopen(sys.argv[1]).read(); assert hashlib.sha256(b).hexdigest()==sys.argv[2]; pathlib.Path('input.mp4').write_bytes(b); print(len(b))"
   done=subprocess.run([sys.executable,'-c',code,url+'/assets/animations/'+aid,aid[:-4]],cwd=consumer,env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True,check=True)
   self.assertEqual((consumer/'input.mp4').read_bytes(),content);self.assertEqual(int(done.stdout),len(content))
  finally:http.shutdown();http.server_close();thread.join()
 def test_dedicated_create_guard_through_authority(self):
  current=self.source.current()
  with make_transport(self.config) as t:guard=t.prepare_create('newnpc','create.parent',current['sourceVersion'])
  headers={'X-NPC-Ownership-Guard':base64.b64encode(json.dumps(guard).encode()).decode(),'X-NPC-Stage-Request-Id':'create.child'}
  doc=copy.deepcopy(self.doc);doc['characters'].append({'id':'newnpc','name':'new','planningCategories':[]})
  with request_scope(headers):
   result=self.source.save(1,'create fixture',doc);self.assertEqual(result['document']['generationEpoch'],'create.parent')
  self.assertEqual(self.writer.generation('newnpc')['state'],'active');self.assertEqual(self.writer.generation('newnpc')['generationId'],guard['generationId'])
  self.assertEqual(self.writer.receipt('create.parent')['lifecycleOperation'],'create')
 def test_restore_builds_metadata_from_verified_backup(self):
  self.doc['characters'][0]['name']='ready'
  with request_scope(self.headers()):self.source.save(1,'materialize owned slots',self.doc)
  self.writer.finish(self.guard);self.writer.prepare_delete('npc',self.generation,'delete.parent','e'*64)
  snap=self.writer.snapshot();metadata=copy.deepcopy(snap['documents']['metadata.json']);metadata.update(characterOrder=[],generationEpoch='delete.parent');deleted_doc={'characters':[],'generationEpoch':'delete.parent'}
  metadata['_pdsCurrent']={'revision':3,'savedAt':'test','note':'delete','documentHash':digest(deleted_doc)}
  deleted=self.writer.delete_character('delete.parent','d','npc',snap['sourceVersion'],metadata,{'generationId':self.generation})
  current=self.source.current()
  with make_transport(self.config) as t:guard=t.prepare_restore('npc','restore.parent',deleted['backupId'],current['sourceVersion'])
  headers={'X-NPC-Ownership-Guard':base64.b64encode(json.dumps(guard).encode()).decode()}
  with request_scope(headers):
   result=self.source.restore_character('restore.parent','restore.input',deleted['backupId'],3)
   self.assertEqual(result,self.source.restore_character('restore.parent','restore.input',deleted['backupId'],3))
  self.assertEqual(self.source.current()['revision'],4);self.assertEqual(self.source.current()['document']['characters'],self.doc['characters']);self.assertNotEqual(guard['generationId'],self.generation)
 def test_guard_parsing_and_context_reset(self):
  with self.assertRaises(ValueError):
   with request_scope({'X-NPC-Ownership-Guard':'bad'}):pass
  with request_scope(self.headers()):self.assertEqual(GUARD.get()['npcId'],'npc')
  self.assertIsNone(GUARD.get())
if __name__=='__main__':unittest.main()
