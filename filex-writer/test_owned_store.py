import unittest,tempfile,json,hashlib
from pathlib import Path
from owned_store import OwnedNPCStore
from npc_store import NPC_PREFIX,encoded,digest
from server import Conflict
class Tests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.root=Path(self.t.name);self.s=OwnedNPCStore(self.root)
  (self.root/'metadata.json').write_bytes(encoded({'characterOrder':['npc'],'generationEpoch':'init'}));(self.root/'npc').mkdir();(self.root/'npc/metadata.json').write_bytes(encoded({'id':'npc','portraitSets':{}}))
 def tearDown(self):self.t.cleanup()
 def put(self,b):
  a=hashlib.sha256(b).hexdigest()+'.png';self.s.put_asset(a,b);return a
 def commit(self,a,rid='request1'):
  snap=self.s.snapshot();key=NPC_PREFIX+'npc/metadata.json';body={'id':'npc','portraitSets':{'combat':{'pixelAssetId':a}}}
  return self.s.batch(rid,rid,[{'path':key,'expectedHash':snap['hashes']['npc/metadata.json'],'body':body}],{key:snap['hashes']['npc/metadata.json']})
 def test_current_slot_replace(self):
  a=self.put(b'first');self.commit(a);b=self.put(b'second');self.commit(b,'request2');p=self.root/'npc/assets/portraits/combat/pixel.png'
  self.assertEqual(p.read_bytes(),b'second');self.assertEqual(len(list((self.root/'npc/assets').rglob('*.png'))),1);self.assertEqual([x['assetId']for x in self.s.snapshot()['assets']],[b])
 def test_crash_rollforward(self):
  a=self.put(b'first');original=self.s.recover_owned
  self.s.recover_owned=lambda:None
  self.commit(a);self.assertTrue((self.root/'.owned-tx.json').exists());self.s.recover_owned=original
  self.assertEqual(self.s.snapshot()['assets'][0]['assetId'],a);self.assertFalse((self.root/'.owned-tx.json').exists())
 def test_stale_cas(self):
  a=self.put(b'x');self.commit(a)
  with self.assertRaises(Conflict):self.s.batch('different','h',[{'path':NPC_PREFIX+'npc/metadata.json','expectedHash':None,'body':{}}],{NPC_PREFIX+'npc/metadata.json':None})
 def test_delete_restore_complete_bytes(self):
  a=self.put(b'video-and-frame-fixture');self.commit(a);before=self.s.owner_files('npc');snap=self.s.snapshot();meta={'characterOrder':[],'generationEpoch':'delete1'}
  receipt=self.s.delete_character('delete1','h','npc',snap['sourceVersion'],meta)
  self.assertFalse(self.s.owner_files('npc'));self.assertEqual(self.s.snapshot()['assets'],[])
  snap=self.s.snapshot();self.s.restore_character('restore1','h',receipt['backupId'],snap['sourceVersion'],{'characterOrder':['npc'],'generationEpoch':'restore1'})
  self.assertEqual(self.s.owner_files('npc'),before);self.assertEqual(self.s.asset(a)['sha256'],a[:-4]);self.assertTrue((self.root/'.deleted'/receipt['backupId']/'manifest.json').exists())
 def test_corrupt_backup_blocks_restore(self):
  a=self.put(b'x');self.commit(a);snap=self.s.snapshot();r=self.s.delete_character('d','h','npc',snap['sourceVersion'],{'characterOrder':[],'generationEpoch':'d'})
  (self.root/'.deleted'/r['backupId']/'files/npc/assets/portraits/combat/pixel.png').write_bytes(b'bad')
  with self.assertRaises(Conflict):self.s.restore_character('r','h',r['backupId'],self.s.snapshot()['sourceVersion'],{'characterOrder':['npc'],'generationEpoch':'r'})
 def test_symlink_escape(self):
  (self.root/'npc/assets').symlink_to('/tmp',target_is_directory=True)
  with self.assertRaises(ValueError):self.commit(self.put(b'x'))
 def test_missing_asset_no_metadata_change(self):
  before=self.s.snapshot()['sourceVersion']
  with self.assertRaises(FileNotFoundError):self.commit('f'*64+'.png')
  self.assertEqual(self.s.snapshot()['sourceVersion'],before);self.assertFalse((self.root/'.owned-tx.json').exists())
 def test_replay_same_receipt(self):
  a=self.put(b'x');snapshot=self.s.snapshot();key=NPC_PREFIX+'npc/metadata.json';files=[{'path':key,'expectedHash':snapshot['hashes']['npc/metadata.json'],'body':{'id':'npc','portraitSets':{'combat':{'pixelAssetId':a}}}}];read={key:snapshot['hashes']['npc/metadata.json']}
  first=self.s.batch('replay','h',files,read);self.assertEqual(self.s.batch('replay','h',files,read),first)
  with self.assertRaises(Conflict):self.s.batch('replay','different',files,read)
 def test_pending_consumed_after_commit(self):
  a=self.put(b'x');self.commit(a);self.assertFalse((self.root/'.pending-assets'/a).exists());self.assertEqual(self.s.asset(a)['sha256'],a[:-4])
 def test_stage_corruption_blocks_before_write(self):
  a=self.put(b'x');old=self.s.recover_owned;self.s.recover_owned=lambda:None;before=(self.root/'npc/metadata.json').read_bytes();self.commit(a);self.s.recover_owned=old
  (self.root/'.owned-stage/0').write_bytes(b'corrupt')
  with self.assertRaises(Conflict):self.s.snapshot()
  self.assertEqual((self.root/'npc/metadata.json').read_bytes(),before)
 def test_backup_corrupt_before_recovery_blocks_metadata_write(self):
  a=self.put(b'x');self.commit(a);snap=self.s.snapshot();before=(self.root/'metadata.json').read_bytes();old=self.s.recover_owned;self.s.recover_owned=lambda:None
  r=self.s.delete_character('d','h','npc',snap['sourceVersion'],{'characterOrder':[],'generationEpoch':'d'});self.s.recover_owned=old
  (self.root/'.deleted'/r['backupId']/'files/npc/assets/portraits/combat/pixel.png').write_bytes(b'corrupt')
  with self.assertRaises(Conflict):self.s.snapshot()
  self.assertEqual((self.root/'metadata.json').read_bytes(),before);self.assertTrue((self.root/'npc/assets/portraits/combat/pixel.png').exists())
 def test_other_owner_and_global_unchanged(self):
  (self.root/'other').mkdir();(self.root/'other/metadata.json').write_bytes(encoded({'id':'other'}));m=json.loads((self.root/'metadata.json').read_bytes());m['characterOrder'].append('other');(self.root/'metadata.json').write_bytes(encoded(m))
  (self.root/'assets').mkdir();sentinel=self.root/'assets'/('e'*64+'.png');sentinel.write_bytes(b'preserve')
  a=self.put(b'x');self.commit(a);snap=self.s.snapshot();r=self.s.delete_character('d','h','npc',snap['sourceVersion'],{'characterOrder':['other'],'generationEpoch':'d'})
  self.assertEqual((self.root/'other/metadata.json').read_bytes(),encoded({'id':'other'}));self.assertEqual(sentinel.read_bytes(),b'preserve')
if __name__=='__main__':unittest.main()
