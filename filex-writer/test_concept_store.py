import tempfile,unittest
from concept_store import ConceptStore,CONCEPT_PREFIX,digest
from server import Conflict
class Test(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.s=ConceptStore(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def test_namespace(self):
  for key in ['canonical-files/binari/npcs/metadata.json',CONCEPT_PREFIX+'../x',CONCEPT_PREFIX+'characters/x.json']:
   with self.assertRaises(ValueError):self.s.path(key)
 def test_batch_full_fields_replay_and_conflict(self):
  key=CONCEPT_PREFIX+'sections/art/fields/rules.json';files=[{'path':key,'body':{'id':'rules','value':'old'},'expectedHash':None}]
  a=self.s.batch('initial','a',files,{key:None});self.assertEqual(a['status'],'committed');self.assertEqual(self.s.batch('initial','a',files,{key:None}),a)
  s=self.s.snapshot();self.assertEqual(s['documents']['sections/art/fields/rules.json']['value'],'old')
  with self.assertRaises(Conflict):self.s.batch('other','b',[{**files[0],'body':{'value':'new'}}],{key:None})
if __name__=='__main__':unittest.main()
