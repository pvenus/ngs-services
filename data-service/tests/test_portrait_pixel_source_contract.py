import copy,unittest
from project_data_service.domain_contracts.legacy_http import validate_npcs,NPC_TEXT_FIELDS,NPC_TRAIT_GROUPS
from project_data_service.domain_contracts.profile_model import blank_profile

class PixelSourceContract(unittest.TestCase):
 def setUp(self):
  self.portrait='a'*64+'.png';self.pixel='b'*64+'.png';self.source='c'*64+'.png'
  proof={'postprocessCompleted':True,'portraitSha256':'a'*64,'referencePortraitSha256':'a'*64,'pixelSha256':'b'*64,'pixelSourceSha256':'c'*64,'postprocessInputSha256':'c'*64}
  self.pair={'portraitAssetId':self.portrait,'pixelAssetId':self.pixel,'referencePortraitAssetId':None,'requestId':'fixture','pixelSourceAssetId':self.source,'pixelProvenance':{'pipeline':proof,'postprocess':{}}}
  character={k:'' for k in NPC_TEXT_FIELDS};character.update(id='character.fixture',name='Fixture',role='role',status='confirmed',chapters=[],sourceRefs=[],traits={g:{k:None for k in keys}for g,keys in NPC_TRAIT_GROUPS.items()},numericProfile=blank_profile(),portraits=[{'assetId':a,'label':'fixture','sourceRef':''}for a in (self.portrait,self.pixel)],activePortraitId=self.portrait,portraitSets={'representative':self.pair})
  self.doc={'schemaVersion':1,'projectId':'ProjectBS','actId':'act01','masterRevision':1,'storyRevision':1,'characters':[character]}
 def validate(self):return validate_npcs(self.doc,lambda _:b'fixture')
 def test_native_source_passes_without_mutation_or_display_registration(self):
  before=copy.deepcopy(self.doc);self.assertIs(self.validate(),self.doc);self.assertEqual(self.doc,before)
 def test_legacy_pairs_with_and_without_provenance_remain_valid(self):
  del self.pair['pixelSourceAssetId'];self.validate();del self.pair['pixelProvenance'];self.validate()
 def test_bad_hash_path_and_type_are_denied(self):
  for value in ['d'*64+'.png','../../private.png',None,123]:
   self.pair['pixelSourceAssetId']=value
   with self.assertRaisesRegex(ValueError,'NPC_PIXEL_SOURCE_(PROVENANCE_MISMATCH|ASSET_INVALID)'):self.validate()
 def test_source_without_provenance_preserves_optional_legacy_contract(self):
  del self.pair['pixelProvenance']
  before=copy.deepcopy(self.doc);self.validate();self.assertEqual(self.doc,before)
 def test_unknown_keys_still_denied(self):
  self.pair['arbitrary']='x'
  with self.assertRaisesRegex(ValueError,'NPC_PORTRAIT_PAIR_INVALID'):self.validate()
