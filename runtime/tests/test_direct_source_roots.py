import importlib.util,pathlib,tempfile,unittest
spec=importlib.util.spec_from_file_location('stack',pathlib.Path(__file__).parents[1]/'stack.py');stack=importlib.util.module_from_spec(spec);spec.loader.exec_module(stack)
class Tests(unittest.TestCase):
 def test_explicit_data_only(self):
  with tempfile.TemporaryDirectory()as tmp:
   root=pathlib.Path(tmp).resolve();a=root/'ngs';b=root/'workers';a.mkdir();b.mkdir();env={'PDS_DIRECT_PORTRAIT_PAIR_CONFIG':'private','PYTHONPATH':str(a)+':'+str(b)}
   self.assertEqual(stack.child_environment(env,'data',root)['PYTHONPATH'],str(root)+':'+env['PYTHONPATH'])
   self.assertEqual(stack.child_environment(env,'workflow',root)['PYTHONPATH'],str(root))
 def test_default_and_invalid(self):
  self.assertEqual(stack.child_environment({'PYTHONPATH':'arbitrary'},'data','/service')['PYTHONPATH'],'/service')
  with self.assertRaises(ValueError):stack.child_environment({'PDS_DIRECT_POSE_DIRECTION_CONFIG':'p','PYTHONPATH':'relative:else'},'data','/service')
