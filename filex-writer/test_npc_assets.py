import tempfile,unittest,json,hashlib
from pathlib import Path
from npc_store import NPCStore,encoded
from server import Conflict
class AssetsTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=NPCStore(self.tmp.name)
  self.root=self.s.root
  self.s.write(self.root/'metadata.json',encoded({'characterOrder':['a','b'],'_pdsCurrent':{'revision':1}}))
  self.data=b'\x89PNG\r\n\x1a\nfixture';self.a=hashlib.sha256(self.data).hexdigest()+'.png'
  self.s.put_asset(self.a,self.data)
  for name in ('a','b'):self.s.write(self.root/name/'metadata.json',encoded({'id':name,'portraits':[{'assetId':self.a}]}))
 def tearDown(self):self.tmp.cleanup()
 def test_delete_preserves_shared_and_verified_backup(self):
  snap=self.s.snapshot();meta={'characterOrder':['b'],'generationEpoch':'req','_pdsCurrent':{'revision':2}}
  r=self.s.delete_character('req','input','a',snap['sourceVersion'],meta)
  self.assertTrue(r['backupVerified']);self.assertFalse((self.root/'a/metadata.json').exists())
  self.assertEqual(self.s.asset_path(self.a).read_bytes(),self.data)
  self.assertEqual((self.root/'.deleted'/r['backupId']/'files/assets'/self.a).read_bytes(),self.data)
  self.assertEqual(self.s.delete_character('req','input','a',snap['sourceVersion'],meta),r)
 def test_stale_and_missing_asset_abort(self):
  with self.assertRaises(Conflict):self.s.delete_character('x','i','a','old',{})
  snap=self.s.snapshot();self.s.asset_path(self.a).unlink()
  with self.assertRaises(FileNotFoundError):self.s.delete_character('x','i','a',snap['sourceVersion'],{'characterOrder':['b'],'generationEpoch':'x','_pdsCurrent':{}})
  self.assertTrue((self.root/'a/metadata.json').exists())
 def test_recovery_verifies_backup_before_removing(self):
  snap=self.s.snapshot();meta={'characterOrder':['b'],'generationEpoch':'crash','_pdsCurrent':{'revision':2}}
  recover=self.s.recover_delete
  self.s.recover_delete=lambda:None
  with self.assertRaises(FileNotFoundError):self.s.delete_character('crash','i','a',snap['sourceVersion'],meta)
  self.s.recover_delete=recover
  r=self.s.snapshot()
  self.assertEqual(r['documents']['metadata.json']['characterOrder'],['b'])
  self.assertTrue(self.s.receipt('crash')['backupVerified'])
 def test_corrupt_backup_blocks_recovery(self):
  snap=self.s.snapshot();meta={'characterOrder':['b'],'generationEpoch':'crash','_pdsCurrent':{'revision':2}}
  recover=self.s.recover_delete;self.s.recover_delete=lambda:None
  with self.assertRaises(FileNotFoundError):self.s.delete_character('crash','i','a',snap['sourceVersion'],meta)
  backup=self.root/'.deleted'/hashlib.sha256(b'crash').hexdigest()/'files/a/metadata.json'
  backup.write_bytes(b'corrupt');self.s.recover_delete=recover
  with self.assertRaises(Conflict):self.s.snapshot()
  self.assertTrue((self.root/'a/metadata.json').exists())
 def test_asset_hash(self):
  with self.assertRaises(ValueError):self.s.put_asset(self.a,b'bad')
if __name__=='__main__':unittest.main()
