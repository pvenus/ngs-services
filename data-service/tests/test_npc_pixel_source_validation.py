import copy, unittest
from project_data_service.domain_contracts.legacy_http import validate_npcs, NPC_TEXT_FIELDS, NPC_TRAIT_GROUPS
from project_data_service.domain_contracts.profile_model import blank_profile

class PixelSourceValidation(unittest.TestCase):
 def setUp(self):
  self.portrait='a'*64+'.png';self.pixel='b'*64+'.png';self.source='c'*64+'.png'
  n={k:'' for k in NPC_TEXT_FIELDS};n.update(id='character.test',name='test',role='villager',status='proposal',chapters=['chapter01'],sourceRefs=[],traits={g:dict.fromkeys(keys)for g,keys in NPC_TRAIT_GROUPS.items()},numericProfile=blank_profile(),portraits=[{'assetId':a,'label':'fixture','sourceRef':'test'}for a in [self.portrait,self.pixel]],activePortraitId=self.portrait)
  n['portraitSets']={'representative':{'portraitAssetId':self.portrait,'pixelAssetId':self.pixel,'referencePortraitAssetId':None,'requestId':'test','pixelSourceAssetId':self.source,'pixelProvenance':{'pipeline':{'postprocessCompleted':True,'portraitSha256':'a'*64,'referencePortraitSha256':'a'*64,'pixelSha256':'b'*64,'pixelSourceSha256':'c'*64,'postprocessInputSha256':'c'*64},'postprocess':{}}}}
  self.doc={'schemaVersion':1,'projectId':'ProjectBS','actId':'act01','masterRevision':1,'storyRevision':1,'characters':[n]}
 def test_source_preserved_and_read(self):
  before=copy.deepcopy(self.doc);seen=[];self.assertIs(validate_npcs(self.doc,lambda a:seen.append(a)),self.doc);self.assertEqual(self.doc,before);self.assertIn(self.source,seen)
 def test_legacy_pairs_remain_valid(self):
  pair=self.doc['characters'][0]['portraitSets']['representative'];pair.pop('pixelSourceAssetId');validate_npcs(self.doc,lambda a:b'fixture');pair.pop('pixelProvenance');validate_npcs(self.doc,lambda a:b'fixture')
 def test_unknown_key_rejected(self):
  self.doc['characters'][0]['portraitSets']['representative']['surprise']=True
  with self.assertRaisesRegex(ValueError,'PAIR_INVALID'):validate_npcs(self.doc,lambda a:b'fixture')
 def test_source_id_and_provenance_rejected(self):
  pair=self.doc['characters'][0]['portraitSets']['representative'];pair['pixelSourceAssetId']='../source.png'
  with self.assertRaisesRegex(ValueError,'SOURCE_ASSET_INVALID'):validate_npcs(self.doc,lambda a:b'fixture')
  pair['pixelSourceAssetId']='d'*64+'.png'
  with self.assertRaisesRegex(ValueError,'SOURCE_PROVENANCE_MISMATCH'):validate_npcs(self.doc,lambda a:b'fixture')
 def test_missing_source_fails(self):
  def read(a):
   if a==self.source:raise ValueError('missing source')
   return b'fixture'
  with self.assertRaisesRegex(ValueError,'missing source'):validate_npcs(self.doc,read)
