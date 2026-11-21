import copy,json,tempfile,unittest
from pathlib import Path
from project_data_service.knowledge_requests import KnowledgeRequests,RequestJournal,digest
class FilexFixture:
 def __init__(self):self.documents={'overview.json':{},'categories/test/metadata.json':{'id':'test'},'categories/test/entries/laya-worker-registry.json':{'id':'laya-worker-registry','version':1,'data':{'version':'test'},'status':'active','authority':'authoritative','type':'worker-registry'}};self.receipts={};self.calls=0
 def snapshot(self):return {'sourceVersion':digest(self.documents),'manifestHash':digest(self.documents),'documents':copy.deepcopy(self.documents),'hashes':{p:digest(v)for p,v in self.documents.items()}}
 def recover(self,key,h):
  old=self.receipts.get(key)
  if old and old['inputHash']!=h:raise ValueError('FILEX_IDEMPOTENCY_CONFLICT')
  return old
 def apply(self,r,version,selection,*,files):
  if version!=digest(self.documents):raise ValueError('FILEX_CAS_CONFLICT')
  self.calls+=1;path=files[0]['path'];old=self.documents.get(path)
  entry=copy.deepcopy(files[0]['body'])
  entry['version']=r['expectedVersion']+1;self.documents[path]=entry
  result={'requestId':r['requestId'],'inputHash':digest(r),'sourceVersion':digest(self.documents),'manifestHash':digest(self.documents),'entry':entry,'verified':True,'fileHash':digest(entry)};self.receipts[r['requestId']]=result;return result
class TestKnowledgeRequests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=RequestJournal(Path(self.tmp.name)/'state.sqlite');self.content=FilexFixture();self.calls=0;self.score=1
  self.s=KnowledgeRequests(self.store,self.selection,content=self.content)
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def selection(self,r):
  self.calls+=1;s=self.content.snapshot();registry=s['documents']['categories/test/entries/laya-worker-registry.json']
  return {'requestHash':digest(r),'registryHash':digest(registry['data']),'knowledgeSource':{'entryVersion':registry['version'],'sourceVersion':s['sourceVersion']},'selectionQuality':{'score':self.score,'threshold':.8,'passed':self.score>=.8},'stages':[{'id':'knowledge.'+r['operation'],'workerId':'knowledge-mutation'}],'invocations':[{}]}
 def request(self):return {'schemaVersion':'ngs.knowledge-request.v1','requestId':'request-test-1','projectId':'binari','operation':'create','entryId':'new-entry','expectedVersion':0,'note':'test creation','entry':{'id':'new-entry','categoryId':'test','type':'reference','title':'Test','purpose':'Test'}}
 def test_explicit_preselection_source_failure_can_resume_same_id(self):
  r=self.request()
  def unavailable(_):raise ValueError('KNOWLEDGE_LAYA_REJECTED: {"error":"LAYA_KNOWLEDGE_UNAVAILABLE","details":null}')
  self.s.selector=unavailable
  with self.assertRaises(ValueError):self.s.submit(r)
  self.assertTrue(self.s.view(r['requestId'])['resumeAvailable']);self.assertEqual(self.calls,0)
  self.s.selector=self.selection;self.s.resume(r['requestId'],digest(r))
  self.assertEqual(self.calls,1);self.assertEqual(self.content.calls,1)
 def test_unknown_selection_failure_not_resumable(self):
  r=self.request()
  def unknown(_):raise ValueError('connection lost after possible model call')
  self.s.selector=unknown
  with self.assertRaises(ValueError):self.s.submit(r)
  self.assertFalse(self.s.view(r['requestId'])['resumeAvailable'])
  with self.assertRaisesRegex(ValueError,'OUTCOME_UNKNOWN'):self.s.resume(r['requestId'],digest(r))
 def test_create_replay(self):
  r=self.request();a=self.s.submit(r);self.assertEqual(a,self.s.submit(r));self.assertEqual(self.calls,1);self.assertEqual(self.content.calls,1)
 def test_low_score_no_write(self):
  self.score=.2
  with self.assertRaisesRegex(ValueError,'SELECTION_REJECTED'):self.s.submit(self.request())
  self.assertNotIn('categories/test/entries/new-entry.json',self.content.documents);self.assertEqual(self.s.view('request-test-1')['state'],'failed')
 def test_stale_rejected(self):
  r=self.request();r.update(operation='update',entryId='laya-worker-registry',expectedVersion=9,changes={'title':'other'});r.pop('entry')
  with self.assertRaisesRegex(ValueError,'VERSION_CONFLICT'):self.s.submit(r)
  self.assertEqual(self.calls,0)
 def test_registry_delete_blocked(self):
  r=self.request();r.update(operation='delete',entryId='laya-worker-registry',expectedVersion=1);r.pop('entry')
  with self.assertRaisesRegex(ValueError,'BOOTSTRAP_DEPENDENCY'):self.s.submit(r)
 def test_request_collision(self):
  r=self.request();self.s.submit(r);r['note']='different request'
  with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):self.s.submit(r)
 def test_delete_retains_body(self):
  self.s.submit(self.request());r=self.request();r.update(requestId='request-delete',operation='delete',expectedVersion=1);r.pop('entry')
  result=self.s.submit(r);self.assertEqual(result['entry']['status'],'deleted');self.assertEqual(result['entry']['title'],'Test')
 def test_registry_changed_during_selection(self):
  def changed(r):
   value=self.selection(r);self.content.documents['categories/test/entries/laya-worker-registry.json']['version']=2;return value
  self.s.selector=changed
  with self.assertRaisesRegex(ValueError,'SOURCE_CHANGED'):self.s.submit(self.request())
  self.assertEqual(self.content.calls,0)
 def test_resume_saved_selection_does_not_call_model(self):
  r=self.request();selection=self.selection(r)
  with self.store.connection:self.store.connection.execute('INSERT INTO knowledge_requests VALUES(?,?,?,?,?,?,?)',(r['requestId'],digest(r),json.dumps(r),'selecting',json.dumps(selection),None,None))
  self.s.resume(r['requestId'],digest(r));self.assertEqual(self.calls,1);self.assertEqual(self.content.calls,1)
 def test_resume_unknown_does_not_call_model(self):
  r=self.request()
  with self.store.connection:self.store.connection.execute('INSERT INTO knowledge_requests VALUES(?,?,?,?,?,?,?)',(r['requestId'],digest(r),json.dumps(r),'selecting',None,None,None))
  with self.assertRaisesRegex(ValueError,'OUTCOME_UNKNOWN'):self.s.resume(r['requestId'],digest(r))
  self.assertEqual(self.calls,0)
 def test_filex_commit_response_loss_recovered(self):
  r=self.request();apply=self.content.apply
  def lost(*args,**kwargs):apply(*args,**kwargs);raise OSError('response lost')
  self.content.apply=lost
  with self.assertRaises(OSError):self.s.submit(r)
  result=self.s.resume(r['requestId'],digest(r));self.assertEqual(result['entry']['version'],1);self.assertEqual(self.content.calls,1);self.assertEqual(self.calls,1)
 def test_missing_port_no_db_fallback(self):
  self.s.content=None
  with self.assertRaisesRegex(ValueError,'FILEX_PORT'):self.s.submit(self.request())
  self.assertEqual(self.calls,0)
 def test_completed_journal_keeps_metadata_not_body_copy(self):
  r=self.request();self.s.submit(r);saved=self.s.view(r['requestId'])
  self.assertNotIn('entry',saved['result']);self.assertEqual(saved['result']['entryId'],'new-entry')
  self.assertIsNone(self.store.connection.execute('SELECT id FROM knowledge_request_files WHERE id=?',(r['requestId'],)).fetchone())
 def test_authoring_completes_and_replay_does_not_reauthor(self):
  r=self.request();r['authoring']={'description':'완전한 원문을 보존한다.'};r['entry'].pop('title');r['entry'].pop('purpose');calls=[]
  def author(request,current):
   calls.append(request['authoring']['description'])
   return {'title':'모델 제목','purpose':'연결','content':'원문 보존','triggers':['보존'],'sections':[{'id':'s1','heading':'규칙','body':'원문을 보존한다.'}],'coverage':[{'paragraphId':'p1','sectionIds':['s1'],'sourceQuote':'원문'}]}, {'modelCalls':1,'elapsedSeconds':1.2}
  self.s.authorer=author;result=self.s.submit(r);self.assertEqual(result['entry']['title'],'모델 제목');self.assertEqual(result['entry']['data']['sourceDescription'],r['authoring']['description']);self.s.submit(r);self.assertEqual(len(calls),1);self.assertEqual(self.s.view(r['requestId'])['authoringTrace']['coveredParagraphs'],1)
if __name__=='__main__':unittest.main()
