import copy,unittest
from test_fenced_store import Tests as Fixture
from fenced_store import FencedOwnedNPCStore
from npc_store import NPC_PREFIX
from server import Conflict
class LifecycleTests(unittest.TestCase):
 setUp=Fixture.setUp
 tearDown=Fixture.tearDown
 publish=Fixture.publish
 def deleted(self):
  self.publish();self.s.finish(self.g);self.s.prepare_delete('npc',self.gen,'delete','a'*64);snap=self.s.snapshot()
  return self.s.delete_character('delete','dh','npc',snap['sourceVersion'],{'characterOrder':[],'generationEpoch':'delete'},{'generationId':self.gen})
 def create_packet(self,owner='new',rid='create'):
  snap=self.s.snapshot();root=copy.deepcopy(snap['documents']['metadata.json']);root['characterOrder'].append(owner);root['generationEpoch']=rid
  files=[{'path':NPC_PREFIX+'metadata.json','expectedHash':snap['hashes']['metadata.json'],'body':root},{'path':NPC_PREFIX+owner+'/metadata.json','expectedHash':None,'body':{'id':owner}}]
  return snap,files,{x['path']:x['expectedHash']for x in files}
 def test_create_new_and_replay(self):
  snap,files,read=self.create_packet();guard=self.s.prepare_create('new','create',snap['sourceVersion'])
  self.assertEqual(guard,self.s.prepare_create('new','create',snap['sourceVersion']))
  with self.assertRaises(Conflict):self.s.admit('new',guard['generationId'],'too-soon')
  receipt=self.s.create_character('create','h',files,read,snap['sourceVersion'],guard)
  self.assertEqual(self.s.create_character('create','h',files,read,snap['sourceVersion'],guard),receipt)
  self.assertEqual(self.s.generation('new')['state'],'active');self.assertEqual(receipt['generationId'],guard['generationId'])
 def test_restore_new_generation_old_parent_denied(self):
  deleted=self.deleted();snap=self.s.snapshot();guard=self.s.prepare_restore('npc','restore',deleted['backupId'],snap['sourceVersion'])
  self.assertNotEqual(guard['generationId'],self.gen)
  r=self.s.restore_character('restore','rh',deleted['backupId'],snap['sourceVersion'],{'characterOrder':['npc'],'generationEpoch':'restore'},guard)
  self.assertEqual(r['generationId'],guard['generationId']);self.assertEqual(self.s.generation('npc')['state'],'active')
  with self.assertRaises(Conflict):self.s.admit('npc',self.gen,'late')
  with self.assertRaises(Conflict):self.s.admit('npc',guard['generationId'],'r')
  with self.assertRaises(Conflict):self.s.put_asset('a'*64+'.png',b'x',self.g)
 def test_recreate_retires_old_requests(self):
  self.deleted();snap,files,read=self.create_packet('npc','recreate');g=self.s.prepare_create('npc','recreate',snap['sourceVersion']);r=self.s.create_character('recreate','h',files,read,snap['sourceVersion'],g)
  self.assertNotEqual(r['generationId'],self.gen)
  with self.assertRaises(Conflict):self.s.admit('npc',r['generationId'],'r')
 def test_restore_wrong_backup_owner_blocked(self):
  d=self.deleted()
  with self.assertRaises(Conflict):self.s.prepare_restore('other','restore',d['backupId'],self.s.snapshot()['sourceVersion'])
 def test_active_owner_cannot_prepare(self):
  with self.assertRaises(Conflict):self.s.prepare_create('npc','create',self.s.snapshot()['sourceVersion'])
 def test_root_scope_tampering_blocked(self):
  snap,files,read=self.create_packet();g=self.s.prepare_create('new','create',snap['sourceVersion']);files[0]['body']['characterOrder']=['new']
  with self.assertRaises(Conflict):self.s.create_character('create','h',files,read,snap['sourceVersion'],g)
  self.assertEqual(self.s.snapshot()['documents']['metadata.json']['characterOrder'],['npc'])
 def test_restore_crash_activates_only_in_same_recovery(self):
  d=self.deleted();snap=self.s.snapshot();g=self.s.prepare_restore('npc','restore',d['backupId'],snap['sourceVersion']);recover=self.s.recover_owned;self.s.recover_owned=lambda:None
  self.s.restore_character('restore','h',d['backupId'],snap['sourceVersion'],{'characterOrder':['npc'],'generationEpoch':'restore'},g);self.s.recover_owned=recover
  other=FencedOwnedNPCStore(self.root);self.assertEqual(other.generation('npc')['generationId'],g['generationId']);self.assertEqual(other.generation('npc')['state'],'active');self.assertTrue(other.owner_files('npc'));self.assertEqual(other.receipt('restore')['generationId'],g['generationId'])
 def test_backup_preview_verified_owner_json_only(self):
  receipt=self.deleted();p=self.s.backup_preview(receipt['backupId'])
  self.assertTrue(p['backupVerified']);self.assertEqual(p['characterId'],'npc');self.assertEqual(p['backupManifestHash'],receipt['backupManifestHash']);self.assertEqual(set(p['documents']),{'npc/metadata.json'})
  (self.root/'.deleted'/receipt['backupId']/'files/npc/assets/portraits/combat/pixel.png').write_bytes(b'bad')
  with self.assertRaises(Conflict):self.s.backup_preview(receipt['backupId'])
del Fixture
if __name__=='__main__':unittest.main()
