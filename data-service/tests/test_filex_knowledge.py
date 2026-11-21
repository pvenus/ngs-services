import copy,unittest
from project_data_service.filex_knowledge import FilexKnowledge
from project_data_service.filex_read import digest
class Transport:
 def __init__(self,value):self.value=value
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def snapshot(self):return copy.deepcopy(self.value)
def fixture():
 docs={'overview.json':{'categories':[{'id':'old'}]},'categories/workflow/metadata.json':{'id':'workflow','entries':[{'id':'old'}],'entryCount':99},'categories/workflow/entries/test.json':{'id':'test','categoryId':'workflow','version':1,'data':{'a':1}}}
 hashes={p:digest(v)for p,v in docs.items()};version=digest(hashes)
 return {'sourceVersion':version,'manifestHash':version,'documents':docs,'hashes':hashes}
class Tests(unittest.TestCase):
 def test_entry_source(self):
  f=fixture();v=FilexKnowledge(lambda:Transport(f)).entry('test');self.assertEqual(v['data'],{'a':1});self.assertEqual(v['filexSource']['sourceVersion'],f['sourceVersion'])
 def test_corrupt_file_rejected(self):
  f=fixture();f['documents']['categories/workflow/entries/test.json']['data']={}
  with self.assertRaisesRegex(ValueError,'HASH_MISMATCH'):FilexKnowledge(lambda:Transport(f)).entry('test')
 def test_stale_version_rejected(self):
  with self.assertRaisesRegex(ValueError,'SOURCE_CHANGED'):FilexKnowledge(lambda:Transport(fixture())).snapshot('wrong')
 def test_missing_and_path(self):
  r=FilexKnowledge(lambda:Transport(fixture()));self.assertIsNone(r.entry('absent'))
  with self.assertRaises(ValueError):r.entry('../test')
 def test_projection_uses_current_entries_not_stale_summary(self):
  f=fixture();r=FilexKnowledge(lambda:Transport(f));self.assertEqual(r.category('workflow')['entryCount'],1);self.assertEqual(r.category('workflow')['entries'][0]['id'],'test');self.assertEqual(r.overview()['categories'][0]['id'],'workflow');self.assertEqual(f['documents']['categories/workflow/metadata.json']['entryCount'],99)
 def test_full_snapshot_no_history(self):
  f=fixture();s=FilexKnowledge(lambda:Transport(f)).snapshot();self.assertEqual(len(s['documents']),3);self.assertFalse(any('versions/'in p for p in s['documents']))
