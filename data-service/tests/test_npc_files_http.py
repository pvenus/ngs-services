import json,threading,unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from types import SimpleNamespace
from unittest.mock import Mock
from project_data_service.http_server import Handler

class NpcFilesHttp(unittest.TestCase):
 def test_server_only_snapshot_and_unavailable(self):
  value={'authority':'filex','documents':{},'sourceVersion':'a'*64}
  domains=SimpleNamespace(npc_files=Mock(return_value=value))
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.app=SimpleNamespace(domains=domains)
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  origin='http://127.0.0.1:'+str(server.server_port)
  try:
   with urlopen(origin+'/v1/npcs/files') as r:self.assertEqual(json.load(r),value)
   for req,status in [(Request(origin+'/v1/npcs/files',headers={'Origin':origin}),403),(Request(origin+'/v1/npcs/files',method='POST'),405),(Request(origin+'/v1/npcs/files?q=1'),422)]:
    with self.assertRaises(HTTPError) as e:urlopen(req)
    self.assertEqual(e.exception.code,status)
   domains.npc_files.side_effect=OSError('offline')
   with self.assertRaises(HTTPError) as e:urlopen(origin+'/v1/npcs/files')
   self.assertEqual(e.exception.code,503)
  finally:server.shutdown();server.server_close();thread.join()
