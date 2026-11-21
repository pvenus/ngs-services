import json,threading,unittest,urllib.request,urllib.error
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from test_npc_owned_authority import Tests as Fixture
from project_data_service.http_server import Handler
from test_npc_owned_authority import make_transport
class AbandonHTTPTests(unittest.TestCase):
 setUp=Fixture.setUp
 tearDown=Fixture.tearDown
 def test_actual_pds_filex_replay_and_authority(self):
  http=ThreadingHTTPServer(('127.0.0.1',0),Handler);http.app=SimpleNamespace(domains=SimpleNamespace(npcs=self.source));thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
  url='http://127.0.0.1:'+str(http.server_port)+'/v1/npcs/ownership/abandon-creation'
  def post(body,headers=None):
   return json.load(urllib.request.urlopen(urllib.request.Request(url,data=json.dumps(body).encode(),headers={'Content-Type':'application/json',**(headers or {})})))
  try:
   source=self.source.current()['sourceVersion']
   with make_transport(self.config)as t:g=t.prepare_create('new','create.unstarted',source)
   with self.assertRaises(urllib.error.HTTPError)as e:post({'guard':g},{'Origin':'http://127.0.0.1'})
   self.assertEqual(e.exception.code,403)
   with self.assertRaises(urllib.error.HTTPError)as e:post({'guard':dict(g,lifecycleToken='wrong')})
   self.assertIn(e.exception.code,(400,409,422))
   r=post({'guard':g});self.assertEqual(r['state'],'creation-abandoned');self.assertEqual(post({'guard':g}),r)
   with make_transport(self.config)as t:g2=t.prepare_create('new','create.next',source)
   self.assertNotEqual(g2['generationId'],g['generationId']);self.assertEqual(post({'guard':g}),r)
   self.assertEqual(self.source.current()['document'],self.doc)
   writerurl=self.config['endpoint']+'/v1/generation/abandon-creation'
   with self.assertRaises(urllib.error.HTTPError)as e:
    urllib.request.urlopen(urllib.request.Request(writerurl,data=json.dumps({'guard':g2}).encode(),headers={'Content-Type':'application/json'}))
   self.assertEqual(e.exception.code,401)
  finally:http.shutdown();http.server_close();thread.join()
del Fixture
if __name__=='__main__':unittest.main()
