import unittest,tempfile,hashlib
from pathlib import Path
from fenced_store import FencedOwnedNPCStore
from npc_store import encoded,NPC_PREFIX
from server import Conflict
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'npc').mkdir();(self.root/'metadata.json').write_bytes(encoded({'characterOrder':['npc'],'generationEpoch':'init'}));(self.root/'npc/metadata.json').write_bytes(encoded({'id':'npc'}));self.s=FencedOwnedNPCStore(self.root);self.gen=self.s.bootstrap('npc',self.s.snapshot()['sourceVersion'])['generationId'];self.g=self.s.admit('npc',self.gen,'r')
 def tearDown(self):self.tmp.cleanup()
 def publish(self):
  b=b'pixels';a=hashlib.sha256(b).hexdigest()+'.png';self.s.put_asset(a,b,self.g);key=NPC_PREFIX+'npc/metadata.json';snap=self.s.snapshot();files=[{'path':key,'expectedHash':snap['hashes']['npc/metadata.json'],'body':{'id':'npc','portraitSets':{'combat':{'pixelAssetId':a}}}}];read={key:snap['hashes']['npc/metadata.json']};return self.s.batch('r','h',files,read,self.g)
 def test_legacy_client_closed(self):
  with self.assertRaises(ValueError):self.s.put_asset('a'*64+'.png',b'x')
 def test_active_lease_blocks_delete(self):
  with self.assertRaises(Conflict):self.s.prepare_delete('npc',self.gen,'d','a'*64)
 def test_repeated_admission_same_lease(self):self.assertEqual(self.g,self.s.admit('npc',self.gen,'r'))
 def test_cross_process_fence(self):
  other=FencedOwnedNPCStore(self.root);self.s.finish(self.g);other.prepare_delete('npc',self.gen,'d','a'*64)
  with self.assertRaises(Conflict):self.s.admit('npc',self.gen,'late')
  with self.assertRaises(Conflict):self.s.put_asset('a'*64+'.png',b'x',self.g)
 def test_atomic_delete_and_receipt(self):
  self.publish();self.s.finish(self.g);self.s.prepare_delete('npc',self.gen,'d','a'*64);snap=self.s.snapshot();r=self.s.delete_character('d','h','npc',snap['sourceVersion'],{'characterOrder':[],'generationEpoch':'d'},{'generationId':self.gen});self.assertEqual(r['generationId'],self.gen);self.assertEqual(r['externalOwnerManifestHash'],'a'*64);self.assertEqual(self.s.generation('npc')['state'],'deleted');self.assertTrue(r['backupVerified'])
  with self.assertRaises(Conflict):self.s.admit('npc',self.gen,'late')
  with self.assertRaises(ValueError):self.s.restore_character('x')
 def test_publish_and_release(self):
  self.assertEqual(self.publish()['status'],'committed');self.s.finish(self.g)
  with self.assertRaises(Conflict):self.publish()
 def test_delete_crash_rollforward_keeps_tombstone(self):
  self.publish();self.s.finish(self.g);self.s.prepare_delete('npc',self.gen,'d','a'*64);snap=self.s.snapshot();recover=self.s.recover_owned;self.s.recover_owned=lambda:None
  self.s.delete_character('d','h','npc',snap['sourceVersion'],{'characterOrder':[],'generationEpoch':'d'},{'generationId':self.gen});self.s.recover_owned=recover
  other=FencedOwnedNPCStore(self.root);self.assertEqual(other.generation('npc')['state'],'deleted');self.assertEqual(other.receipt('d')['externalOwnerManifestHash'],'a'*64);self.assertFalse(other.owner_files('npc'))
 def test_http_auth_and_missing_guard(self):
  import threading,json,urllib.request,urllib.error
  from http.server import ThreadingHTTPServer
  from server import handler
  token='t'*32;server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.s,token));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   url='http://127.0.0.1:'+str(server.server_port)
   with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(url+'/v1/snapshot')
   self.assertEqual(e.exception.code,401)
   request=urllib.request.Request(url+'/v1/asset',data=json.dumps({'assetId':'a'*64+'.png','base64':'eA=='}).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
   with self.assertRaises(urllib.error.HTTPError) as e:urllib.request.urlopen(request)
   self.assertEqual(e.exception.code,400)
   request=urllib.request.Request(url+'/v1/generation?npcId=npc',headers={'Authorization':'Bearer '+token})
   self.assertEqual(json.load(urllib.request.urlopen(request))['generationId'],self.gen)
  finally:server.shutdown();server.server_close();thread.join()
 def test_prepare_replay_binding(self):
  self.s.finish(self.g);self.s.prepare_delete('npc',self.gen,'d','a'*64);self.s.prepare_delete('npc',self.gen,'d','a'*64)
  with self.assertRaises(Conflict):self.s.prepare_delete('npc',self.gen,'d','b'*64)

class DynamicVideoFenceTests(Tests):
 def test_walk_path_still_cannot_publish_other_owner(self):
  key=NPC_PREFIX+'other/animations/idle/walk/n.json'
  before=self.s.snapshot()['sourceVersion']
  with self.assertRaisesRegex(ValueError,'single owner'):
   self.s.batch('other-request','h',[{'path':key,'expectedHash':None,'body':{}}],{key:None},self.g)
  self.assertEqual(self.s.snapshot()['sourceVersion'],before)
 def test_walk_guard_cannot_be_omitted(self):
  key=NPC_PREFIX+'npc/animations/idle/walk/n.json'
  with self.assertRaises(ValueError):self.s.batch('no-guard','h',[{'path':key,'expectedHash':None,'body':{}}],{key:None})
 def test_walk_video_commits_and_replays_in_same_owner(self):
  data=b'fixture-mp4-existing-output';aid=hashlib.sha256(data).hexdigest()+'.mp4';self.s.put_asset(aid,data,self.g)
  snap=self.s.snapshot();meta=NPC_PREFIX+'npc/metadata.json';video=NPC_PREFIX+'npc/animations/idle/walk/n.json'
  read={meta:snap['hashes']['npc/metadata.json'],video:None}
  files=[{'path':meta,'expectedHash':read[meta],'body':{'id':'npc','animationSlotKeys':['idle/walk/n']}},{'path':video,'expectedHash':None,'body':{'assetId':aid}}]
  first=self.s.batch('walk-fixture','hash',files,read,self.g)
  self.assertEqual(self.s.batch('walk-fixture','hash',files,read,self.g),first)
  self.assertEqual((self.root/'npc/assets/animations/idle/walk/n/video.mp4').read_bytes(),data)

class DynamicFrameFenceTests(Tests):
 def test_walk_frame_owner_and_guard_remain_required(self):
  for owner,guard in (('other',self.g),('npc',None)):
   key=NPC_PREFIX+owner+'/animation-frames/idle/walk.json'
   with self.assertRaises(ValueError):self.s.batch('frame-denied','h',[{'path':key,'expectedHash':None,'body':{}}],{key:None},guard)
 def test_walk_frame_manifest_commits_and_replays(self):
  snap=self.s.snapshot();meta=NPC_PREFIX+'npc/metadata.json';frame=NPC_PREFIX+'npc/animation-frames/idle/walk.json'
  read={meta:snap['hashes']['npc/metadata.json'],frame:None}
  manifest={'posePreset':'idle','presetId':'walk','directions':{}}
  files=[{'path':meta,'expectedHash':read[meta],'body':{'id':'npc','animationFrameSetKeys':['idle/walk']}},{'path':frame,'expectedHash':None,'body':manifest}]
  receipt=self.s.batch('frame-fixture','h',files,read,self.g)
  self.assertEqual(self.s.batch('frame-fixture','h',files,read,self.g),receipt)
  self.assertEqual(__import__('json').loads((self.root/'npc/animation-frames/idle/walk.json').read_text()),manifest)

if __name__=='__main__':unittest.main()
