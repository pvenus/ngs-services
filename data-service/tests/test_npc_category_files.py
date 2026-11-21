import copy, json, sqlite3, threading, urllib.request, urllib.error, unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from test_filex_npc_integration import Integration
from project_data_service.filex_npc_authority import FilexCurrentNPC,migration_files
from project_data_service.npc_category_files import CategoryFiles,encoded,header,parse_header
from project_data_service.npc_ownership import GUARD,make_transport
from project_data_service.filex_npc_store import FilexNPCStore
from fenced_store import FencedOwnedNPCStore
import server
from project_data_service.domain_contracts.legacy_http import NPC_TEXT_FIELDS,NPC_TRAIT_GROUPS
from project_data_service.domain_contracts.profile_model import blank_profile
from project_data_service.http_server import Handler

class Files(unittest.TestCase):
 def setUp(self):
  Integration.setUp(self)
  n={k:'' for k in NPC_TEXT_FIELDS};n.update(id='character.test',name='test',role='villager',status='proposal',chapters=['chapter01'],sourceRefs=[],traits={g:dict.fromkeys(keys)for g,keys in NPC_TRAIT_GROUPS.items()},numericProfile=blank_profile(),portraits=[],activePortraitId=None,planningCategories=[{'id':'art','uuid':'art','name':'Art','content':'old','unknown':'preserve'}])
  self.doc={'schemaVersion':1,'projectId':'ProjectBS','actId':'act01','masterRevision':1,'storyRevision':1,'characters':[n]}
  files=migration_files({'revision':1,'savedAt':'fixture','note':'fixture','document':self.doc})
  self.adapter.apply('seed','seed',before={},after=files,expected_hashes={k:None for k in files})
  self.http.shutdown();self.http.server_close();self.thread.join()
  writer=FencedOwnedNPCStore(self.data);generation=writer.bootstrap('character.test',writer.snapshot()['sourceVersion'])['generationId']
  self.http=ThreadingHTTPServer(('127.0.0.1',0),server.handler(writer,(self.root/'token').read_text()));self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
  self.config.update(ownershipMode='npc-owned-v1',endpoint='http://127.0.0.1:'+str(self.http.server_port))
  self.adapter=FilexNPCStore(lambda:make_transport(self.config))
  with make_transport(self.config) as t:self.guard=t.admit('character.test',generation,'request.test')
  self.db=sqlite3.connect(':memory:',check_same_thread=False);self.db.execute('CREATE TABLE npc_category_apply_receipts(project_id TEXT,operation_hash TEXT,receipt TEXT,PRIMARY KEY(project_id,operation_hash))')
  self.domains=SimpleNamespace(lock=threading.RLock(),npcs=FilexCurrentNPC(self.adapter),npc_receipts=SimpleNamespace(connection=self.db))
  self.service=CategoryFiles(self.domains)
  self.token=GUARD.set(self.guard)
 def tearDown(self):GUARD.reset(self.token);self.db.close();Integration.tearDown(self)
 def packet(self,cat='art',mode='modify'):
  raw,b=self.service.download('character.test',cat,1,mode)
  p={'projectId':'ProjectBS','expectedRevision':1,'npcId':'character.test','categoryId':cat,'mode':mode,'operationHash':'c'*64,'metadata':{'inputHash':'d'*64,'categoryId':cat},'basis':b,'note':'NPC category '+mode}
  return p,json.loads(raw)
 def test_replace_only_target_and_receipt_recovery(self):
  p,b=self.packet();b['content']='updated';before=self.adapter.snapshot()['hashes'];r=self.service.replace(p,encoded(b))
  after=self.adapter.snapshot()['hashes'];self.assertEqual({k for k in before if before[k]!=after[k]},{'metadata.json','character.test/planning/art.json'})
  self.assertEqual(r['revision'],2);self.db.execute('DELETE FROM npc_category_apply_receipts');self.db.commit()
  self.assertEqual(self.service.replace(p,encoded(b)),r)
  self.assertEqual(self.domains.npcs.current()['document']['characters'][0]['planningCategories'][0]['unknown'],'preserve')
  b['content']='different'
  with self.assertRaisesRegex(ValueError,'PAYLOAD_CONFLICT'):self.service.replace(p,encoded(b))
 def test_scope_stale_and_wrong_owner(self):
  p,b=self.packet();b['id']='other'
  with self.assertRaisesRegex(ValueError,'SCOPE_CHANGED'):self.service.replace(p,encoded(b))
  p,b=self.packet();p['basis']['fileHash']='0'*64
  with self.assertRaisesRegex(ValueError,'REVISION_CONFLICT'):self.service.replace(p,encoded(b))
  GUARD.set({**self.guard,'npcId':'other'})
  with self.assertRaisesRegex(ValueError,'OWNER_REQUIRED'):self.service.download('character.test','art',1)
 def test_add_and_native(self):
  p,b=self.packet('category.new','add');b.update(name='New',content='new body');self.service.replace(p,encoded(b))
  d=self.domains.npcs.current()['document'];self.assertEqual(d['characters'][0]['planningCategories'][-1],b)
  raw,basis=self.service.download('character.test','field:role',2)
  b=json.loads(raw);b['role']='new role';p.update(expectedRevision=2,categoryId='field:role',mode='modify',operationHash='e'*64,basis=basis)
  p['metadata']['categoryId']='field:role';self.service.replace(p,encoded(b))
  self.assertEqual(self.domains.npcs.current()['document']['characters'][0]['role'],'new role')
 def test_http_download_and_raw_replace(self):
  h=ThreadingHTTPServer(('127.0.0.1',0),Handler);h.app=SimpleNamespace(domains=self.domains);thread=threading.Thread(target=h.serve_forever,daemon=True);thread.start()
  try:
   base='http://127.0.0.1:'+str(h.server_port);heads={'X-NPC-Ownership-Guard':header(self.guard)}
   with urllib.request.urlopen(urllib.request.Request(base+'/v1/npc-category/file?npcId=character.test&categoryId=art&expectedRevision=1',headers=heads)) as r:
    raw=r.read();basis=parse_header(r.headers['X-NPC-File-Basis'])
   p,b=self.packet();p['basis']=basis;b['content']='http changed'
   req=urllib.request.Request(base+'/v1/npc-category/file-replace',data=encoded(b),headers={**heads,'Content-Type':'application/octet-stream','X-NPC-File-Replace':header(p)})
   with urllib.request.urlopen(req) as r:self.assertEqual(json.load(r)['revision'],2)
   req=urllib.request.Request(base+'/v1/npc-category/file?npcId=character.test&categoryId=art&expectedRevision=2')
   with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(req)
  finally:h.shutdown();h.server_close();thread.join()

 def test_expired_lease_cannot_replace(self):
  p,b=self.packet();b['content']='late'
  with make_transport(self.config) as t:t.finish()
  with self.assertRaises(ValueError):self.service.replace(p,encoded(b))
  self.assertEqual(self.domains.npcs.current()['revision'],1)

 def test_null_key_injection_and_stage_binding(self):
  p,b=self.packet();b['unrelated']=None
  with self.assertRaisesRegex(ValueError,'SCOPE_CHANGED'):self.service.replace(p,encoded(b))
  p,b=self.packet();p['basis']['stageRequestId']='other.stage'
  with self.assertRaisesRegex(ValueError,'BASIS_INVALID'):self.service.replace(p,encoded(b))
  self.assertEqual(self.domains.npcs.current()['revision'],1)

 def test_runtime_transport_against_real_pds_filex(self):
  import os,sys
  from unittest.mock import patch
  root=os.environ.get('NGS_RUNTIME_SOURCE')
  if not root:self.skipTest('set NGS_RUNTIME_SOURCE for cross-repository integration')
  sys.path.insert(0,root)
  from local.services.npc_category.source import PDSNpcSource
  from local.services.npc_ownership import _CURRENT,stage
  self.domains.category_receipt=lambda op: self._lookup(op)
  h=ThreadingHTTPServer(('127.0.0.1',0),Handler);h.app=SimpleNamespace(domains=self.domains);thread=threading.Thread(target=h.serve_forever,daemon=True);thread.start()
  base='http://127.0.0.1:'+str(h.server_port)
  class LocalRequest(urllib.request.Request):
   def __init__(self,url,*a,**kw):super().__init__(url.replace('http://127.0.0.1:8030',base,1),*a,**kw)
  token=_CURRENT.set(self.guard)
  try:
   with patch('urllib.request.Request',LocalRequest),stage('planning.stage'):
    adapter=PDSNpcSource('ProjectBS').adapter()
    artifact=adapter.prepare_file('ProjectBS','character.test',1,'modify','art','requesthash')
    proposal={'categoryName':'Art','content':'runtime authored'}
    raw=json.loads(artifact['content']);raw['content']=proposal['content'];artifact['replacementContent']=encoded(raw).decode()
    r=adapter.apply_file('ProjectBS','character.test',1,'modify','art',proposal,'f'*64,artifact)
    self.assertEqual(r['diff']['before']['content'],'old');self.assertEqual(r['diff']['after']['content'],'runtime authored')
    self.assertEqual(r,adapter.apply_file('ProjectBS','character.test',1,'modify','art',proposal,'f'*64,artifact))
    native=adapter.prepare_file('ProjectBS','character.test',2,'modify','field:role','nativehash')
    r=adapter.apply_file('ProjectBS','character.test',2,'modify','field:role',{'categoryName':'역할','content':'상인'},'9'*64,native)
    self.assertEqual(r['diff']['after']['sourceField'],'role');self.assertEqual(r['diff']['after']['content'],'상인')
  finally:_CURRENT.reset(token);h.shutdown();h.server_close();thread.join();sys.path.remove(root)
 def _lookup(self,op):
  row=self.db.execute('SELECT receipt FROM npc_category_apply_receipts WHERE operation_hash=?',(op,)).fetchone()
  return json.loads(row[0]) if row else None

 def test_opt_in_actual_cli_to_owned_file_replace(self):
  import os,sys
  from unittest.mock import patch
  root=os.environ.get('NGS_RUNTIME_SOURCE')
  if not root or os.environ.get('NGS_ACTUAL_CLI_E2E')!='1':self.skipTest('explicit opt-in actual CLI fixture only')
  sys.path.insert(0,root)
  from local.services.npc_category.source import PDSNpcSource
  from local.services.npc_ownership import _CURRENT,stage
  from local.services.npc_workflow.stages import OnePlanningGenerator
  from local.services.request_pipeline.common import digest,canonical
  self.domains.category_receipt=lambda op:self._lookup(op)
  h=ThreadingHTTPServer(('127.0.0.1',0),Handler);h.app=SimpleNamespace(domains=self.domains);thread=threading.Thread(target=h.serve_forever,daemon=True);thread.start()
  base='http://127.0.0.1:'+str(h.server_port)
  class LocalRequest(urllib.request.Request):
   def __init__(self,url,*a,**kw):super().__init__(url.replace('http://127.0.0.1:8030',base,1),*a,**kw)
  token=_CURRENT.set(self.guard)
  try:
   with patch('urllib.request.Request',LocalRequest),stage('cli.planning.stage'):
    adapter=PDSNpcSource('ProjectBS').adapter()
    artifact=adapter.prepare_file('ProjectBS','character.test',1,'modify','art','cli-fixture')
    request={'mode':'modify','intent':'Art 이름과 기존 unknown 필드는 그대로 보존하고 content만 정확히 파란 모자를 쓴 마을 상인 으로 수정하세요. 모든 응답은 짧게 작성하세요.','npcId':'character.test'}
    folder=self.root/'cli-fixture';folder.mkdir(mode=0o700)
    generator=OnePlanningGenerator(folder,'cli.fixture')
    record={'promptVersion':5,'requestHash':digest(request),'model':{'intent':request['intent'],'commonPlanning':{},'targetCategory':json.loads(artifact['content']),'characterBasicInfo':{'role':'villager'},'targetFile':{'path':artifact['basis']['path'],'content':json.loads(artifact['content'])}},'trace':{'fixtureOnly':True}}
    (folder/(digest('cli.fixture')+'.context.json')).write_text(canonical(record))
    try:proposal=generator({'request':request,'targetFile':artifact})[0]
    except ValueError:
     attempt=generator.session.root/generator.session.attempt_id
     for name in ('terminal.json','stderr.log','events.jsonl'):
      path=attempt/name
      if path.exists():print('CLI_DIAGNOSTIC '+name+' '+path.read_text()[-3000:])
     raise
    self.assertEqual(generator.model_calls,1)
    artifact['replacementContent']=generator.last_file_content
    r=adapter.apply_file('ProjectBS','character.test',1,'modify','art',proposal,'8'*64,artifact)
    self.assertEqual(r['diff']['after']['content'],proposal['content']);self.assertEqual(r['diff']['after']['unknown'],'preserve')
    self.assertEqual(r['revision'],2);self.assertEqual(r['transport'],'pds-file-replace.v1')
    print('ACTUAL_CLI_FILE_E2E_PASS modelCalls=1 revision=2 unchangedUnknown=true')
  finally:_CURRENT.reset(token);h.shutdown();h.server_close();thread.join();sys.path.remove(root)
