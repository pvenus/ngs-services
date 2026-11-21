import tempfile,unittest,os
from pathlib import Path
from unittest.mock import patch
from project_data_service.npc_preserved_legacy import snapshot,assess

class Links(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.old=self.root/'old';self.old.mkdir();self.target=self.root/'unopened-target';self.link=self.old/'link';self.link.symlink_to(self.target);self.roots={'old':str(self.old)};self.policy=[{'rootKey':'old','path':'link','target':str(self.target)}]
 def read(self):return snapshot(self.roots,preserved_links=self.policy)
 def test_exact_dangling_target_not_followed_and_hashed(self):
  s=self.read();self.assertFalse(self.target.exists());self.assertEqual(s['files'],[]);self.assertFalse(s['preservedLinks'][0]['targetFollowed']);self.assertIn('ino',s['preservedLinks'][0]['identity']);self.assertEqual(s,self.read())
 def test_default_and_unlisted_links_denied(self):
  with self.assertRaisesRegex(ValueError,'SYMLINK'):snapshot(self.roots)
  (self.old/'other').symlink_to(self.target)
  with self.assertRaisesRegex(ValueError,'SYMLINK'):self.read()
 def test_missing_regular_and_wrong_target_denied(self):
  self.link.unlink()
  with self.assertRaisesRegex(ValueError,'MISSING'):self.read()
  self.link.write_bytes(b'x')
  with self.assertRaisesRegex(ValueError,'MISSING'):self.read()
  self.link.unlink();self.link.symlink_to(self.root/'other')
  with self.assertRaisesRegex(ValueError,'TARGET_CHANGED'):self.read()
 def test_mutated_during_read_rejected(self):
  original=os.readlink;calls=[]
  def changed(p,*a,**kw):
   v=original(p,*a,**kw)
   if Path(p)==self.link and not calls:
    calls.append(1);self.link.unlink();self.link.symlink_to(self.root/'other')
   return v
  with patch('os.readlink',changed):
   with self.assertRaisesRegex(ValueError,'LINK_CHANGED'):self.read()
 def test_policy_scope_duplicate_denied(self):
  for policy in [[dict(self.policy[0],path='../link')],[dict(self.policy[0],rootKey='unknown')],self.policy*2]:
   with self.assertRaisesRegex(ValueError,'POLICY'):snapshot(self.roots,preserved_links=policy)
 def test_assessment_reobserves_link_metadata(self):
  s=self.read();fresh=self.root/'fresh';fresh.mkdir();backup=lambda n,g:{'npcId':n,'generationId':g,'verified':True,'backupId':'b','manifestHash':'h'}
  self.assertFalse(assess(s,{'fresh':str(fresh)},s['snapshotHash'],backup,'n','g')['fullNpcDeletionAllowed'])
  self.link.unlink();self.link.symlink_to(self.root/'other')
  with self.assertRaisesRegex(ValueError,'TARGET_CHANGED'):assess(s,{'fresh':str(fresh)},s['snapshotHash'],backup,'n','g')
if __name__=='__main__':unittest.main()
