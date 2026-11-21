"""Sterile worker -> PDS -> Filex and PDS -> Request metadata, separate PIDs.
No production configuration, credentials, ports, model, or filesystem is used.
"""
import base64,dataclasses,hashlib,io,json,os,subprocess,sys,tempfile,threading,unittest,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'data-service'),str(ROOT/'filex-writer')]
WORKERS=os.environ.get('WORKERS_NATIVE_PNG_SOURCE');NGS=os.environ.get('NGS_NATIVE_PNG_SOURCE')
if WORKERS:sys.path.insert(0,WORKERS)
if NGS:sys.path.insert(0,NGS)

def child(mode,config):
 from http.server import ThreadingHTTPServer,HTTPServer
 from project_data_service.filex_read import canonical
 c=json.loads(Path(config).read_text());root=Path(c['root'])
 if mode=='filex':
  from fenced_store import FencedOwnedNPCStore
  from npc_store import encoded
  from server import handler
  store_root=root/'filex';store_root.mkdir();(store_root/'metadata.json').write_bytes(encoded({'characterOrder':['npc','other']}))
  for npc in ('npc','other'):
   (store_root/npc).mkdir();(store_root/npc/'metadata.json').write_bytes(encoded({'id':npc}))
  store=FencedOwnedNPCStore(store_root);gen=store.bootstrap('npc',store.snapshot()['sourceVersion'])['generationId']
  server=ThreadingHTTPServer(('127.0.0.1',0),handler(store,'f'*32));ready={'generationId':gen}
 elif mode=='request':
  from local.services.request_pipeline.common import CommonRequests
  from local.services.animation_requests.native_png_workflow import NativePNGWorkflow,BATCH_SCHEMA
  import importlib.util
  spec=importlib.util.spec_from_file_location('native_source_fixture',Path(WORKERS)/'tests/test_native_png_inputs.py');source=importlib.util.module_from_spec(spec);spec.loader.exec_module(source)
  from workers.resident.adapters.native_png_inputs import freeze_inputs,read_frozen_inputs
  from workers.resident.deployment.native_png_requests import NativePNGRequests
  from workers.resident.deployment.native_png_storage import NativePNGStorage
  from workers.resident.deployment.native_png_publication import NativePNGPublicationAuthority
  from workers.resident.deployment.request_service import build_handler
  from workers.resident.contracts import PROFILES
  from project_data_service.pds_owned_transport import OwnedNPCTransport
  common=CommonRequests(root/'common.sqlite');w=NativePNGWorkflow(common,read_raw=lambda _:None,read_alpha=lambda _:None);w.initialize()
  f=source.Tests();f.setUp();f.generation['generationId']=c['generationId'];original=f.client.json
  def read(path,payload=None):
   if path.startswith('/v1/motion-presets/'):f.motion['direction']=path.rsplit('/',1)[1]
   return original(path,payload)
  f.client.json=read
  resolve=lambda r,g:freeze_inputs(f.client,r,generation_id=g,read_generation=lambda _:f.generation)[0]
  directions=('n','ne','e','se','s','sw','w','nw')
  parent=w.admit_frozen('owner','fixture',dict(schemaVersion=BATCH_SCHEMA,npcId='npc',generationId=c['generationId'],posePreset='idle',presetId='walk',policyVersion='v1',directionRequests={d:dict(f.request,direction=d)for d in directions}),resolve_frozen=resolve)['requestId']
  with OwnedNPCTransport(c['filex'])as t:guard=t.admit('npc',c['generationId'],parent)
  def verify(r,b):read_frozen_inputs(f.client,r,b,read_generation=lambda _:f.generation);return True
  a=NativePNGRequests(w,threading.RLock(),lambda *_:None,{'video-global':1},verify_frozen=verify,verify_stopped=lambda _:False,guard_resolver=lambda claim:dict(guard=guard,scopeRequestId=parent,stageRequestId=claim.request_id));a.initialize_schema()
  claim=a.claim(PROFILES['animation'],'fixture.worker')
  storage=NativePNGStorage(a.authority,lambda *_:(_ for _ in ()).throw(AssertionError('REQUEST MUST NOT STORE BYTES')),enabled=True)
  service=NativePNGPublicationAuthority(storage)
  original_authorize=service.authorize
  def log(claim,action,metadata):
   with (root/'metadata-audit.jsonl').open('a')as out:out.write(json.dumps({'action':action,'metadata':metadata})+'\n')
   return original_authorize(claim,action,metadata)
  service.authorize=log
  server=HTTPServer(('127.0.0.1',0),build_handler(a.authority,{'worker-token':('fixture.worker','animation')},publication_tokens=['a'*32],owned_operation_authority=service));ready={'claim':dataclasses.asdict(claim),'guard':guard}
 elif mode=='pds':
  from project_data_service.http_server import Handler
  from project_data_service.owned_operation_http import OwnedOperationsHTTP
  from project_data_service.media_artifacts import Transport
  from project_data_service.pds_owned_transport import OwnedNPCTransport
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
  if c.get('enabled',True):server.owned_npc_operations=OwnedOperationsHTTP(Transport(c['requestEndpoint'],c['authorityToken']),lambda:OwnedNPCTransport(c['filex']))
  ready={}
 else:raise AssertionError(mode)
 print(json.dumps({**ready,'endpoint':'http://127.0.0.1:'+str(server.server_port),'pid':os.getpid()}),flush=True);server.serve_forever()

class Tests(unittest.TestCase):
 def setUp(self):
  if not WORKERS or not NGS:self.skipTest('explicit WORKERS_NATIVE_PNG_SOURCE and NGS_NATIVE_PNG_SOURCE required')
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.procs=[]
  self.addCleanup(self.stop)
  for name,text in [('filex-token','f'*32),('authority-token','a'*32)]:
   p=self.root/name;p.write_text(text);p.chmod(0o600)
  self.cfg={'root':str(self.root)};fx=self.start('filex')
  self.cfg.update(generationId=fx['generationId'],filex=dict(schemaVersion='pds-filex-cas.v1',projectId='binari',scope='npc',enabled=True,ownershipMode='npc-owned-v1',endpoint=fx['endpoint'],tokenFile=str(self.root/'filex-token')))
  request=self.start('request');self.cfg.update(requestEndpoint=request['endpoint'],authorityToken=str(self.root/'authority-token'))
  pds=self.start('pds');self.pids=[fx['pid'],request['pid'],pds['pid']]
  from workers.resident.contracts import Claim
  from workers.resident.deployment.native_png_http_storage import NativePNGHTTPStorage
  self.claim=Claim(**request['claim']);self.guard=request['guard'];self.client=NativePNGHTTPStorage(pds['endpoint'],lambda _:self.guard)
  self.path='canonical-files/binari/npcs/npc/animation-sequences/idle/walk/'+self.claim.parameters['direction']+'.json'
  from PIL import Image
  out=io.BytesIO();Image.new('RGBA',(4,4),(20,40,60,255)).save(out,'PNG');self.raw=out.getvalue();self.sha=hashlib.sha256(self.raw).hexdigest();self.asset=self.sha+'.png'
 def start(self,mode):
  p=self.root/(mode+'.json');p.write_text(json.dumps(self.cfg));log=(self.root/(mode+'.stderr')).open('w');self.addCleanup(log.close)
  proc=subprocess.Popen([sys.executable,__file__,'--fixture',mode,str(p)],stdout=subprocess.PIPE,stderr=log,text=True);self.procs.append(proc)
  line=proc.stdout.readline()
  if not line:raise AssertionError((self.root/(mode+'.stderr')).read_text())
  return json.loads(line)
 def stop(self):
  for log in self.root.glob('*.stderr'):
   if 'Traceback' in log.read_text():print(log.name,log.read_text(),file=sys.stderr)
  for p in reversed(self.procs):
   p.terminate()
   try:p.wait(5)
   except subprocess.TimeoutExpired:p.kill();p.wait()
   p.stdout.close()
 def test_internal_ingress_requires_original_guard_and_no_browser(self):
  import urllib.request,urllib.error,struct
  from project_data_service.owned_operation_http import PATH
  from workers.resident.deployment.native_png_http_storage import encoded
  e=encoded({'claim':dataclasses.asdict(self.claim),'action':'receipt','metadata':{}})
  data=struct.pack('>I',len(e))+e
  headers={'Content-Type':'application/octet-stream','X-NPC-Ownership-Guard':base64.b64encode(encoded(self.guard)).decode(),'X-NPC-Stage-Request-Id':self.claim.request_id}
  for change in ({'Origin':'http://example.invalid'},{'Host':'evil.invalid'},{'X-NPC-Stage-Request-Id':'other.request'},{'X-NPC-Ownership-Guard':''}):
   req=urllib.request.Request(self.client.endpoint+PATH,data=data,headers={**headers,**change})
   with self.assertRaises(urllib.error.HTTPError)as error:urllib.request.urlopen(req)
   self.assertIn(error.exception.code,(403,409,422))
  self.cfg['enabled']=False;disabled=self.start('pds')
  with self.assertRaises(urllib.error.HTTPError)as error:urllib.request.urlopen(urllib.request.Request(disabled['endpoint']+PATH,data=data,headers=headers))
  self.assertEqual(error.exception.code,503)
 def test_actual_process_transport_and_wrong_scope_denied(self):
  from project_data_service.pds_owned_transport import OwnedNPCTransport
  self.assertEqual(len(set(self.pids+[os.getpid()])),4)
  self.assertIsNone(self.client.receipt(self.claim))
  self.client.upload_asset(self.claim,self.asset,self.raw)
  manifest={'schemaVersion':'ngs.animation-native-png-sequence.v1','frameCount':2,'frames':[{'index':i,'assetId':self.asset,'sha256':self.sha,'mimeType':'image/png'}for i in range(2)]}
  files=[dict(path=self.path,body=manifest,expectedHash=None)]
  for badpath in (self.path.replace('/npc/','/other/'),self.path.replace('/walk/','/run/'),self.path.replace('/animation-sequences/','/planning/')):
   with self.assertRaises(urllib.error.HTTPError):self.client.commit(self.claim,self.claim.expected_revision,[{**files[0],'path':badpath}],{badpath:None})
  for bad in (dataclasses.replace(self.claim,fencing_token='wrong'),dataclasses.replace(self.claim,parameters={**self.claim.parameters,'actor':'other'}),dataclasses.replace(self.claim,parameters={**self.claim.parameters,'projectId':'other'}),dataclasses.replace(self.claim,request_id='other.request')):
   with self.assertRaises(urllib.error.HTTPError):self.client.upload_asset(bad,self.asset,self.raw)
  original=self.guard;self.guard={**original,'npcId':'other'}
  with self.assertRaises(urllib.error.HTTPError):self.client.upload_asset(self.claim,self.asset,self.raw)
  self.guard=original
  with OwnedNPCTransport(self.cfg['filex'])as t:
   before=t.snapshot();self.assertNotIn(self.path.removeprefix('canonical-files/binari/npcs/'),before['documents'])
  r=self.client.commit(self.claim,self.claim.expected_revision,files,{self.path:None})
  self.assertEqual(self.client.receipt(self.claim),r);self.assertEqual(self.client.commit(self.claim,self.claim.expected_revision,files,{self.path:None}),r)
  with OwnedNPCTransport(self.cfg['filex'])as t:
   self.assertEqual(t.asset_get(self.asset),self.raw)
   after=t.snapshot();self.assertEqual(after['documents']['other/metadata.json'],before['documents']['other/metadata.json'])
  import sqlite3
  with sqlite3.connect(self.root/'common.sqlite')as db:db.execute('UPDATE common_requests SET lease_until=0 WHERE request_id=?',(self.claim.request_id,))
  with self.assertRaises(urllib.error.HTTPError):self.client.receipt(self.claim)
  audit=(self.root/'metadata-audit.jsonl').read_text();self.assertNotIn(base64.b64encode(self.raw).decode(),audit);self.assertNotIn('frames',audit)

if __name__=='__main__':
 if len(sys.argv)>1 and sys.argv[1]=='--fixture':child(sys.argv[2],sys.argv[3])
 else:unittest.main()
