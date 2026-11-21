import copy,unittest
from test_filex_knowledge_writer import Memory
from project_data_service.filex_knowledge_writer import FilexKnowledgeWriter
from project_data_service.filex_knowledge import FilexKnowledge
from project_data_service.knowledge_category_removal import build
from project_data_service.filex_read import digest
from test_knowledge_requests import TestKnowledgeRequests

class CategoryWriter(unittest.TestCase):
 def setUp(self):self.memory=Memory();self.writer=FilexKnowledgeWriter(lambda:self.memory);self.reader=FilexKnowledge(lambda:self.memory)
 def request(self):return {'requestId':'category-delete-test','projectId':'binari','operation':'delete-category','entryId':'workflow','expectedVersion':0,'note':'test removal'}
 def test_single_metadata_write_hides_children_retains_bytes(self):
  before=copy.deepcopy(self.memory.docs);request=self.request();snapshot=self.writer.snapshot()
  result=self.writer.apply(request,snapshot['sourceVersion'],{},files=build(request,snapshot))
  self.assertEqual(self.memory.writes,1);self.assertIsNone(self.reader.category('workflow'));self.assertIsNone(self.reader.entry('existing'));self.assertEqual(self.reader.overview()['categories'],[])
  self.assertEqual(self.memory.docs['categories/workflow/entries/existing.json'],before['categories/workflow/entries/existing.json'])
  self.assertEqual(self.writer.apply(request,snapshot['sourceVersion'],{},files=[]),result);self.assertEqual(self.memory.writes,1)
 def test_core_category_cannot_hide(self):
  self.memory.docs['categories/workflow/entries/laya-worker-registry.json']={'id':'laya-worker-registry','categoryId':'workflow'}
  with self.assertRaisesRegex(ValueError,'BOOTSTRAP_CATEGORY'):build(self.request(),self.writer.snapshot())
  self.assertEqual(self.memory.writes,0)
 def test_tamper_or_stale_cannot_write(self):
  request=self.request();snapshot=self.writer.snapshot();files=build(request,snapshot);files[0]['body']['other']='inject'
  with self.assertRaisesRegex(ValueError,'CONTENT_LOSS'):self.writer.apply(request,snapshot['sourceVersion'],{},files=files)
  with self.assertRaisesRegex(ValueError,'SOURCE_CHANGED'):self.writer.apply(request,'stale',{},files=build(request,snapshot))
  self.assertEqual(self.memory.writes,0)

class CategoryFormal(TestKnowledgeRequests):
 def test_formal_remove_category(self):
  self.content.documents['categories/ordinary/metadata.json']={'id':'ordinary','name':'retain'}
  self.content.documents['categories/ordinary/entries/keep.json']={'id':'keep','content':'retain'}
  request={'schemaVersion':'ngs.knowledge-request.v1','requestId':'category-remove-official','projectId':'binari','operation':'delete-category','entryId':'ordinary','expectedVersion':0,'note':'remove classification'}
  result=self.s.submit(request);self.assertEqual(result['entry']['status'],'deleted');self.assertEqual(self.calls,1);self.assertEqual(self.content.calls,1)
  self.assertEqual(self.content.documents['categories/ordinary/entries/keep.json']['content'],'retain')

class LayaHiddenParent(unittest.TestCase):
 def test_hidden_classification_excluded_from_laya(self):
  from laya_service.knowledge_bootstrap import initial_documents
  docs=initial_documents();docs['categories/hidden/metadata.json']={'id':'hidden','status':'deleted'}
  docs['categories/hidden/entries/hidden.json']={'id':'hidden','categoryId':'hidden','status':'active','authority':'authoritative','title':'hidden','version':1,'data':{}}
  hashes={p:digest(v)for p,v in docs.items()}
  result=FilexKnowledge.compile_laya_catalog({'documents':docs,'hashes':hashes,'sourceVersion':digest(hashes)})
  self.assertNotIn('hidden',[v['entryId']for v in result['sources']])
  core=next(v for p,v in docs.items()if '/entries/'in p and v['id']=='laya-worker-registry')
  docs['categories/'+core['categoryId']+'/metadata.json']['status']='deleted'
  with self.assertRaisesRegex(ValueError,'NOT_CONFIRMED'):FilexKnowledge.compile_laya_catalog({'documents':docs,'hashes':hashes,'sourceVersion':digest(hashes)})
