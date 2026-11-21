import copy
import unittest
from test_filex_npc_integration import Integration
from project_data_service.filex_npc_authority import FilexCurrentNPC,migration_files
from project_data_service.filex_npc_files import decode

class Authority(Integration):
 def seed(self):
  envelope={'revision':83,'savedAt':'2026-11-14T06:42:38Z','note':'source','document':self.doc}
  files=migration_files(envelope)
  self.adapter.apply('migration','migration',before={},after=files,expected_hashes={k:None for k in files})
  return FilexCurrentNPC(self.adapter)
 def test_current_only_roundtrip(self):
  authority=self.seed()
  self.assertEqual(authority.current()['document'],self.doc)
  changed=copy.deepcopy(self.doc);changed['characters'][0]['planningCategories'][0]['content']='new body'
  result=authority.save(83,'official edit',changed)
  self.assertEqual(result['revision'],84)
  self.assertEqual(authority.save(83,'official edit',changed),result)
  self.assertIsNone(authority.save(83,'different operation',self.doc))
  self.assertIsNone(authority.revision(83))
  self.assertEqual(len(authority.history()),1)
  self.assertFalse((self.data/'versions').exists())
  changed_again=copy.deepcopy(changed);changed_again['characters'][0]['name']='later request'
  later=authority.save(84,'later edit',changed_again)
  self.assertEqual(later['revision'],85)
  self.assertEqual(authority.save(83,'official edit',changed),result)
  self.assertEqual(authority.current()['document'],changed_again)
 def test_save_returns_its_receipt_not_a_later_body(self):
  authority=self.seed();first=copy.deepcopy(self.doc);first['characters'][0]['name']='first'
  later=copy.deepcopy(self.doc);later['characters'][0]['name']='later'
  original=self.adapter.apply
  def interleaved(*args,**kwargs):
   outcome=original(*args,**kwargs)
   self.adapter.apply=original
   authority.save(84,'second operation',later)
   return outcome
  self.adapter.apply=interleaved
  result=authority.save(83,'first operation',first)
  self.assertEqual(result['revision'],84)
  self.assertEqual(result['document'],first)
  self.assertEqual(authority.current()['revision'],85)
  self.assertEqual(authority.current()['document'],later)
 def test_authority_failure_does_not_fallback(self):
  authority=self.seed();(self.data/'metadata.json').unlink()
  with self.assertRaises(KeyError):authority.current()
 def test_implicit_delete_fails(self):
  authority=self.seed();deleted=copy.deepcopy(self.doc);deleted['characters']=[]
  with self.assertRaisesRegex(ValueError,'EXPLICIT_DELETE'):authority.save(83,'not a delete worker',deleted)
  self.assertEqual(authority.current()['document'],self.doc)
if __name__=='__main__':unittest.main()

class Configuration(unittest.TestCase):
 def test_temp_storage_does_not_acquire_live_writer(self):
  import tempfile,json
  from pathlib import Path
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp).resolve()/'authority.json';p.write_text(json.dumps({'authority':'filex','storageRoot':'/an-explicit-production-root'}));p.chmod(0o600)
   self.assertIsNone(FilexCurrentNPC.configured(path=p,storage_root=tmp))

 def test_cutover_never_falls_back_without_config(self):
  import tempfile,json
  from pathlib import Path
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp).resolve();(root/'domains').mkdir()
   (root/'domains/npc-filex-authority.json').write_text('{}')
   with self.assertRaisesRegex(ValueError,'CONFIG_MISSING_AFTER_CUTOVER'):
    FilexCurrentNPC.configured(path=root/'missing.json',storage_root=root)
   p=root/'authority.json';p.write_text(json.dumps({'authority':'filex','storageRoot':'/wrong-root'}));p.chmod(0o600)
   with self.assertRaisesRegex(ValueError,'CONFIG_ROOT_MISMATCH_AFTER_CUTOVER'):
    FilexCurrentNPC.configured(path=p,storage_root=root)


class CaptureAssets(unittest.TestCase):
 def test_filex_portraits_never_read_old_disk_original(self):
  from unittest.mock import Mock
  from project_data_service.domain_export.binari_current import BinariCurrentSource
  source=object.__new__(BinariCurrentSource)
  source.npc_asset_read=Mock(return_value=b'filex bytes')
  source._bounded=Mock(return_value=b'other domain bytes')
  self.assertEqual(source._asset_bytes('assets/portraits/a.png','a.png'),b'filex bytes')
  source._bounded.assert_not_called()
  self.assertEqual(source._asset_bytes('assets/relationships/trust.png','trust.png'),b'other domain bytes')


class PublicationEnvelope(unittest.TestCase):
 def test_transport_metadata_does_not_change_official_content(self):
  from project_data_service.domain_export.binari_current import npc_publication_envelope
  base={'revision':83,'savedAt':'time','note':'source','document':{'characters':[]}}
  self.assertEqual(npc_publication_envelope({**base,'authority':'filex','sourceVersion':'hash','operationHash':'receipt'}),base)
  for key,value in [('revision',84),('savedAt','new time'),('note','new note'),('document',{'characters':[{'id':'different'}]})]:
   self.assertNotEqual(npc_publication_envelope({**base,key:value}),base)
