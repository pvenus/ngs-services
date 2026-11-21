import hashlib,io,json,tempfile,unittest,sys,os
from pathlib import Path
from PIL import Image
sys.path.insert(0,os.environ['WORKERS_NATIVE_PNG_SOURCE'])
from workers.resident.backends.animation_native_png import build_native_png_manifest,validate_native_png_manifest
from fenced_store import FencedOwnedNPCStore
from npc_store import encoded,NPC_PREFIX,digest
from server import Conflict
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'npc').mkdir();(self.root/'metadata.json').write_bytes(encoded({'characterOrder':['npc']}));(self.root/'npc/metadata.json').write_bytes(encoded({'id':'npc'}))
  self.s=FencedOwnedNPCStore(self.root);self.gen=self.s.bootstrap('npc',self.s.snapshot()['sourceVersion'])['generationId'];self.g=self.s.admit('npc',self.gen,'parent');self.raw=[]
  for color in ('red','blue'):
   out=io.BytesIO();Image.new('RGB',(8,6),color).save(out,format='PNG');self.raw.append(out.getvalue())
  self.binding=dict(requestId='direction.n',npcId='npc',generationId=self.gen,posePreset='idle',presetId='walk',direction='n',sourceBindingHash='a'*64)
  self.m=build_native_png_manifest(self.binding,self.raw,frame_count=2,fps=24);self.path=NPC_PREFIX+'npc/animation-sequences/idle/walk/n.json'
 def tearDown(self):self.tmp.cleanup()
 def stage(self,count=2):
  for row,data in zip(self.m['frames'][:count],self.raw):self.s.put_asset(row['assetId'],data,self.g)
 def commit(self,m=None):
  m=m or self.m
  return self.s.batch('direction.n',digest(m),[dict(path=self.path,expectedHash=None,body=m)],{self.path:None},self.g)
 def test_actual_producer_fenced_commit_read_validate_replay(self):
  self.stage();r=self.commit();self.assertEqual(self.commit(),r);self.assertEqual(r['status'],'committed')
  data=[(self.root/f'npc/assets/animation-sequences/idle/walk/n/raw-{i}.png').read_bytes()for i in range(2)]
  m=json.loads((self.root/'npc/animation-sequences/idle/walk/n.json').read_bytes());validate_native_png_manifest(m,self.binding,data,frame_count=2,fps=24)
  self.assertEqual(data,self.raw);self.assertFalse(list(self.root.rglob('*.mp4')))
 def test_missing_last_frame_no_manifest_receipt(self):
  self.stage(1)
  with self.assertRaises(FileNotFoundError):self.commit()
  self.assertFalse((self.root/'npc/animation-sequences/idle/walk/n.json').exists());self.assertFalse(self.s.receipt_path('direction.n').exists())
 def test_order_rejected(self):
  self.stage();m=dict(self.m,frames=list(reversed(self.m['frames'])))
  with self.assertRaises(ValueError):self.commit(m)
  self.assertFalse(self.s.receipt_path('direction.n').exists())
 def test_stale_guard_rejected(self):
  self.stage();self.s.finish(self.g)
  with self.assertRaises(Conflict):self.commit()
 def test_crash_journal_recovery_receipt_last(self):
  self.stage();recover=self.s.recover_owned;self.s.recover_owned=lambda:None;self.commit()
  self.assertFalse(self.s.receipt_path('direction.n').exists());self.s.recover_owned=recover;self.s.snapshot();self.assertTrue(self.s.receipt_path('direction.n').exists())
  for i,data in enumerate(self.raw):self.assertEqual((self.root/f'npc/assets/animation-sequences/idle/walk/n/raw-{i}.png').read_bytes(),data)

class CrossPC(Tests):
 def test_real_pds_transport_http_separate_roots(self):
  import threading,shutil
  from http.server import ThreadingHTTPServer
  from server import handler
  sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'data-service'))
  from project_data_service.pds_owned_transport import OwnedNPCTransport
  token=self.root/'fixture-token';token.write_text('t'*32);token.chmod(0o600)
  http=ThreadingHTTPServer(('127.0.0.1',0),handler(self.s,'t'*32));thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
  cfg={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','scope':'npc','enabled':True,'ownershipMode':'npc-owned-v1','endpoint':'http://127.0.0.1:'+str(http.server_port),'tokenFile':str(token.resolve())}
  try:
   producer=Path(tempfile.mkdtemp());consumer=Path(tempfile.mkdtemp())
   try:
    with OwnedNPCTransport(cfg,dict(self.g,stageRequestId='direction.n'))as t:
     for row,data in zip(self.m['frames'],self.raw):
      p=producer/row['assetId'];p.write_bytes(data);t.asset_put(row['assetId'],p.read_bytes())
     plan={'files':[dict(path=self.path,expectedHash=None,body=self.m)],'readSet':{self.path:None}}
     receipt=t.batch('direction.n',digest(self.m),plan)
     self.assertEqual(receipt['stageRequestId'],'direction.n')
    shutil.rmtree(producer)
    with OwnedNPCTransport(cfg)as t:
     snapshot=t.snapshot();m=snapshot['documents'][self.path.removeprefix(NPC_PREFIX)]
     self.assertEqual(t.receipt('direction.n'),receipt)
     downloaded=[]
     for row in m['frames']:
      data=t.asset_get(row['assetId']);(consumer/row['assetId']).write_bytes(data);downloaded.append(data)
     validate_native_png_manifest(m,self.binding,downloaded,frame_count=2,fps=24)
     self.assertEqual(downloaded,self.raw)
    self.assertFalse(producer.exists());self.assertEqual(len(list(consumer.glob('*.png'))),2)
   finally:
    if producer.exists():shutil.rmtree(producer)
    shutil.rmtree(consumer)
  finally:http.shutdown();http.server_close();thread.join()
 def test_durable_alpha_and_derived_pixel_keep_raw(self):
  # Mock alpha images only: exercise genuine storage, never inference claims.
  from workers.resident.backends.frame_native_manifests import build_alpha_manifest
  self.stage();self.commit();original={r['assetId']:b for r,b in zip(self.m['frames'],self.raw)}
  rgba=[]
  for b in self.raw:
   out=io.BytesIO();Image.open(io.BytesIO(b)).convert('RGBA').save(out,format='PNG');rgba.append(out.getvalue())
  alpha=build_alpha_manifest(self.m,self.raw,rgba,dependency_hash='b'*64,raw_manifest_id=self.path,alpha_request_id='alpha.n',inference={'engine':'fixture','modelId':'mock','modelDigest':'c'*64,'policyHash':'d'*64,'inferenceRequestId':'alpha.n','fixture':True})
  for row,data in zip(alpha['frames'],rgba):self.s.put_asset(row['alphaAssetId'],data,self.g)
  key=NPC_PREFIX+'npc/animation-alpha/idle/walk/n.json'
  self.s.batch('alpha.n',digest(alpha),[dict(path=key,expectedHash=None,body=alpha)],{key:None},dict(self.g,stageRequestId='alpha.n'))
  # Existing final frame ownership shape, not a new published domain receipt.
  pixel=self.raw[0];aid=hashlib.sha256(pixel).hexdigest()+'.png';self.s.put_asset(aid,pixel,self.g)
  final={'directions':{'n':{'frames':[{'index':0,'assetId':aid,'rawAssetId':self.m['frames'][0]['assetId'],'alphaAssetId':alpha['frames'][0]['alphaAssetId']}]}}}
  target=NPC_PREFIX+'npc/animation-frames/idle/walk.json'
  self.s.batch('pixels.fixture',digest(final),[dict(path=target,expectedHash=None,body=final)],{target:None},dict(self.g,stageRequestId='pixels.fixture'))
  for asset,data in original.items():
   import base64
   self.assertEqual(base64.b64decode(self.s.asset(asset)['base64']),data)
  self.assertTrue((self.root/'npc/animation-alpha/idle/walk/n.json').exists())
