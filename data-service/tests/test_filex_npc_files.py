import unittest
from project_data_service.filex_npc_files import encode, decode, changed_files
from project_data_service.filex_read import digest

class NPCFilesTest(unittest.TestCase):
 def setUp(self):
  self.doc={'projectId':'ProjectBS','characters':[{'id':'character.neutral-fixture','name':'테스트 인물','role':'별감','portraits':[],'planningCategories':[{'id':'art','name':'아트','content':'기존'}],'unknown':{'keep':True}}]}
 def test_lossless(self):self.assertEqual(decode(encode(self.doc)),self.doc)
 def test_only_changed_full_file(self):
  before=encode(self.doc);after=encode(self.doc);p='character.neutral-fixture/planning/art.json';after[p]['content']='새 본문'
  plan=changed_files(before,after,{k:digest(v) for k,v in before.items()})
  self.assertEqual(len(plan['files']),1);self.assertEqual(plan['files'][0]['body'],after[p]);self.assertEqual(plan['backupPolicy'],'none')
 def test_stale_common_rejected(self):
  before=encode(self.doc)
  with self.assertRaisesRegex(ValueError,'NPC_SOURCE_CHANGED'):changed_files(before,before,{'metadata.json':'0'*64})
 def test_implicit_delete_rejected(self):
  before=encode(self.doc);after=dict(before);del after['character.neutral-fixture/planning/art.json']
  with self.assertRaisesRegex(ValueError,'NPC_EXPLICIT_DELETE'):changed_files(before,after,{k:digest(v)for k,v in before.items()})
 def test_unread_write_rejected(self):
  before=encode(self.doc);after=encode(self.doc);after['character.neutral-fixture/metadata.json']['role']='다른 직업'
  with self.assertRaisesRegex(ValueError,'NPC_WRITE_BASIS_MISSING'):changed_files(before,after,{})
 def test_path_escape_rejected(self):
  self.doc['characters'][0]['planningCategories'][0]['id']='../art'
  with self.assertRaises(ValueError):encode(self.doc)
 def test_absent_categories_preserved(self):
  del self.doc['characters'][0]['planningCategories'];self.assertEqual(decode(encode(self.doc)),self.doc)

if __name__=='__main__':unittest.main()
