import base64,json,threading,unittest,urllib.request,urllib.error
from http.server import ThreadingHTTPServer
import test_portrait_pair_direct as pair
import test_pose_direct as pose
from project_data_service import portrait_pair_direct,pose_direct
class Tests(unittest.TestCase):
 def run_boundary(self,fixture,module,path):
  w=fixture.Tests();w.setUp();server=ThreadingHTTPServer(('127.0.0.1',0),module.handler(w.service));t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
  def call(guard=None,stage=None,extra=None,suffix='publish'):
   headers={'Content-Type':'application/octet-stream'if suffix=='publish'else'application/json'}
   if guard is not None:headers['X-NPC-Ownership-Guard']=base64.b64encode(json.dumps(guard).encode()).decode()
   if stage is not None:headers['X-NPC-Stage-Request-Id']=stage
   headers.update(extra or {})
   raw=w.body if suffix=='publish'else json.dumps({'claim':w.claim,'packetHash':module.digest(w.packet)}).encode()
   with urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+path+'/'+suffix,data=raw,headers=headers))as r:return json.load(r)
  try:
   guard=w.binding['parentGuard'];rid=w.claim['request_id']
   for g,stage,extra in ((None,None,{'Authorization':'Bearer existing-image-ingress'}),(guard,None,{}),(guard,'another.child',{}),({**guard,'leaseToken':'c'*64},rid,{}),(guard,rid,{'Host':'localhost:'+str(server.server_port)}),(guard,rid,{'Origin':'http://127.0.0.1'})):
    with self.assertRaises(urllib.error.HTTPError):call(g,stage,extra)
   self.assertEqual(call(guard,rid),w.result);self.assertEqual(call(guard,rid,suffix='read'),w.result)
   def expired(*a):raise ValueError('LIVE_RESERVATION_EXPIRED')
   w.service.authority=expired
   for suffix in ('publish','read'):
    with self.assertRaises(urllib.error.HTTPError):call(guard,rid,suffix=suffix)
  finally:server.shutdown();server.server_close();t.join()
 def test_pair(self):self.run_boundary(pair,portrait_pair_direct,'/v1/npc-portrait-pairs')
 def test_pose(self):self.run_boundary(pose,pose_direct,'/v1/npc-pose-directions')
