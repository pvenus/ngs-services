import json,threading,unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from project_data_service.http_server import Handler
from laya_service.knowledge_source import PdsKnowledgeSource
from laya_service.knowledge_bootstrap import initial_documents
from project_data_service.filex_knowledge import FilexKnowledge,digest

class CatalogHttpTests(unittest.TestCase):
 def test_real_route_and_laya_contract(self):
  docs=initial_documents();hashes={k:digest(v) for k,v in docs.items()}
  catalog=FilexKnowledge.compile_laya_catalog({'documents':docs,'hashes':hashes,'sourceVersion':digest(hashes)})
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  origin='http://127.0.0.1:'+str(server.server_port)
  try:
   with patch.object(FilexKnowledge,'laya_catalog',return_value=catalog):
    with urlopen(origin+'/v1/knowledge/laya-catalog') as r:self.assertEqual(json.load(r),catalog)
    source=PdsKnowledgeSource(origin)
    registry,provenance=source()
    self.assertEqual(provenance['sourceVersion'],catalog['sourceVersion'])
    self.assertEqual(registry,catalog['registry'])
    for req,status in [(Request(origin+'/v1/knowledge/laya-catalog',headers={'Origin':origin}),403),(Request(origin+'/v1/knowledge/laya-catalog',method='POST'),405),(Request(origin+'/v1/knowledge/laya-catalog?q=1'),422)]:
     with self.assertRaises(HTTPError) as e:urlopen(req)
     self.assertEqual(e.exception.code,status)
   with patch.object(FilexKnowledge,'laya_catalog',side_effect=OSError('unavailable')):
    with self.assertRaises(HTTPError) as e:urlopen(origin+'/v1/knowledge/laya-catalog')
    self.assertEqual(e.exception.code,503)
  finally:server.shutdown();server.server_close();thread.join()
