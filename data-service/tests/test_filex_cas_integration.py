import importlib.util,pathlib,tempfile,threading,unittest,json
from http.server import ThreadingHTTPServer
from project_data_service.filex_cas_transport import FilexCASTransport
from project_data_service.filex_knowledge_writer import FilexKnowledgeWriter
from project_data_service.filex_read import canonical
source=pathlib.Path(__file__).resolve().parents[2]/'filex-writer/server.py'
spec=importlib.util.spec_from_file_location('ngs_filex_cas_test',source);server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)
class Integration(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.temp.name).resolve();(self.root/'categories/workflow/entries').mkdir(parents=True)
  for path,doc in {'overview.json':{'revision':1},'categories/workflow/metadata.json':{'id':'workflow'},'categories/workflow/entries/old.json':{'id':'old','categoryId':'workflow','version':1,'content':'original','updatedAt':'before','status':'active'}}.items():(self.root/path).write_bytes(canonical(doc))
  token=self.root/'token';token.write_text('test-only-token-'+'x'*32);token.chmod(0o600)
  self.http=ThreadingHTTPServer(('127.0.0.1',0),server.handler(server.Store(self.root),token.read_text()));self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
  config={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','enabled':True,'endpoint':'http://127.0.0.1:'+str(self.http.server_port),'tokenFile':str(token)}
  self.writer=FilexKnowledgeWriter(lambda:FilexCASTransport(config));self.config=config
 def tearDown(self):self.http.shutdown();self.http.server_close();self.thread.join();self.temp.cleanup()
 def request(self):return {'projectId':'binari','requestId':'request.test','operation':'update','entryId':'old','expectedVersion':1,'note':'replace complete body'}
 def test_real_http_atomic_single_file(self):
  w=self.writer;s=w.snapshot();r=self.request();body={**s['documents']['categories/workflow/entries/old.json'],'content':'replacement','version':2,'updatedAt':'now'}
  result=w.apply(r,s['sourceVersion'],{},files=[{'path':'categories/workflow/entries/old.json','body':body,'expectedHash':s['hashes']['categories/workflow/entries/old.json']}]);self.assertTrue(result['verified']);self.assertEqual(w.recover(r['requestId'],result['inputHash']),result)
  self.assertEqual(json.loads((self.root/'categories/workflow/entries/old.json').read_bytes()),body);self.assertFalse((self.root/'versions').exists());self.assertFalse(list(self.root.rglob('.pending-*')))
 def test_wrong_token_fails_before_write(self):
  self.config={**self.config,'tokenFile':str(self.root/'bad-token')};(self.root/'bad-token').write_text('wrong-token-'+'y'*32);(self.root/'bad-token').chmod(0o600)
  with self.assertRaisesRegex(ValueError,'HTTP_401'):FilexKnowledgeWriter(lambda:FilexCASTransport(self.config)).require_write_capability()
 def test_stale_hash_conflict_http(self):
  w=self.writer;s=w.snapshot();r=self.request();body={**s['documents']['categories/workflow/entries/old.json'],'version':2,'updatedAt':'now'}
  (self.root/'overview.json').write_bytes(canonical({'revision':2}))
  with self.assertRaisesRegex(ValueError,'SOURCE_CHANGED'):w.apply(r,s['sourceVersion'],{},files=[{'path':'categories/workflow/entries/old.json','body':body,'expectedHash':s['hashes']['categories/workflow/entries/old.json']}])
 def test_reader_writer_share_current_http_files(self):
  from project_data_service.filex_knowledge import FilexKnowledge
  reader=FilexKnowledge(lambda:FilexCASTransport(self.config));before=reader.entry('old');untouched=(self.root/'overview.json').read_bytes()
  snapshot=self.writer.snapshot();body={**snapshot['documents']['categories/workflow/entries/old.json'],'content':'new current only','version':2,'updatedAt':'now'}
  self.writer.apply(self.request(),snapshot['sourceVersion'],{},files=[{'path':'categories/workflow/entries/old.json','body':body,'expectedHash':snapshot['hashes']['categories/workflow/entries/old.json']}])
  after=reader.entry('old');self.assertEqual(after['content'],'new current only');self.assertNotEqual(after['filexSource']['sourceVersion'],before['filexSource']['sourceVersion']);self.assertEqual((self.root/'overview.json').read_bytes(),untouched);self.assertEqual(reader.category('workflow')['entries'][0]['version'],2)
  self.assertFalse((self.root/'versions').exists());self.assertEqual(len(list((self.root/'categories/workflow/entries').glob('*.json'))),1)
 def test_formal_single_result_update_roundtrip(self):
  from project_data_service.knowledge_requests import KnowledgeRequests,RequestJournal
  from project_data_service.filex_knowledge import FilexKnowledge
  from laya_service.registry import load_registry
  from laya_service.service import LayaSelectionService
  from laya_service.one_result_policy import update_request
  from laya_service.knowledge_source import PdsKnowledgeSource
  registry=load_registry();entry={'id':'laya-worker-registry','categoryId':'workflow','type':'worker-registry','title':'Registry','purpose':'Routing','content':'Registered workers','triggers':[],'maxReadChars':50000,'version':1,'updatedAt':'before','status':'active','authority':'authoritative','data':registry}
  (self.root/'categories/workflow/entries/laya-worker-registry.json').write_bytes(canonical(entry))
  reader=FilexKnowledge(lambda:FilexCASTransport(self.config))
  source=PdsKnowledgeSource('http://127.0.0.1:8030',lambda _:reader.laya_catalog())
  def planner(*args):return {'stageIds':['knowledge.update'],'invocationStageIds':['knowledge.update'],'variationIds':['none'],'score':1,'reason':'explicit single result policy'}, {'modelCalls':0,'fixture':True}
  laya=LayaSelectionService(None,workflow_planner=planner,knowledge_source=source)
  journal=RequestJournal(self.root/'requests.sqlite');service=KnowledgeRequests(journal,lambda r:laya.select_workflow({'mode':'single','requestType':'knowledge.update','request':r}),content=self.writer)
  try:
   request=update_request(entry,'single-result-http-test');result=service.submit(request)
   self.assertTrue(result['verified']);new=reader.entry('laya-worker-registry');self.assertEqual(new['version'],2);self.assertFalse(any(a['outputPolicy']['kind']=='comparison'for a in new['data']['actions']));self.assertEqual({k:v for k,v in source()[0].items()if k!='knowledgeGuidance'},new['data']);self.assertEqual(service.view(request['requestId'])['state'],'completed')
   self.assertEqual(reader.entry('old')['content'],'original');self.assertFalse((self.root/'versions').exists())
  finally:journal.close()
 def test_formal_new_knowledge_changes_laya_routing(self):
  from project_data_service.knowledge_requests import KnowledgeRequests,RequestJournal
  from project_data_service.filex_knowledge import FilexKnowledge
  from laya_service.knowledge_bootstrap import minimal_registry
  from laya_service.knowledge_source import PdsKnowledgeSource
  from laya_service.service import LayaSelectionService
  core={'id':'laya-worker-registry','categoryId':'workflow','type':'worker-registry','title':'Core','purpose':'Routing','content':'Core','triggers':[],'version':1,'status':'active','authority':'authoritative','data':minimal_registry()}
  (self.root/'categories/workflow/entries/laya-worker-registry.json').write_bytes(canonical(core))
  reader=FilexKnowledge(lambda:FilexCASTransport(self.config));source=PdsKnowledgeSource('http://127.0.0.1:8030',lambda _:reader.laya_catalog())
  seen=[]
  def planner(request,registry,targets,mode):
   seen.append(registry);return {'stageIds':[targets[0]],'invocationStageIds':[targets[0]],'variationIds':['none'],'score':1,'reason':'registered guide'}, {'modelCalls':0,'fixture':True}
  laya=LayaSelectionService(None,workflow_planner=planner,knowledge_source=source)
  with self.assertRaises(ValueError):laya.route_request({'requestType':'knowledge.review-update'})
  journal=RequestJournal(self.root/'guide-requests.sqlite');service=KnowledgeRequests(journal,lambda r:laya.select_workflow({'mode':'single','requestType':'knowledge.create','request':r}),content=self.writer)
  try:
   request={'schemaVersion':'ngs.knowledge-request.v1','requestId':'new-workflow-guide','projectId':'binari','operation':'create','entryId':'update-guide','expectedVersion':0,'note':'register user workflow','entry':{'id':'update-guide','categoryId':'workflow','type':'workflow','title':'검토 갱신','purpose':'선택 연결','content':'검토된 지식을 갱신','status':'active','authority':'authoritative','triggers':['검토 갱신'],'data':{'routing':{'requestType':'knowledge.review-update','stageIds':['knowledge.update']}}}}
   import copy
   invalid=copy.deepcopy(request);invalid['requestId']='unsupported-worker-guide';invalid['entryId']='invalid-guide';invalid['entry']['id']='invalid-guide';invalid['entry']['data']['routing']['stageIds']=['arbitrary-shell-command']
   with self.assertRaisesRegex(ValueError,'EXECUTABLE_STAGE_UNKNOWN'):service.submit(invalid)
   self.assertIsNone(reader.entry('invalid-guide'))
   service.submit(request)
   result=laya.select_workflow({'mode':'single','requestType':'knowledge.review-update','request':{'schemaVersion':'test','intent':'검토 갱신'}})
   self.assertEqual(result['stages'][0]['id'],'knowledge.update');self.assertEqual(seen[-1]['knowledgeGuidance'][0]['entryId'],'update-guide');self.assertTrue(any(x['entryId']=='update-guide'for x in result['knowledgeSource']['sources']))
  finally:journal.close()
if __name__=='__main__':unittest.main()
