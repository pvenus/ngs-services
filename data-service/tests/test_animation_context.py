import copy,json,unittest
from project_data_service.planning_items import PlanningItems,animation_catalog,ANIMATION_CATEGORIES,ANIMATION_STYLE_KEYS
from project_data_service.filex_read import digest
class Transport:
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def get(self,*args,**kwargs):return b'pointer'
class TestAnimationContext(unittest.TestCase):
 def setUp(self):
  style={'schemaVersion':'ngs.character-style-contract.v1',**{k:k+' value' for k in ANIMATION_STYLE_KEYS},'story':'SECRET STORY'}
  character={'id':'npc','planningCategories':[{'id':'art','name':'포트레이트용 아트','content':'BLUE CLOTHES'},{'id':'pose','name':'아이들 포즈 기획','planningRole':'portrait.idle','content':'STAND STILL'}],'portraitSets':{'idle':{'pixelAssetId':'a'*64+'.png'}}}
  self.s={'_concept':{'document':{'sections':[{'id':'expression','fields':[{'id':'visualStyle','value':json.dumps(style)}]}]}},'_character':character,'values':{'unique.npc-identity':{'id':'unique.npc-identity','value':{'name':'테스트 인물'}},'unique.npc-design':{'id':'unique.npc-design','value':{}}},'catalog':{'npcRevision':7,'masterRevision':57,'masterDocumentHash':'masterhash'}}
  self.reader=PlanningItems(transport_factory=Transport)
  self.reader._snapshot=lambda *args:(b'pointer',copy.deepcopy(self.s))
  self.request={'projectId':'binari','npcId':'npc','npcRevision':7,'sourceVersion':'current','categories':ANIMATION_CATEGORIES,'worker':'npc-animation','posePreset':'idle','referenceType':'pixel'}
 def test_selected_data_and_provenance(self):
  out=self.reader.read(self.request);raw=json.dumps(out)
  self.assertNotIn('STAND STILL',raw);self.assertNotIn('SECRET STORY',raw)
  self.assertIn('BLUE CLOTHES',raw);self.assertEqual(out['masterRevision'],57)
  self.assertEqual(out['trace']['referenceAssetId'],'a'*64+'.png')
  self.assertEqual(out['trace']['validatedPoseContentHash'],digest(self.s['_character']['planningCategories'][1]))
  self.assertFalse(out['trace']['poseBodyIncluded'])
 def test_missing_pose_still_rejected(self):
  self.s['_character']['planningCategories'].pop()
  with self.assertRaises(ValueError):self.reader.read(self.request)
 def test_missing_reference_rejected(self):
  self.s['_character']['portraitSets']={}
  with self.assertRaises(ValueError):self.reader.read(self.request)
 def test_schema_does_not_fallback_to_full_master(self):
  self.s['_concept']['document']['sections'][0]['fields'][0]['value']='plain text'
  with self.assertRaises(ValueError):animation_catalog(self.s)
 def test_catalog_and_source_unchanged(self):
  before=copy.deepcopy(self.s);out=animation_catalog(self.s)
  self.assertEqual(before,self.s);self.assertEqual([c['id']for c in out['catalog']['categories']],ANIMATION_CATEGORIES)
 def test_no_arbitrary_categories(self):
  with self.assertRaises(ValueError):self.reader.read({**self.request,'categories':['unique.npc-performance']})
if __name__=='__main__':unittest.main()
