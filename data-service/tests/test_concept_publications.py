import copy,json,tempfile,unittest
from project_data_service.concept_filex import FilexConcept,split
from project_data_service.concept_migration import migrate
from project_data_service.concept import ROOT
from project_data_service.concept_publications import publish,recover_publication,validate_change
from project_data_service.request_primitives import digest
from test_concept_filex import Transport,ConceptStore

class Test(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.store=ConceptStore(self.tmp.name)
  d=json.loads((ROOT/'seed.json').read_text());d.update(projectId='ProjectBS',schemaVersion=2);d['hierarchy']={'schemaVersion':'npc-concept-groups.v1','groups':[{'id':'group.all','title':'All','categoryIds':[s['id']for s in d['sections']]}]}
  self.source=FilexConcept(transport_factory=lambda:Transport(self.store));migrate({'revision':1,'savedAt':'test','note':'init','document':d},lambda:Transport(self.store));self.cid=d['sections'][0]['id'];self.iid=d['sections'][0]['fields'][0]['id']
 def payload(self,action,after,scope=None):
  cur=self.source.current();rid='formal.request.'+str(cur['revision']);ev={'kind':'selected','proposalHash':'a'*64,'selectedId':'one'}
  key=digest({'requestId':rid,'proposalHash':ev['proposalHash'],'selectedId':'one'})
  if action.endswith(('archive','restore')):
   ev={'kind':'deterministic','requestHash':'b'*64,'confirmed':True};key=digest({'requestId':rid,'requestHash':ev['requestHash'],'action':action})
  return {'schemaVersion':'ngs.concept-publication.v1','requestId':rid,'operationHash':key,'action':action,'scope':scope or {'categoryId':self.cid,'itemId':self.iid,'groupId':'group.all','categoryPosition':0},'expectedRevision':cur['revision'],'documentHash':digest(cur['document']),'document':after,'evidence':ev}
 def summary(self):return {'schemaVersion':'npc-concept-summary.v1','keywords':['one'],'sentence':'One result.'}
 def test_add_item_and_replay(self):
  d=copy.deepcopy(self.source.current()['document']);fields=d['sections'][0]['fields'];fields.append({'id':'new.item','label':'New','value':'new','status':'confirmed','sources':[],'priority':len(fields)+1,'summary':self.summary()})
  p=self.payload('item.add',d);r=publish(self.source,p);self.assertEqual(r,publish(self.source,p));self.assertEqual(r,recover_publication(self.source,p));self.assertEqual(self.source.current()['revision'],2);self.assertIn('sections/'+self.cid+'/fields/new.item.json',self.source.snapshot()['documents'])
 def test_add_category_and_summary(self):
  d=copy.deepcopy(self.source.current()['document']);d['sections'].append({'id':'new.category','title':'New','description':'Description','priority':len(d['sections'])+1,'summary':self.summary(),'fields':[{'id':'new.field','label':'New','value':'new','status':'confirmed','sources':[],'priority':1,'summary':self.summary()}]});d['hierarchy']['groups'][0]['categoryIds'].append('new.category')
  publish(self.source,self.payload('category.add',d));d=copy.deepcopy(d);d['sections'][0]['summary']=self.summary();publish(self.source,self.payload('category.summary.modify',d));self.assertEqual(self.source.current()['document'],d)
 def test_archive_preserved_and_legacy_restore_removed(self):
  before=set(self.source.snapshot()['documents']);d=copy.deepcopy(self.source.current()['document']);d['hierarchy']['archivedItems']=[{'categoryId':self.cid,'itemId':self.iid}];publish(self.source,self.payload('item.archive',d));d['hierarchy']['archivedItems']=[]
  with self.assertRaisesRegex(ValueError,'RECOVERY_FEATURE_REMOVED'):publish(self.source,self.payload('item.restore',d))
  self.assertEqual(before,set(self.source.snapshot()['documents']))

 def test_out_of_scope_and_binding(self):
  d=copy.deepcopy(self.source.current()['document']);d['sections'][0]['summary']=self.summary();d['title']='unauthorized';p=self.payload('category.summary.modify',d)
  with self.assertRaisesRegex(ValueError,'OUT_OF_SCOPE'):publish(self.source,p)
  d['title']=self.source.current()['document']['title'];p=self.payload('category.summary.modify',d);p['operationHash']='f'*64
  with self.assertRaisesRegex(ValueError,'BINDING'):publish(self.source,p)
 def test_conflict_and_lost_response(self):
  d=copy.deepcopy(self.source.current()['document']);d['sections'][0]['summary']=self.summary();p=self.payload('category.summary.modify',d)
  class Lost(Transport):
   def call(self,*a):super().call(*a);raise TimeoutError('lost')
  self.source.factory=lambda:Lost(self.store)
  with self.assertRaises(TimeoutError):publish(self.source,p)
  self.source.factory=lambda:Transport(self.store);self.assertEqual(publish(self.source,p),recover_publication(self.source,p));self.assertEqual(self.source.current()['revision'],2)
  stale=copy.deepcopy(p);stale['requestId']='formal.another';stale['operationHash']=digest({'requestId':stale['requestId'],'proposalHash':stale['evidence']['proposalHash'],'selectedId':'one'})
  with self.assertRaisesRegex(ValueError,'SOURCE_CONFLICT'):publish(self.source,stale)
 def test_same_base_second_operation_and_changed_replay_denied(self):
  d=copy.deepcopy(self.source.current()['document']);d['sections'][0]['summary']=self.summary();p=self.payload('category.summary.modify',d)
  second=copy.deepcopy(p);second['requestId']='formal.concurrent';second['operationHash']=digest({'requestId':second['requestId'],'proposalHash':second['evidence']['proposalHash'],'selectedId':'one'})
  publish(self.source,p)
  with self.assertRaisesRegex(ValueError,'SOURCE_CONFLICT'):publish(self.source,second)
  changed=copy.deepcopy(p);changed['document']['sections'][0]['summary']['sentence']='Changed after commit'
  with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):publish(self.source,changed)
  self.assertEqual(self.source.current()['revision'],2)
 def test_direct_modify_cannot_store_invalid_rule_registration(self):
  d=copy.deepcopy(self.source.current()['document']);d['sections'][0]['fields'][0]['value']='{"schemaVersion":"ngs.character-rendering-rules.v1",'
  with self.assertRaisesRegex(ValueError,'MASTER_RENDERING_RULES_JSON'):
   self.source.apply({'requestId':'modify.invalid.rules','expectedRevision':1,'documentHash':digest(self.source.current()['document'])},'input',d,{})
  self.assertEqual(self.source.current()['revision'],1)
 def test_physical_remove_denied(self):
  d=copy.deepcopy(self.source.current()['document']);d['sections'][0]['fields'].pop()
  with self.assertRaises(ValueError):publish(self.source,self.payload('item.archive',d))
if __name__=='__main__':unittest.main()
