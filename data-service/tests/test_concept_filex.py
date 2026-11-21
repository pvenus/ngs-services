import copy,json,sys,tempfile,unittest
from pathlib import Path
from project_data_service.concept_migration import migrate
from project_data_service.concept_filex import FilexConcept,split,join,PREFIX
from project_data_service.concept import ROOT
from project_data_service.knowledge_requests import digest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'filex-writer'))
from concept_store import ConceptStore
class Transport:
 def __init__(self,store):self.store=store
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def capabilities(self):return {'namespace':PREFIX,'lockedBatch':True}
 def snapshot(self):return self.store.snapshot()
 def receipt(self,key):
  try:return self.store.receipt(key)
  except FileNotFoundError:return None
 def call(self,method,path,body):return self.store.batch(body['requestId'],body['inputHash'],body['files'],body['readSet'])
class Test(unittest.TestCase):
 def test_exact_roundtrip_individual_update_no_history(self):
  with tempfile.TemporaryDirectory()as tmp:
   store=ConceptStore(tmp);doc=json.loads((ROOT/'seed.json').read_text());doc['projectId']='ProjectBS';e={'revision':1,'savedAt':'test','note':'initial','document':doc};files=split(e)
   self.assertTrue(migrate(e,lambda:Transport(store))['verified']);self.assertTrue(migrate(e,lambda:Transport(store))['verified'])
   source=FilexConcept(transport_factory=lambda:Transport(store));self.assertEqual(source.current(),e)
   section=doc['sections'][0];field=section['fields'][0];new=copy.deepcopy(doc);new['sections'][0]['fields'][0]['value']='updated master';new['sections'][0]['fields'][0]['status']='confirmed'
   r={'requestId':'request-test','expectedRevision':1,'documentHash':digest(doc)}
   result=source.apply(r,'input',new,{});self.assertTrue(result['verified']);self.assertEqual(source.current()['document'],new)
   before=split(e);after=source.snapshot()['documents'];changed=[p for p in before if before[p]!=after[p]]
   self.assertEqual(set(changed),{'metadata.json','sections/'+section['id']+'/fields/'+field['id']+'.json'})
   self.assertIsNone(source.revision(1));self.assertEqual(len(source.history()),1);self.assertEqual(source.recover('request-test','input'),result)
   self.assertFalse((Path(tmp)/'.stage').exists());self.assertFalse((Path(tmp)/'.batch.json').exists())
if __name__=='__main__':unittest.main()
