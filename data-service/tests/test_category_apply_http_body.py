"""Bounded loopback category envelope tests; fixture adapter never persists."""
import io,json,threading,unittest,urllib.request,urllib.error
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from project_data_service.http_server import Handler,MAX_BODY,MAX_CATEGORY_APPLY_BODY
from project_data_service.database import DataError
class Tests(unittest.TestCase):
 def handler(self,body,length=None):
  h=object.__new__(Handler);h.headers={'Content-Length':str(len(body) if length is None else length),'Content-Type':'application/json'};h.rfile=io.BytesIO(body);return h
 def test_category_bound_accepts_current_scale_without_global_expansion(self):
  body=json.dumps({'document':{'note':'가'*800000}},ensure_ascii=False).encode()
  self.assertGreater(len(body),MAX_BODY)
  with self.assertRaises(DataError):self.handler(body).read_json()
  self.assertEqual(len(self.handler(body).read_json(MAX_CATEGORY_APPLY_BODY)['document']['note']),800000)
  self.assertEqual(MAX_BODY,2*1024*1024);self.assertEqual(MAX_CATEGORY_APPLY_BODY,32*1024*1024)
 def test_oversize_rejected_before_read(self):
  h=self.handler(b'{}',MAX_CATEGORY_APPLY_BODY+1)
  with self.assertRaisesRegex(DataError,'invalid request body size'):h.read_json(MAX_CATEGORY_APPLY_BODY)
  self.assertEqual(h.rfile.tell(),0)
 def test_real_endpoint_dispatch_and_loopback_guards(self):
  seen=[]
  def category(body):seen.append(body);return {'verified':True,'size':len(body['document']['note'])}
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.app=SimpleNamespace(domains=SimpleNamespace(category=category));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   url='http://127.0.0.1:'+str(server.server_port)+'/v1/npc-category/apply';body=json.dumps({'document':{'note':'가'*800000}},ensure_ascii=False).encode()
   def call(data,headers):return urllib.request.urlopen(urllib.request.Request(url,data=data,headers=headers),timeout=5)
   with call(body,{'Content-Type':'application/json'})as r:self.assertEqual(json.load(r),{'verified':True,'size':800000})
   with self.assertRaises(urllib.error.HTTPError)as e:call(b'{}',{'Content-Type':'application/json','Origin':'https://untrusted.example'})
   self.assertEqual(e.exception.code,403)
   with self.assertRaises(urllib.error.HTTPError)as e:call(b'{}',{'Content-Type':'text/plain'})
   self.assertEqual(e.exception.code,422);self.assertEqual(len(seen),1)
  finally:server.shutdown();server.server_close();thread.join()
