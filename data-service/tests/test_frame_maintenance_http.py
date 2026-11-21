"""Real local PDS -> authenticated Filex fixture; no production resources."""
import json,threading,unittest,urllib.request,urllib.error,hashlib
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from test_npc_owned_authority import Tests as Base
from project_data_service.http_server import Handler
from npc_store import NPC_PREFIX,digest
setup_base=Base.setUp
teardown_base=Base.tearDown
class Tests(unittest.TestCase):
 def setUp(self):
  setup_base(self)
  self.pds=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.pds.app=SimpleNamespace(domains=SimpleNamespace(npcs=self.source));self.t=threading.Thread(target=self.pds.serve_forever,daemon=True);self.t.start();self.url='http://127.0.0.1:'+str(self.pds.server_port)
  self.writer.finish(self.guard);rid='frames.req.fixture';g=self.writer.admit('npc',self.generation,rid);self.sha=hashlib.sha256(b'frame').hexdigest();self.writer.put_asset(self.sha+'.png',b'frame',g)
  m={'requestId':rid,'directions':{'n':{'frames':[{'index':0,**{k:self.sha+'.png'for k in ('assetId','rawAssetId','alphaAssetId')}}]}}};p=NPC_PREFIX+'npc/animation-frames/idle/run.json'
  self.writer.batch(rid,'input',[{'path':p,'expectedHash':None,'body':m}],{p:None},g);self.writer.finish(g)
  self.cmd={'npcId':'npc','generationId':self.generation,'originalRequestId':rid,'maintenanceId':'frame.cleanup.http','expectedSourceVersion':self.writer.snapshot()['sourceVersion'],'expectedManifestHash':digest(m)}
 def tearDown(self):self.pds.shutdown();self.pds.server_close();self.t.join();teardown_base(self)
 def call(self,action,body=None,origin=False):
  headers={'Content-Type':'application/json'}
  if origin:headers['Origin']=self.url
  req=urllib.request.Request(self.url+'/v1/npcs/ownership/frame-maintenance/'+action,data=json.dumps(body).encode()if body is not None else None,headers=headers)
  with urllib.request.urlopen(req)as r:return json.load(r)
 def test_full_protocol_replay_and_no_status_token(self):
  self.assertTrue(self.call('capability')['releasedFrameCleanupLeaseV1']);g=self.call('begin',self.cmd)['guard'];self.assertEqual(g,self.call('begin',self.cmd)['guard'])
  p=self.call('reserve',{'guard':g,'sha256':self.sha,'reservationId':'a'*64})
  with self.assertRaises(urllib.error.HTTPError):self.call('finish',{'guard':g})
  self.assertTrue(self.call('release',{'guard':g,'reservationId':'a'*64,'reservationToken':p['reservationToken']})['released'])
  self.assertTrue(self.call('finish',{'guard':g})['released']);self.assertTrue(self.call('finish',{'guard':g})['released'])
  status=json.load(urllib.request.urlopen(self.url+'/v1/npcs/ownership/generation?npcId=npc'));self.assertEqual(status['activeMaintenance'],0);self.assertNotIn('token',json.dumps(status).lower())
 def test_origin_unknown_fields_and_capability_denial(self):
  with self.assertRaises(urllib.error.HTTPError)as e:self.call('begin',self.cmd,True)
  self.assertEqual(e.exception.code,403)
  with self.assertRaises(urllib.error.HTTPError):self.call('begin',{**self.cmd,'paths':['/tmp/any']})
  with self.assertRaises(urllib.error.HTTPError):self.call('finish',{'guard':{},'extra':True})
del Base
