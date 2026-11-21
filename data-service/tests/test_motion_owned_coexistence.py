"""Production preset routes must survive owned-authority additions."""
import json,threading,unittest,urllib.request,urllib.error
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch
from project_data_service.http_server import Handler

class Tests(unittest.TestCase):
 def test_motion_route_and_existing_security_gate(self):
  http=ThreadingHTTPServer(('127.0.0.1',0),Handler);http.app=SimpleNamespace();thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start();url='http://127.0.0.1:'+str(http.server_port)
  try:
   with patch('project_data_service.motion_presets.MotionPresets')as cls:
    cls.return_value.list.return_value={'presets':[{'presetId':'run'}]}
    result=json.load(urllib.request.urlopen(url+'/v1/motion-presets'));self.assertEqual(result['presets'][0]['presetId'],'run')
    req=urllib.request.Request(url+'/v1/motion-presets',headers={'Origin':url})
    with self.assertRaises(urllib.error.HTTPError)as e:urllib.request.urlopen(req)
    self.assertEqual(e.exception.code,403);self.assertEqual(cls.return_value.list.call_count,1)
  finally:http.shutdown();http.server_close();thread.join()
 def test_packaging_all_owned_and_motion_modules(self):
  from pathlib import Path
  root=Path(__file__).resolve().parents[2]/'filex-writer';line=next(x for x in (root/'Dockerfile').read_text().splitlines()if x.startswith('COPY '))
  for name in ('motion_store.py','fenced_store.py','migration_store.py','owned_store.py','npc_document.py','ownership_plan.py'):
   self.assertIn(name,line);self.assertTrue((root/name).is_file())
if __name__=='__main__':unittest.main()
