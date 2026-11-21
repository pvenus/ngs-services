import copy
from test_filex_npc_authority import Authority
from project_data_service.npc_category_removal import remove,plan
from project_data_service.filex_read import digest

class CategoryRemoval(Authority):
 def test_logical_removal_preserves_content_and_retry(self):
  authority=self.seed();npc=self.doc['characters'][0];category=npc['planningCategories'][0]
  binding={'requestId':'remove-test','characterId':npc['id'],'categoryId':category['id'],'expectedRevision':83}
  result=remove(authority,binding['requestId'],digest(binding),npc['id'],category['id'],83)
  self.assertEqual(result['revision'],84);self.assertTrue(result['contentPreserved'])
  current=authority.current()['document'];target=current['characters'][0]['planningCategories'][0]
  self.assertEqual(target['content'],category['content']);self.assertEqual(target['lifecycleState'],'deleted')
  self.assertEqual({k:v for k,v in target.items() if k not in ('lifecycleState','removalRequestId')},category)
  self.assertEqual(remove(authority,binding['requestId'],digest(binding),npc['id'],category['id'],83),result)
  self.assertFalse((self.data/'versions').exists());self.assertEqual(len(self.adapter.snapshot()['documents']),len(self.adapter.snapshot()['hashes']))
  with self.assertRaisesRegex(ValueError,'INPUT_HASH'):remove(authority,'remove-test','other',npc['id'],category['id'],83)
 def test_stale_and_unknown_do_not_mutate(self):
  authority=self.seed();npc=self.doc['characters'][0]
  binding={'requestId':'stale','characterId':npc['id'],'categoryId':'missing','expectedRevision':82}
  self.assertIsNone(remove(authority,'stale',digest(binding),npc['id'],'missing',82))
  with self.assertRaisesRegex(ValueError,'TARGET_REQUIRED'):plan(self.doc,npc['id'],'missing','unknown')
  self.assertEqual(authority.current()['document'],self.doc)
 def test_visible_membership_and_nested_fields_preserved(self):
  doc=copy.deepcopy(self.doc);npc=doc['characters'][0];cid=npc['planningCategories'][0]['id'];npc['planningPreset']={'visibleCategoryIds':[cid,'other']};npc['planningCategories'][0]['children']=[{'content':'keep'}]
  result=plan(doc,npc['id'],cid,'r')
  self.assertEqual(result['characters'][0]['planningPreset']['visibleCategoryIds'],['other'])
  self.assertEqual(result['characters'][0]['planningCategories'][0]['children'],[{'content':'keep'}])
  self.assertNotIn('lifecycleState',npc['planningCategories'][0])
