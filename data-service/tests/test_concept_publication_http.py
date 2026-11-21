import json,threading,unittest,urllib.request,urllib.error
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from test_concept_publications import Test as Base
from project_data_service.http_server import Handler
class Test(Base):
 def setUp(self):
  super().setUp();self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.app=SimpleNamespace(concept=self.source);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.url='http://127.0.0.1:'+str(self.server.server_port)
  self.addCleanup(self.stop)
 def stop(self):self.server.shutdown();self.server.server_close();self.thread.join()
 def call(self,path,p,origin=False):
  headers={'Content-Type':'application/json'}
  if origin:headers['Origin']=self.url
  with urllib.request.urlopen(urllib.request.Request(self.url+path,data=json.dumps(p).encode(),headers=headers))as r:return json.load(r)
 def test_http_commit_recover_scope(self):
  d=self.source.current()['document'];d['sections'][0]['summary']=self.summary();p=self.payload('category.summary.modify',d)
  self.assertIsNone(self.call('/v1/concept/publications/recover',p)['receipt'])
  r=self.call('/v1/concept/publications',p);self.assertEqual(r,self.call('/v1/concept/publications/recover',p)['receipt'])
  with self.assertRaises(urllib.error.HTTPError)as error:self.call('/v1/concept/publications',p,True)
  self.assertEqual(error.exception.code,403)
  with self.assertRaises(urllib.error.HTTPError):self.call('/v1/concept/publications',{**p,'extra':True})
