import io,json,unittest
from catalog_service.repository import InMemoryCatalogRepository
from catalog_service.service import CatalogService
from catalog_service.bootstrap import load_minimum_catalog
from catalog_service.http_api import CatalogApi
from catalog_service.execution_contracts import entries,get

class NoKnowledgeRepository(InMemoryCatalogRepository):
 def get(self,collection,*args,**kwargs):
  if collection=='knowledge':raise AssertionError('knowledge read')
  return super().get(collection,*args,**kwargs)
 def put(self,collection,*args,**kwargs):
  if collection=='knowledge':raise AssertionError('knowledge write')
  return super().put(collection,*args,**kwargs)

class Contracts(unittest.TestCase):
 def setUp(self):
  self.repository=NoKnowledgeRepository();self.service=CatalogService(self.repository)
  load_minimum_catalog(self.service)
 def test_bootstrap_and_context_without_knowledge(self):
  context=self.service.get_execution_context('video-alpha.compose-spec')
  self.assertNotIn('knowledge',context)
  self.assertEqual(len(context['executionContracts']),4)
  self.assertEqual(context['template']['prerequisite_get'],'video-alpha.job-get')
 def test_eight_versioned_execution_contracts(self):
  self.assertEqual(len(entries()),8)
  self.assertEqual(get('npc-planning.workflow.v1')['content']['readCapability']['toolName'],'npc_context_read')
  self.assertEqual(get('template.processing-spec.knowledge')['id'],'template.processing-spec.contract')
 def call(self,path,method='GET',payload=None):
  body=json.dumps(payload).encode() if payload else b'';status=[]
  result=CatalogApi(self.service)({'PATH_INFO':path,'REQUEST_METHOD':method,'CONTENT_LENGTH':str(len(body)),'wsgi.input':io.BytesIO(body)},lambda s,h:status.append(s))
  return status[0],json.loads(b''.join(result))
 def test_removed_routes_and_new_query(self):
  for path in ['/v1/catalog/knowledge','/v1/catalog/knowledge/query','/v1/catalog/knowledge/fixture']:
   for method in ['GET','PUT','POST']:
    self.assertEqual(self.call(path,method)[0],'410 Gone')
  status,body=self.call('/v1/catalog/execution-contracts/query','POST',{'ids':['npc-planning.rules.v1','missing']})
  self.assertEqual(status,'200 OK');self.assertEqual(body['missingIds'],['missing']);self.assertEqual(len(body['entries']),1)

class ContextBinding(unittest.TestCase):
 def test_decision_and_executor_bind_same_contracts(self):
  from workflow_decision_service.feature_selection import FeatureSelectionService
  from workflow_service.service import WorkflowService
  repository=NoKnowledgeRepository();service=CatalogService(repository);load_minimum_catalog(service)
  class Adapter:
   def list_services(self):return repository.list('services',status='active')
   def list_actions(self):return repository.list('actions',status='active')
   def execution_context(self,key):return service.get_execution_context(key)
  decision=FeatureSelectionService(Adapter()).select('video 처리',{'jobId':'fixture'})
  context=service.get_execution_context(decision['actionId'])
  self.assertEqual(decision['contextEntryIds'],[c['id'] for c in context['executionContracts']])
  worker=WorkflowService._codex_context(context)
  self.assertEqual(worker['executionContracts'],[{k:c[k] for k in ['id','purpose','content']} for c in context['executionContracts']])
  self.assertNotIn('knowledge',worker)
