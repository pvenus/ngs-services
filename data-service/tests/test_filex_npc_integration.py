import pathlib,tempfile,threading,unittest,sys
from http.server import ThreadingHTTPServer
from project_data_service.filex_npc_store import FilexNPCStore,NPCTransport
from project_data_service.filex_npc_files import encode
from project_data_service.filex_read import canonical
ROOT=pathlib.Path(__file__).resolve().parents[2]/'filex-writer'
sys.path.insert(0,str(ROOT))
# Share one canonical writer module identity with the owned fixtures. Replacing
# sys.modules['server'] during discovery splits Conflict classes across tests.
import server
import npc_store as npc
class Integration(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name).resolve();self.data=self.root/'npcs';self.data.mkdir()
  token=self.root/'token';token.write_text('isolated-test-token-'+'x'*32);token.chmod(0o600)
  self.http=ThreadingHTTPServer(('127.0.0.1',0),server.handler(npc.NPCStore(self.data),token.read_text()));self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
  self.config={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','scope':'npc','enabled':True,'endpoint':'http://127.0.0.1:'+str(self.http.server_port),'tokenFile':str(token)}
  self.adapter=FilexNPCStore(lambda:NPCTransport(self.config))
  self.doc={'projectId':'ProjectBS','characters':[{'id':'character.test','name':'격리 시험','role':'별감','planningCategories':[{'id':'art','content':''}]}]}
 def tearDown(self):self.http.shutdown();self.http.server_close();self.thread.join();self.temp.cleanup()
 def register(self):
  files=encode(self.doc);return self.adapter.apply('test.register','input.register',before={},after=files,expected_hashes={k:None for k in files})
 def test_binary_asset_and_delete_http_contract(self):
  import hashlib
  from project_data_service.filex_npc_authority import FilexCurrentNPC
  data=b'\x89PNG\r\n\x1a\nfixture';aid=hashlib.sha256(data).hexdigest()+'.png'
  with NPCTransport(self.config) as t:
   t.asset_put(aid,data);self.assertEqual(t.asset_get(aid),data)
  self.doc['characters'][0]['portraits']=[{'assetId':aid}]
  files=encode(self.doc);files['metadata.json']['_pdsCurrent']={'revision':1,'savedAt':'test','note':'test'}
  self.adapter.apply('test.register','input.register',before={},after=files,expected_hashes={k:None for k in files})
  auth=FilexCurrentNPC(self.adapter)
  result=auth.delete_character('delete.http','delete.input','character.test',1)
  self.assertTrue(result['deletionReceipt']['backupVerified'])
  from project_data_service.filex_read import digest
  self.assertEqual(auth.current()['document']['generationEpoch'],'delete.http')
  self.assertEqual(result['documentHash'],digest(auth.current()['document']))
  self.assertEqual(auth.current()['document']['characters'],[])
  self.assertEqual(auth.asset_get(aid),data)
  self.assertEqual(auth.delete_character('delete.http','delete.input','character.test',1),result)
 def test_register_and_edit_only_changed(self):
  self.assertEqual(self.register()['changedFiles'],3);self.assertEqual(self.adapter.current()['document'],self.doc)
  s=self.adapter.snapshot();after=encode(self.doc);p='character.test/planning/art.json';after[p]['content']='완성된 전체 파일'
  original=(self.data/'character.test/metadata.json').stat().st_mtime_ns
  args=dict(before=s['documents'],after=after,expected_hashes=s['hashes'])
  self.assertEqual(self.adapter.apply('test.edit','input.edit',**args)['changedFiles'],1)
  self.assertEqual(self.adapter.apply('test.edit','input.edit',**args)['changedFiles'],1)
  self.assertEqual(original,(self.data/'character.test/metadata.json').stat().st_mtime_ns)
  self.assertFalse((self.data/'versions').exists());self.assertFalse((self.data/'.stage').exists())
 def test_stale_worker_read_set(self):
  self.register();s=self.adapter.snapshot();after=encode(self.doc);after['character.test/planning/art.json']['content']='late'
  (self.data/'metadata.json').write_bytes(canonical({'characterOrder':[],'projectId':'changed'}))
  with self.assertRaisesRegex(ValueError,'SOURCE_CHANGED'):self.adapter.apply('late','late',before=s['documents'],after=after,expected_hashes=s['hashes'])
 def test_no_knowledge_authority_reuse(self):
  del self.config['scope']
  with self.assertRaisesRegex(ValueError,'EXPLICIT_TRANSPORT_SCOPE'):NPCTransport(self.config)
if __name__=='__main__':unittest.main()
