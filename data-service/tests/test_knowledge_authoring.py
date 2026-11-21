import copy,unittest
from project_data_service.knowledge_authoring import paragraphs,validate_output,authored_request,author_context
class Tests(unittest.TestCase):
 def request(self):return {'operation':'create','entryId':'guide','requestId':'test-authoring','entry':{'id':'guide','categoryId':'workflow','type':'guide','authority':'supporting','status':'draft'},'authoring':{'description':'첫번째 필수 조건을 지킨다.\n\n두번째 실패 시 중단한다.'}}
 def output(self):return {'title':'가이드','purpose':'작업 연결','content':'조건을 지키고 실패 시 중단한다.','triggers':['작업','실패'],'sections':[{'id':'requirements','heading':'조건','body':'첫번째 필수 조건을 지킨다.'},{'id':'failures','heading':'실패','body':'실패 시 중단한다.'}],'coverage':[{'paragraphId':'p1','sectionIds':['requirements'],'sourceQuote':'필수 조건'},{'paragraphId':'p2','sectionIds':['failures'],'sourceQuote':'실패 시 중단'}]}
 def test_raw_description_and_metadata_preserved(self):
  r=self.request();new,trace=authored_request(r,None,lambda *args:(self.output(),{'modelCalls':1}))
  self.assertEqual(new['entry']['data']['sourceDescription'],r['authoring']['description']);self.assertEqual(new['entry']['authority'],'supporting');self.assertEqual(new['entry']['status'],'draft');self.assertEqual(trace['coveredParagraphs'],2)
 def test_omitted_paragraph_fails(self):
  v=self.output();v['coverage'].pop()
  with self.assertRaisesRegex(ValueError,'COVERAGE_MISSING'):validate_output(v,paragraphs(self.request()['authoring']['description']))
 def test_invented_source_quote_fails(self):
  v=self.output();v['coverage'][0]['sourceQuote']='invented'
  with self.assertRaisesRegex(ValueError,'COVERAGE_INVALID'):validate_output(v,paragraphs(self.request()['authoring']['description']))
 def test_model_cannot_change_identity_or_permissions(self):
  v=self.output();v['authority']='authoritative'
  with self.assertRaisesRegex(ValueError,'OUTPUT_SHAPE'):validate_output(v,paragraphs(self.request()['authoring']['description']))
 def test_large_input_not_truncated(self):
  r=self.request();r['authoring']['description']='가'*30000
  v=self.output();v['coverage']=[{'paragraphId':'p1','sectionIds':['requirements'],'sourceQuote':'가'}]
  result,_=authored_request(r,None,lambda *a:(v,{'modelCalls':1}));self.assertEqual(len(result['entry']['data']['sourceDescription']),30000)

 def test_update_preserves_unrequested_data_and_routing(self):
  r=self.request();r.update(operation='update',changes={'data':{'explicit':'new'}});r.pop('entry')
  current={'data':{'marker':'after-update','testOnly':True,'routing':{'requestType':'old','stageIds':['existing']}}}
  result,_=authored_request(r,current,lambda *a:(self.output(),{'modelCalls':1}))
  data=result['changes']['data'];self.assertEqual(data['marker'],'after-update');self.assertTrue(data['testOnly']);self.assertEqual(data['explicit'],'new');self.assertEqual(data['routing'],current['data']['routing'])
  self.assertNotIn('schemaVersion',current['data'])
 def test_create_retains_declared_data(self):
  r=self.request();r['entry']['data']={'marker':'original'}
  result,_=authored_request(r,None,lambda *a:(self.output(),{}))
  self.assertEqual(result['entry']['data']['marker'],'original')

 def test_context_excludes_envelope_and_duplicate_provenance(self):
  raw={'title':'title','content':'domain content','version':3,'updatedAt':'yesterday','status':'deleted','id':'internal','data':{'weapon':'sword','sourceDescription':'old prompt','coverage':[],'schemaVersion':'internal','sections':[]}}
  v=author_context(raw);self.assertEqual(set(v),{'title','content','data'});self.assertEqual(v['data'],{'weapon':'sword','sections':[]});self.assertEqual(raw['version'],3)
