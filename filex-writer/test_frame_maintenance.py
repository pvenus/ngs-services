import base64,copy,hashlib,unittest
from test_fenced_store import Tests as Fixture
from fenced_store import FencedOwnedNPCStore
from npc_store import NPC_PREFIX,digest
from server import Conflict
class Maintenance(unittest.TestCase):
 setUp=Fixture.setUp
 tearDown=Fixture.tearDown
 def publish_frames(self):
  self.s.finish(self.g);self.original='frames.req.fixture';self.g=self.s.admit('npc',self.gen,self.original)
  self.hashes=[]
  for raw in (b'final',b'raw',b'alpha'):
   h=hashlib.sha256(raw).hexdigest();self.hashes.append(h);self.s.put_asset(h+'.png',raw,self.g)
  self.manifest={'requestId':self.original,'directions':{'n':{'frames':[dict(index=0,**{k:self.hashes[i]+'.png'for i,k in enumerate(('assetId','rawAssetId','alphaAssetId'))})]}}}
  path=NPC_PREFIX+'npc/animation-frames/idle/run.json';self.s.batch(self.original,'input-hash',[{'path':path,'expectedHash':None,'body':self.manifest}],{path:None},self.g);self.s.finish(self.g)
  self.cmd={'npcId':'npc','generationId':self.gen,'originalRequestId':self.original,'maintenanceId':'frame.cleanup.fixture','expectedSourceVersion':self.s.snapshot()['sourceVersion'],'expectedManifestHash':digest(self.manifest)}
 def begin(self):self.publish_frames();return self.s.begin_frame_maintenance(self.cmd)['guard']
 def test_finished_original_preserved_and_idempotent_scope(self):
  g=self.begin();finished=copy.deepcopy(self.s.state('npc')['finished']);restart=FencedOwnedNPCStore(self.root)
  self.assertEqual(restart.begin_frame_maintenance(self.cmd)['guard'],g)
  with self.assertRaises(Conflict):restart.begin_frame_maintenance({**self.cmd,'expectedManifestHash':'a'*64})
  self.assertEqual(restart.generation('npc')['activeMaintenance'],1);self.assertEqual(restart.generation('npc')['activeLeases'],0)
  self.assertTrue(restart.finish_frame_maintenance(g)['released']);self.assertTrue(restart.finish_frame_maintenance(g)['released'])
  self.assertTrue(restart.begin_frame_maintenance(self.cmd)['released']);self.assertEqual(restart.state('npc')['finished'],finished)
 def test_cleanup_only_fences_workers_and_lifecycle(self):
  g=self.begin()
  calls=[lambda:self.s.admit('npc',self.gen,'new'),lambda:self.s.finish(self.g),lambda:self.s.put_asset(hashlib.sha256(b'x').hexdigest()+'.png',b'x',g),lambda:self.s.prepare_delete('npc',self.gen,'delete','a'*64),lambda:self.s.stage_delete('npc',self.gen,'delete',self.cmd['expectedSourceVersion']),lambda:self.s.prepare_create('npc','create',self.cmd['expectedSourceVersion']),lambda:self.s.checked_lifecycle(g,'create','create',self.cmd['expectedSourceVersion'])]
  for call in calls:
   with self.subTest(call=call):
    with self.assertRaises((Conflict,ValueError,KeyError)):call()
  with self.assertRaises(Conflict):self.s.reserve_frame_maintenance(g,'f'*64,'a'*64)
 def test_reservation_restart_release_finish(self):
  g=self.begin();s=FencedOwnedNPCStore(self.root);p=s.reserve_frame_maintenance(g,self.hashes[0],'a'*64)
  self.assertEqual(hashlib.sha256(base64.b64decode(p['base64'])).hexdigest(),self.hashes[0]);self.assertEqual(p,s.reserve_frame_maintenance(g,self.hashes[0],'a'*64));self.assertEqual(s.generation('npc')['cleanupReservations'],1)
  with self.assertRaises(Conflict):s.finish_frame_maintenance(g)
  with self.assertRaises(Conflict):s.release_frame_maintenance(g,'a'*64,'bad')
  with self.assertRaises(Conflict):s.reserve_frame_maintenance(g,self.hashes[1],'a'*64)
  s.release_frame_maintenance(g,'a'*64,p['reservationToken']);s.release_frame_maintenance(g,'a'*64,p['reservationToken'])
  with self.assertRaises(Conflict):s.reserve_frame_maintenance(g,self.hashes[0],'a'*64)
  s.finish_frame_maintenance(g);self.assertEqual(s.generation('npc')['activeMaintenance'],0);self.assertTrue(s.release_frame_maintenance(g,'a'*64,p['reservationToken'])['released'])
 def test_active_work_foreign_guard_and_stale_scope(self):
  self.publish_frames();work=self.s.admit('npc',self.gen,'other')
  with self.assertRaises(Conflict):self.s.begin_frame_maintenance(self.cmd)
  self.s.finish(work)
  with self.assertRaises(Conflict):self.s.begin_frame_maintenance({**self.cmd,'expectedSourceVersion':'a'*64})
  g=self.s.begin_frame_maintenance(self.cmd)['guard']
  for field in ('originalRequestId','generationId','maintenanceToken','scopeHash'):
   with self.subTest(field=field):
    with self.assertRaises(Conflict):self.s.finish_frame_maintenance({**g,field:'bad'})
 def test_superseded_manifest_rejected(self):
  self.publish_frames();g=self.s.admit('npc',self.gen,'new');snap=self.s.snapshot();path=NPC_PREFIX+'npc/animation-frames/idle/run.json';m={**self.manifest,'requestId':'frames.req.new'}
  self.s.batch('new','new-hash',[{'path':path,'expectedHash':snap['hashes']['npc/animation-frames/idle/run.json'],'body':m}],{path:snap['hashes']['npc/animation-frames/idle/run.json']},g);self.s.finish(g)
  with self.assertRaisesRegex(Conflict,'current frame manifest'):self.s.begin_frame_maintenance({**self.cmd,'expectedSourceVersion':self.s.snapshot()['sourceVersion']})
 def test_original_must_be_finished_and_frames_published(self):
  self.publish_frames()
  with self.assertRaises(Conflict):self.s.begin_frame_maintenance({**self.cmd,'originalRequestId':'frames.req.unknown'})
  with self.assertRaises(ValueError):self.s.begin_frame_maintenance({**self.cmd,'guard':{}})
 def test_same_request_id_rewritten_manifest_not_authorized(self):
  self.publish_frames();g=self.s.admit('npc',self.gen,'rewrite');snap=self.s.snapshot();path=NPC_PREFIX+'npc/animation-frames/idle/run.json';m={**self.manifest,'note':'rewritten'}
  self.s.batch('rewrite','new-hash',[{'path':path,'expectedHash':snap['hashes']['npc/animation-frames/idle/run.json'],'body':m}],{path:snap['hashes']['npc/animation-frames/idle/run.json']},g);self.s.finish(g)
  with self.assertRaisesRegex(Conflict,'not original publication'):self.s.begin_frame_maintenance({**self.cmd,'expectedSourceVersion':self.s.snapshot()['sourceVersion'],'expectedManifestHash':digest(m)})
 def test_migration_and_generic_authority_rejected(self):
  g=self.begin();p={'npcId':'npc','requestId':'migration','expectedSourceVersion':self.cmd['expectedSourceVersion'],'changedDocuments':{},'importAssets':[]};p['planHash']=digest(p)
  with self.assertRaisesRegex(Conflict,'global idle'):self.s.migration_prepare(p)
  with self.assertRaises((Conflict,KeyError)):self.s.batch('unauthorized','h',[],{},g)
  with self.assertRaises((Conflict,KeyError)):self.s.finish(g)
 def test_concurrent_admission_has_one_winner(self):
  import threading
  self.publish_frames();barrier=threading.Barrier(2);out=[]
  def task(maintenance):
   store=FencedOwnedNPCStore(self.root);barrier.wait()
   try:store.begin_frame_maintenance(self.cmd)if maintenance else store.admit('npc',self.gen,'racer');out.append('ok')
   except Conflict:out.append('blocked')
  threads=[threading.Thread(target=task,args=(v,))for v in (True,False)]
  for t in threads:t.start()
  for t in threads:t.join(5)
  self.assertEqual(sorted(out),['blocked','ok'])
 def test_concurrent_maintenance_scopes_have_one_winner(self):
  import threading
  self.publish_frames();barrier=threading.Barrier(2);out=[]
  def task(key):
   store=FencedOwnedNPCStore(self.root);barrier.wait()
   try:store.begin_frame_maintenance({**self.cmd,'maintenanceId':key});out.append('ok')
   except Conflict:out.append('blocked')
  threads=[threading.Thread(target=task,args=('frame.cleanup.'+v,))for v in ('one','two')]
  for t in threads:t.start()
  for t in threads:t.join(5)
  self.assertEqual(sorted(out),['blocked','ok'])

 def test_reservation_does_not_rehash_all_assets(self):
  g=self.begin()
  def forbidden():raise AssertionError('whole snapshot per artifact')
  self.s.snapshot_unlocked=forbidden
  p=self.s.reserve_frame_maintenance(g,self.hashes[0],'a'*64)
  self.assertTrue(p['currentSlot'])

del Fixture
if __name__=='__main__':unittest.main()
