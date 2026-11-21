import copy,tempfile,unittest
from pathlib import Path
from project_data_service.knowledge_requests import RequestJournal,digest
from project_data_service.concept_requests import ConceptRequests
class Source:
 def __init__(self):self.value={'revision':1,'document':{'sections':[{'id':'art','fields':[{'id':'rules','label':'규칙','value':'old'},{'id':'other','label':'다른항목','value':'unchanged'}]}]}};self.writes=0;self.receipts={};self.lose=False
 def current(self):return copy.deepcopy(self.value)
 def recover(self,key,h):return self.receipts.get(key)
 def apply(self,r,h,doc,selection):
  self.writes+=1;self.value={'revision':2,'document':doc};self.receipts[r['requestId']]={'requestId':r['requestId'],'inputHash':h,'documentHash':digest(doc),'verified':True}
  if self.lose:raise OSError('lost response')
  return self.receipts[r['requestId']]
class Test(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.j=RequestJournal(Path(self.tmp.name)/'requests.sqlite');self.source=Source();self.calls=[0,0];self.score=.99
  self.s=ConceptRequests(self.j,self.source,self.author,self.select)
  self.r={'schemaVersion':'ngs.concept-modify-request.v1','requestId':'test-request','projectId':'binari','intent':'clean rules','targets':[{'categoryId':'art','itemId':'rules'}],'expectedRevision':1,'documentHash':digest(self.source.value['document'])}
 def tearDown(self):self.j.close();self.tmp.cleanup()
 def select(self,r):
  self.calls[0]+=1
  return {'requestHash':digest(r),'selectionQuality':{'score':self.score,'threshold':.8,'passed':self.score>=.8},'stages':[{'id':'concept.modify','workerId':'concept-modify'}],'invocations':[{}]}
 def author(self,r,fields):
  self.calls[1]+=1;return {'fields':[{'categoryId':'art','itemId':'rules','value':'new'}]},{'modelCalls':1}
 def test_single_preserves_unrelated_and_replay(self):
  out=self.s.submit(self.r);self.assertEqual(out['state'],'completed');self.assertEqual(self.calls,[1,1]);self.assertEqual(self.source.writes,1)
  self.assertEqual(self.source.value['document']['sections'][0]['fields'][1]['value'],'unchanged');self.s.submit(self.r);self.assertEqual(self.calls,[1,1])
 def test_low_score_no_worker(self):
  self.score=.1
  with self.assertRaisesRegex(ValueError,'SELECTION_REJECTED'):self.s.submit(self.r)
  self.assertEqual(self.calls,[1,0]);self.assertEqual(self.source.writes,0)
 def test_conflict_no_model(self):
  self.source.value['revision']=2
  with self.assertRaisesRegex(ValueError,'CONFLICT'):self.s.submit(self.r)
  self.assertEqual(self.calls,[0,0])
 def test_unknown_selection_no_recall(self):
  def lose(r):raise OSError('unknown')
  self.s.selector=lose
  with self.assertRaises(OSError):self.s.submit(self.r)
  with self.assertRaisesRegex(ValueError,'SELECTION_OUTCOME_UNKNOWN'):self.s.resume(self.r['requestId'],digest(self.r))
 def test_response_loss_recovers_no_model(self):
  self.source.lose=True
  with self.assertRaises(OSError):self.s.submit(self.r)
  out=self.s.resume(self.r['requestId'],digest(self.r));self.assertEqual(out['state'],'completed');self.assertEqual(self.calls,[1,1]);self.assertEqual(self.source.writes,1)
 def test_changed_target_rejected(self):
  self.s.authorer=lambda *a:({'fields':[{'categoryId':'art','itemId':'other','value':'bad'}]}, {})
  with self.assertRaisesRegex(ValueError,'TARGET_INVALID'):self.s.submit(self.r)
  self.assertEqual(self.source.writes,0)
 def test_changed_input_rejected(self):
  self.s.submit(self.r)
  with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):self.s.submit({**self.r,'intent':'other'})
if __name__=='__main__':unittest.main()
