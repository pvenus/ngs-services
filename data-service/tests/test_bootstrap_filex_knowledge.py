import importlib.util,pathlib,tempfile,unittest,json,copy
from unittest.mock import patch
from project_data_service.filex_read import digest
P=pathlib.Path(__file__).resolve().parents[1]/'tools/bootstrap_filex_knowledge.py';spec=importlib.util.spec_from_file_location('bootstrap_tool',P);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
class Transport:
 def __init__(self):self.docs={};self.writes=[]
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def capabilities(self):return {'atomicCAS':True}
 def snapshot(self):
  hs={p:digest(v)for p,v in self.docs.items()};return {'documents':copy.deepcopy(self.docs),'hashes':hs,'sourceVersion':digest(hs)}
 def compare_and_swap(self,k,old,new,**kw):
  if old is not None or self.snapshot()['sourceVersion']!=kw['expected_source_version']:raise ValueError('conflict')
  p=k[len(mod.PREFIX):]
  if p in self.docs:raise ValueError('exists')
  self.docs[p]=json.loads(new);self.writes.append(p)
class Tests(unittest.TestCase):
 def setUp(self):
  self.t=Transport();self.docs={'overview.json':{'revision':2},'categories/workflow.json':{'id':'workflow'},'entries/keep.json':{'id':'keep','content':'unchanged'}}
  self.tmp=tempfile.TemporaryDirectory();self.registry=pathlib.Path(self.tmp.name)/'registry.json';self.registry.write_text('{"version":"test"}')
 def tearDown(self):self.tmp.cleanup()
 def execute(self,apply=True):
  with patch.object(mod,'capture',side_effect=lambda:copy.deepcopy(self.docs)),patch.object(mod,'FilexCASTransport',return_value=self.t),patch.object(mod,'validate_active_registry'):return mod.execute(self.registry,apply)
 def test_dry_run_no_write(self):self.assertEqual(self.execute(False)['created'],[]);self.assertEqual(self.t.docs,{})
 def test_old_migration_apply_cancelled(self):
  with self.assertRaisesRegex(ValueError,'CANCELLED_BY_OWNER'):self.execute(True)
  self.assertEqual(self.t.docs,{})
if __name__=='__main__':unittest.main()
