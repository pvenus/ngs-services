import copy,hashlib,unittest
from test_fenced_store import Tests as Fixture
from fenced_store import FencedOwnedNPCStore
from npc_store import digest
from server import Conflict
class MigrationTests(unittest.TestCase):
 setUp=Fixture.setUp
 tearDown=Fixture.tearDown
 def packet(self):
  self.s.finish(self.g);b=b'new-source';aid=hashlib.sha256(b).hexdigest()+'.png'
  p={'npcId':'npc','requestId':'migration-one','expectedSourceVersion':self.s.snapshot()['sourceVersion'],'changedDocuments':{'metadata.json':{'id':'npc','portraitSets':{'combat':{'pixelAssetId':aid}}}},'importAssets':[{'assetId':aid,'sha256':aid[:-4]}]};p['planHash']=digest(p);return p,aid,b
 def test_import_snapshot_commit_replay(self):
  p,aid,b=self.packet();before=self.s.snapshot();r=self.s.migration_prepare(p)
  self.assertEqual(self.s.migration_prepare(p),r)
  with self.assertRaises(Conflict):self.s.admit('npc',self.gen,'new')
  with self.assertRaises(Conflict):self.s.prepare_create('other','new',before['sourceVersion'])
  self.s.migration_asset(p['requestId'],r['token'],aid,b)
  result=self.s.migration_commit(p['requestId'],r['token'])
  self.assertEqual(result,self.s.migration_commit(p['requestId'],r['token']))
  m,files=self.s.verified_backup(result['backupId']);self.assertEqual(files['imports/'+aid],b)
  self.assertEqual(files['metadata.json'],(self.root/'.deleted'/result['backupId']/'files/metadata.json').read_bytes())
  self.assertEqual(self.s.asset_path(aid).read_bytes(),b);self.assertTrue(result['backupVerified']);self.assertEqual(self.s.generation('npc')['state'],'active')
  self.s.admit('npc',self.gen,'after')
 def test_global_busy_blocks_prepare(self):
  p,aid,b=self.packet();self.s.admit('npc',self.gen,'busy')
  with self.assertRaises(Conflict):self.s.migration_prepare(p)
 def test_tamper_or_missing_import_fails_closed(self):
  p,aid,b=self.packet();r=self.s.migration_prepare(p)
  with self.assertRaises(Conflict):self.s.migration_asset(p['requestId'],r['token'],aid,b'wrong')
  with self.assertRaises(FileNotFoundError):self.s.migration_commit(p['requestId'],r['token'])
  self.assertEqual(self.s.snapshot()['sourceVersion'],p['expectedSourceVersion'])
 def test_wrong_token_and_changed_plan(self):
  p,aid,b=self.packet();r=self.s.migration_prepare(p)
  with self.assertRaises(Conflict):self.s.migration_commit(p['requestId'],'wrong')
  q=copy.deepcopy(p);q['changedDocuments']['metadata.json']['name']='changed';q['planHash']=digest({k:v for k,v in q.items()if k!='planHash'})
  with self.assertRaises(Conflict):self.s.migration_prepare(q)
 def test_crash_rollforward_includes_fence_and_snapshot(self):
  p,aid,b=self.packet();r=self.s.migration_prepare(p);self.s.migration_asset(p['requestId'],r['token'],aid,b)
  recovery=self.s.recover_owned;self.s.recover_owned=lambda:None
  result=self.s.migration_commit(p['requestId'],r['token']);self.s.recover_owned=recovery
  other=FencedOwnedNPCStore(self.root);self.assertEqual(other.migration_status(p['requestId'])['state'],'committed')
  self.assertEqual(other.migration_commit(p['requestId'],r['token']),result);self.assertEqual(other.asset_path(aid).read_bytes(),b)
 def test_path_escape_rejected(self):
  p,aid,b=self.packet();p['changedDocuments']['../metadata.json']={};p['planHash']=digest({k:v for k,v in p.items()if k!='planHash'})
  with self.assertRaises(ValueError):self.s.migration_prepare(p)
 def test_migration_snapshot_not_ordinary_restore(self):
  from owned_store import OwnedNPCStore
  p,aid,b=self.packet();r=self.s.migration_prepare(p);self.s.migration_asset(p['requestId'],r['token'],aid,b)
  done=self.s.migration_commit(p['requestId'],r['token']);bid=done['backupId']
  self.s.verified_backup(bid) # migration/recovery verification remains supported
  with self.assertRaisesRegex(Conflict,'not restorable'):self.s.backup_preview(bid)
  with self.assertRaisesRegex(Conflict,'not restorable'):self.s.prepare_restore('npc','restore',bid,self.s.snapshot()['sourceVersion'])
  with self.assertRaisesRegex(Conflict,'not restorable'):OwnedNPCStore.restore_character(self.s,'restore','h',bid,self.s.snapshot()['sourceVersion'],{})
 def test_owned_schema_cannot_smuggle_global_bytes(self):
  from npc_store import encoded
  p,aid,b=self.packet();r=self.s.migration_prepare(p);self.s.migration_asset(p['requestId'],r['token'],aid,b)
  done=self.s.migration_commit(p['requestId'],r['token']);bid=done['backupId'];m,_=self.s.verified_backup(bid)
  m['schemaVersion']='npc-owned-backup.v1';self.s.write(self.s.safe('.deleted/'+bid+'/manifest.json'),encoded(m))
  with self.assertRaisesRegex(Conflict,'outside owner'):self.s.backup_preview(bid)
if __name__=='__main__':unittest.main()
