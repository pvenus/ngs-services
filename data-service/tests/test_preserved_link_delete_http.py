import unittest,urllib.error
from test_current_generation_delete_http import StagedHTTP
from project_data_service.npc_preserved_legacy import snapshot

class LinkDelete(StagedHTTP):
 def test_exact_link_preserved_and_changed_link_blocks_actual_delete(self):
  self.start_current();old=self.root/'legacy-links';old.mkdir();target=self.root/'case-assets';target.mkdir();(target/'original').write_bytes(b'untouched');link=old/'case-assets';link.symlink_to(target)
  policy=[{'rootKey':'legacy','path':'case-assets','target':str(target)}];provider=self.source.external_inventory;original=provider.evidence_reader
  def read(*a):
   proof=original(*a);proof['legacySnapshotHash']=snapshot({'legacy':str(old)},preserved_links=policy)['snapshotHash'];return proof
  provider.evidence_reader=read;before=self.writer.owner_files('npc');link.unlink();link.symlink_to(self.root/'wrong')
  with self.assertRaises(urllib.error.HTTPError):self.delete()
  self.assertEqual(self.writer.owner_files('npc'),before);self.assertEqual(self.writer.generation('npc')['state'],'delete-staged')
  link.unlink();link.symlink_to(target);result=self.delete();self.assertEqual(result['deletionReceipt']['status'],'committed');self.assertEqual(link.readlink(),target);self.assertEqual((target/'original').read_bytes(),b'untouched')
if __name__=='__main__':unittest.main()
