import tempfile, unittest
from pathlib import Path
from project_data_service.npc_preserved_legacy import snapshot, assess

class Tests(unittest.TestCase):
 def test_preserved_unknown_never_means_complete(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d).resolve(); old=root/'legacy';old.mkdir();(old/'unknown').write_bytes(b'unknown');new=root/'fresh';new.mkdir()
   s=snapshot({'old':str(old)})
   backup=lambda n,g:{'npcId':n,'generationId':g,'verified':True,'backupId':'b','manifestHash':'h'}
   result=assess(s,{'fresh':str(new)},s['snapshotHash'],backup,'n','g')
   self.assertFalse(result['fullNpcDeletionAllowed']);self.assertFalse(result['globalCoverageComplete']);self.assertEqual(result['deleteCandidates'],[])
   self.assertEqual((old/'unknown').read_bytes(),b'unknown')
   with self.assertRaisesRegex(ValueError,'OVERLAP'):assess(s,{'fresh':str(old/'child')},s['snapshotHash'],backup,'n','g')
   (old/'unknown').write_bytes(b'changed')
   with self.assertRaisesRegex(ValueError,'SNAPSHOT_CHANGED'):assess(s,{'fresh':str(new)},s['snapshotHash'],backup,'n','g')
 def test_symlink_and_unverified_backup_blocked(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d).resolve();old=root/'old';old.mkdir();new=root/'new';new.mkdir()
   s=snapshot({'old':str(old)})
   with self.assertRaisesRegex(ValueError,'BACKUP_REQUIRED'):assess(s,{'new':str(new)},s['snapshotHash'],lambda n,g:{},'n','g')
   (old/'link').symlink_to(new)
   with self.assertRaisesRegex(ValueError,'SYMLINK'):snapshot({'old':str(old)})
if __name__=='__main__':unittest.main()
