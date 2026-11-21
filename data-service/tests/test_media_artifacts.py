import base64,copy,hashlib,io,json,sys,tempfile,threading,unittest,urllib.request,urllib.error,wave,zlib,struct
from pathlib import Path
from http.server import ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'filex-writer'));sys.path.insert(0,str(ROOT/'data-service'))
from server import handler
from media_store import MediaStore,wire,sha
from project_data_service.http_server import Handler
from project_data_service.media_artifacts import MediaArtifacts,Transport,SCOPE
TOKEN='isolated-fixture-token-not-production-123456'

def packet(kind='music.generate'):
 if kind=='music.generate':
  b=io.BytesIO()
  with wave.open(b,'wb')as f:f.setparams((2,2,48000,0,'NONE','not compressed'));f.writeframes(b'\0'*480000*4)
  data=b.getvalue();mime='audio/wav'
 else:
  def chunk(k,v):return struct.pack('>I',len(v))+k+v+struct.pack('>I',zlib.crc32(k+v)&0xffffffff)
  data=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\xff\0\0\xff'))+chunk(b'IEND',b'');mime='image/png'
 p=dict(requestId='request.test',attemptId='attempt.1',fencingToken="fence.1",capability=kind,projectId='binari',targetId='music.example',expectedRevision="0",requestHash='a'*64,artifact={'sha256':sha(data),'bytes':len(data),'mimeType':mime,'base64':base64.b64encode(data).decode()},provenance={'model':'acestep-xl-sft-lm4b'if kind=='music.generate'else 'codex.image_gen'},claim={'request_id':'request.test','attempt_id':'attempt.1','fencing_token':'fence.1'})
 return p
class Authority:
 def __init__(self,p):self.p=p;self.reserved=None;self.receipt=None;self.fail_finish=False;self.deny=False
 def __call__(self,method,path,body):
  if self.deny:raise ValueError('AUTHORITY_DENIED')
  if path.endswith('/prepare'):
   if body['claim']!=self.p['claim']:raise ValueError('CLAIM_MISMATCH')
   if self.reserved and self.reserved!=body['publicationHash']:raise ValueError('RESERVATION_CONFLICT')
   self.reserved=body['publicationHash'];return {**{k:self.p[k]for k in SCOPE},'publicationHash':self.reserved,'reserved':True}
  if self.fail_finish:raise OSError('injected lost finish')
  if body['publicationHash']!=self.reserved:raise ValueError('HASH_CONFLICT')
  self.receipt=body['receipt'];return {'committed':True,'requestId':self.p['requestId'],'publicationHash':self.reserved}
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name).resolve();self.store=MediaStore(root);token=root/'token';token.write_text(TOKEN);token.chmod(0o600)
  self.writer=ThreadingHTTPServer(('127.0.0.1',0),handler(self.store,TOKEN));threading.Thread(target=self.writer.serve_forever,daemon=True).start()
  self.p=packet();self.authority=Authority(self.p);self.media=MediaArtifacts(self.authority,Transport('http://127.0.0.1:'+str(self.writer.server_port),token),TOKEN)
  self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.media_artifacts=self.media;threading.Thread(target=self.server.serve_forever,daemon=True).start()
 def tearDown(self):
  for s in (self.server,self.writer):s.shutdown();s.server_close()
  self.tmp.cleanup()
 def call(self,path='/v1/media-artifacts/publish',p=None,token=TOKEN,origin=False):
  headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
  if origin:headers['Origin']='http://localhost'
  data=None if p is False else wire(self.p if p is None else p)
  req=urllib.request.Request('http://127.0.0.1:'+str(self.server.server_port)+path,data=data,headers=headers)
  with urllib.request.urlopen(req)as r:return json.load(r)
 def test_wav_http_commit_receipt_and_readback_replay(self):
  r=self.call();self.assertTrue(r['verified']);self.assertEqual(self.call(),r);self.assertEqual(self.call('/v1/media-artifacts/receipts/request.test/attempt.1',False),r)
  a=self.call('/v1/media-artifacts/assets/'+r['artifact']['assetId'],False);self.assertEqual(a['base64'],self.p['artifact']['base64']);self.assertEqual(self.authority.receipt,r)
 def test_image_http_commit(self):
  self.p=packet('image.generate');self.authority.p=self.p;self.assertTrue(self.call()['verified'])
 def test_auth_origin_and_disabled_failclosed(self):
  for kw in ({'token':'wrong'},{'origin':True}):
   with self.assertRaises((urllib.error.HTTPError,urllib.error.URLError))as e:self.call(p={},**kw)
   if isinstance(e.exception,urllib.error.HTTPError):self.assertEqual(e.exception.code,403)
  del self.server.media_artifacts
  with self.assertRaises(urllib.error.HTTPError)as e:self.call(p={})
  self.assertEqual(e.exception.code,503);self.assertFalse((self.store.root/'assets').exists())
 def test_denied_scope_no_writes(self):
  self.authority.deny=True
  with self.assertRaises(urllib.error.HTTPError):self.call()
  self.assertFalse((self.store.root/'assets').exists())
 def test_scope_forge_and_destination_rejected(self):
  for key,value in (('targetId','other'),('fencingToken',2),('requestHash','b'*64),('destination','/tmp/evil')):
   p=copy.deepcopy(self.p);p[key]=value
   with self.assertRaises(urllib.error.HTTPError):self.call(p=p)
   self.authority.reserved=None
  self.assertFalse((self.store.root/'assets').exists())
 def test_bad_hash_and_mime_rejected(self):
  for key,value in (('sha256','0'*64),('bytes',1),('mimeType','image/png')):
   p=copy.deepcopy(self.p);p['artifact'][key]=value
   with self.assertRaises(urllib.error.HTTPError):self.call(p=p)
  self.assertFalse((self.store.root/'assets').exists())
 def test_wav_wrong_profile_rejected(self):
  b=io.BytesIO()
  with wave.open(b,'wb')as f:f.setparams((1,2,44100,0,'NONE','none'));f.writeframes(b'\0'*100)
  data=b.getvalue();self.p['artifact'].update(base64=base64.b64encode(data).decode(),sha256=sha(data),bytes=len(data))
  with self.assertRaises(urllib.error.HTTPError):self.call()
  self.assertFalse((self.store.root/'assets').exists())
 def test_finish_loss_exact_replay_only(self):
  self.authority.fail_finish=True
  with self.assertRaises(urllib.error.HTTPError)as e:self.call()
  self.assertEqual(e.exception.code,503);r=self.store.media_receipt('request.test','attempt.1');self.authority.fail_finish=False
  self.assertEqual(self.call(),r)
  p=copy.deepcopy(self.p);p['provenance']['model']='different'
  with self.assertRaises(urllib.error.HTTPError):self.call(p=p)
 def test_symlink_and_corrupt_asset_denied(self):
  r=self.call();p=self.store.root/'assets'/r['artifact']['assetId'];p.write_bytes(b'corrupt')
  with self.assertRaises(urllib.error.HTTPError):self.call('/v1/media-artifacts/receipts/request.test/attempt.1',False)
 def test_interrupted_asset_commit_intent_replay(self):
  orig=self.store.write
  def fail(path,data):
   if path.parent.name=='receipts':raise OSError('injected receipt failure')
   return orig(path,data)
  self.store.write=fail
  with self.assertRaises(urllib.error.HTTPError):self.call()
  self.store.write=orig;self.assertTrue(self.call()['verified'])
 def test_unicode_claim_canonical_and_validation_copies(self):
  self.p['claim']['parameters']={'prompt':'밤의 마을'}
  self.assertTrue(self.call()['verified'])
  self.assertEqual((ROOT/'filex-writer/media_validation.py').read_bytes(),(ROOT/'data-service/project_data_service/media_validation.py').read_bytes())
 def test_empty_png_pixels_rejected_before_reservation(self):
  self.p=packet('image.generate');self.authority.p=self.p
  def chunk(k,v):return struct.pack('>I',len(v))+k+v+struct.pack('>I',zlib.crc32(k+v)&0xffffffff)
  data=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b''))+chunk(b'IEND',b'')
  self.p['artifact'].update(base64=base64.b64encode(data).decode(),sha256=sha(data),bytes=len(data))
  with self.assertRaises(urllib.error.HTTPError):self.call()
  self.assertIsNone(self.authority.reserved)
 def test_receipt_corruption_and_asset_path_denied(self):
  self.call();rp=next((self.store.root/'receipts').glob('*.json'));r=json.loads(rp.read_text());r['targetId']='forged';rp.write_bytes(wire(r))
  with self.assertRaises(urllib.error.HTTPError):self.call('/v1/media-artifacts/receipts/request.test/attempt.1',False)
  with self.assertRaises(ValueError):self.media.asset('../outside')
 def test_symlink_asset_directory_denied(self):
  with tempfile.TemporaryDirectory()as other:
   (self.store.root/'assets').symlink_to(other,target_is_directory=True)
   with self.assertRaises(urllib.error.HTTPError):self.call()
   self.assertEqual(list(Path(other).iterdir()),[])
 def test_indexed_png_rejected_before_reservation(self):
  self.p=packet('image.generate');self.authority.p=self.p
  def chunk(k,v):return struct.pack('>I',len(v))+k+v+struct.pack('>I',zlib.crc32(k+v)&0xffffffff)
  data=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,3,0,0,0))+chunk(b'PLTE',b'')+chunk(b'IDAT',zlib.compress(b'\0\0'))+chunk(b'IEND',b'')
  self.p['artifact'].update(base64=base64.b64encode(data).decode(),sha256=sha(data),bytes=len(data))
  with self.assertRaises(urllib.error.HTTPError):self.call()
  self.assertIsNone(self.authority.reserved)
if __name__=='__main__':unittest.main()
