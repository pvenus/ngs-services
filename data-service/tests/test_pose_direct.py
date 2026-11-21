import hashlib,json,unittest
from project_data_service.pose_direct import *
from test_portrait_pair_direct import png
class Tests(unittest.TestCase):
 def setUp(self):
  self.files={n:png()for n in PNG_FILES}|{n:b'{"raw": true}'for n in JSON_FILES};params={'schemaVersion':'ngs.pose-direction-request.v1','parentRequestId':'parent','direction':'north'}
  self.claim={'request_id':'parent.north','attempt_id':'a','fencing_token':'1','capability':'image.generate','expected_revision':'1','parameters':params};self.packet={'manifest':{'files':{n:{'bytes':len(v),'sha256':sha(v)}for n,v in self.files.items()}}}
  self.body=encode({'claim':self.claim,'packet':self.packet,'metadataFiles':{n:self.files[n].decode()for n in JSON_FILES}},self.files)
  record={'request':{'requestId':'parent','npcId':'npc','projectId':'binari'},'state':{},'step':{'direction':'north'}}
  self.binding={'reservation':{'reserved':True,'publicationHash':digest(self.packet),'requestId':'parent.north','attemptId':'a','fencingToken':'1','capability':'image.generate','expectedRevision':'1','requestHash':digest(params),'targetId':'npc','projectId':'binari'},'record':record,'parentGuard':{'npcId':'npc','generationId':'a'*48,'requestId':'parent','leaseToken':'b'*64}}
  wire={'requestId':'parent.north','npcId':'npc','slot':{'posePreset':'idle','referenceType':'portrait','direction':'north'},'artifact':{'sha256':sha(png()),'bytes':len(png())},'sourceArtifact':{'sha256':sha(png()),'bytes':len(png())}}
  receipt={'requestId':'parent.north','inputHash':digest(wire),'npcId':'npc','slotKey':'idle/portrait/north','assetId':sha(png())+'.png','sourceArtifactId':sha(png())+'.png','sha256':sha(png()),'bytes':len(png()),'verified':True,'source':'filex'}
  self.result={'requestId':'parent.north','attemptId':'a','fencingToken':'1','packetHash':digest(self.packet),'sourceReceipt':receipt,'wire':wire};self.calls=[]
  def authority(*a):self.calls.append(a);return self.binding
  self.service=DirectPoseDirections(authority,lambda *a:self.result,lambda *a:self.result,lambda h:True)
 def test_fixed_binary_and_receipt(self):
  self.assertEqual(decode(self.body)[1],self.files);self.assertEqual(self.service.publish(self.body),self.result);self.assertEqual(set(self.calls[0][2]),{'claim','packetHash'})
 def test_scope_rejects_before_processor(self):
  self.binding['parentGuard']['requestId']='sibling'
  with self.assertRaises(ValueError):self.service.publish(self.body)
 def test_corrupt_bytes_and_receipt(self):
  with self.assertRaises(ValueError):self.service.publish(self.body+b'x')
  self.assertFalse(self.calls);self.result['sourceReceipt']['inputHash']='0'*64
  with self.assertRaises(ValueError):self.service.publish(self.body)

class HTTPTests(unittest.TestCase):
 def test_real_handler(self):
  import threading,urllib.request,urllib.error
  from http.server import ThreadingHTTPServer
  w=Tests();w.setUp();w.service.authenticated=lambda h:h=='Bearer existing'
  server=ThreadingHTTPServer(('127.0.0.1',0),handler(w.service));t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
  try:
   def post(path,raw,content,token='existing'):
    req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+path,data=raw,headers={'Content-Type':content,'Authorization':'Bearer '+token,**({}if token=='wrong'else{'X-NPC-Ownership-Guard':__import__('base64').b64encode(json.dumps(w.binding['parentGuard']).encode()).decode(),'X-NPC-Stage-Request-Id':w.claim['request_id']})})
    with urllib.request.urlopen(req)as r:return json.load(r)
   with self.assertRaises(urllib.error.HTTPError):post('/v1/npc-pose-directions/publish',w.body,'application/octet-stream','wrong')
   self.assertFalse(w.calls)
   r=post('/v1/npc-pose-directions/publish',w.body,'application/octet-stream')
   self.assertEqual(r,post('/v1/npc-pose-directions/read',canonical({'claim':w.claim,'packetHash':digest(w.packet)}),'application/json'))
  finally:server.shutdown();server.server_close();t.join()

class PortTests(unittest.TestCase):
 def test_fixed_api_paths_only(self):
  from types import SimpleNamespace
  from project_data_service.pose_configuration import PoseAPI
  api=PoseAPI(SimpleNamespace(npcs=SimpleNamespace(current=lambda:{'authority':'filex'}),npc_files=lambda:{'sourceVersion':'pinned'}))
  self.assertEqual(api('GET','/api/npcs'),{'authority':'filex'})
  self.assertEqual(api('GET','/v1/npcs/files'),{'sourceVersion':'pinned'})
  for args in (('POST','/api/npcs'),('GET','/tmp/local.png'),('GET','/assets/portraits/../../secret')):
   with self.assertRaises(ValueError):api(*args,raw=args[-1].startswith('/assets'))

class ConfigurationTests(unittest.TestCase):
 def test_fixed_source_factory(self):
  import sys,importlib,tempfile
  from pathlib import Path
  from types import SimpleNamespace
  from project_data_service.pose_configuration import MODULES,configured
  sys.path[:0]=['/Users/fixture/Workspaces/NGS/staging/resident-handoff-runtime-20261118','/Users/fixture/Workspaces/Workers/staging/request-pose-direct-20261119']
  pins={}
  for name in MODULES:
   m=importlib.import_module(name);p=Path(m.__file__).resolve();pins[name]={'path':str(p),'sha256':sha(p.read_bytes())}
  with tempfile.TemporaryDirectory()as tmp:
   root=Path(tmp).resolve();cfg=root/'config';value={'schemaVersion':'pds.direct-pose-direction.v1','enabled':True,'stagingRoot':str(root),'sourcePins':pins};cfg.write_text(json.dumps(value));cfg.chmod(0o600)
   media=SimpleNamespace(authority=lambda *a:None,authenticated=lambda h:h=='existing');domains=SimpleNamespace(npcs=object())
   service=configured(cfg,media=media,domains=domains,planning=object());self.assertTrue(service.authenticated('existing'))
   value['sourcePins'][MODULES[0]]['sha256']='0'*64;cfg.write_text(json.dumps(value))
   with self.assertRaises(ValueError):configured(cfg,media=media,domains=domains,planning=object())
