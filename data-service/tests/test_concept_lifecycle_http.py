"""Real PDS -> authenticated Filex protocol on temporary data only."""
import json,threading,urllib.request,urllib.error,unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from test_concept_lifecycle import Lifecycle
from project_data_service.http_server import Handler
from project_data_service.filex_cas_transport import FilexCASTransport
from server import handler
class HTTP(unittest.TestCase):
 setUpBase=Lifecycle.setUp
 summary=Lifecycle.summary
 payload=Lifecycle.payload
 command=Lifecycle.command
 setup_item=Lifecycle.setup_item
 def setUp(self):
  self.setUpBase();self.scope=self.setup_item()
  token='temporary-concept-fixture-token-1234567890';tf=self.store.root/'.fixture-token';tf.write_text(token);tf.chmod(0o600)
  self.writer=ThreadingHTTPServer(('127.0.0.1',0),handler(self.store,token));self.wt=threading.Thread(target=self.writer.serve_forever,daemon=True);self.wt.start()
  cfg={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','enabled':True,'endpoint':'http://127.0.0.1:'+str(self.writer.server_port),'tokenFile':str(tf)}
  self.source.factory=lambda:FilexCASTransport(cfg)
  self.pds=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.pds.app=SimpleNamespace(concept=self.source);self.pt=threading.Thread(target=self.pds.serve_forever,daemon=True);self.pt.start();self.url='http://127.0.0.1:'+str(self.pds.server_port);self.addCleanup(self.stop)
 def stop(self):
  for s,t in ((self.pds,self.pt),(self.writer,self.wt)):s.shutdown();s.server_close();t.join()
 def call(self,p,suffix='',origin=False):
  headers={'Content-Type':'application/json'}
  if origin:headers['Origin']=self.url
  with urllib.request.urlopen(urllib.request.Request(self.url+'/v1/concept/lifecycle'+suffix,data=json.dumps(p).encode(),headers=headers))as r:return json.load(r)['receipt']
 def test_remove_restore_real_http(self):
  p=self.command(self.scope);self.assertIsNone(self.call(p,'/recover'));r=self.call(p);self.assertTrue(r['recoverable']);self.assertEqual(r,self.call(p,'/recover'));q=self.command(self.scope,r['backupId'])
  with self.assertRaises(urllib.error.HTTPError)as e:self.call(q)
  self.assertEqual(e.exception.code,422);self.assertEqual(self.source.current()['revision'],3)
 def test_direct_writer_restore_rejected_without_mutation(self):
  before=self.source.current();token='temporary-concept-fixture-token-1234567890'
  req=urllib.request.Request('http://127.0.0.1:'+str(self.writer.server_port)+'/v1/concept/restore-scope',data=b'{}',headers={'Content-Type':'application/json','Authorization':'Bearer '+token})
  with self.assertRaises(urllib.error.HTTPError)as e:urllib.request.urlopen(req)
  self.assertEqual(e.exception.code,400);self.assertEqual(json.loads(e.exception.read())['error'],'INVALID_REQUEST');self.assertEqual(self.source.current(),before)
 def test_origin_and_payload_rejected(self):
  p=self.command(self.scope)
  with self.assertRaises(urllib.error.HTTPError)as e:self.call(p,origin=True)
  self.assertEqual(e.exception.code,403)
  with self.assertRaises(urllib.error.HTTPError)as e:self.call({**p,'paths':['/tmp/any']})
  self.assertEqual(e.exception.code,422);self.assertEqual(self.source.current()['revision'],2)
del Lifecycle
