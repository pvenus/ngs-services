import copy,json,unittest
from project_data_service.rendering_rules import SCHEMA,fields
from project_data_service.planning_items import animation_catalog,rendering_value,ANIMATION_STYLE_KEYS
class Rules(unittest.TestCase):
 def concept(self):
  rules={'schemaVersion':SCHEMA,'rules':[{'id':'side','stages':['portrait'],'posePresets':['idle'],'directions':['e','w'],'instruction':'STATIC'},{'id':'anatomy','stages':['portrait','animation'],'posePresets':['idle'],'directions':['e','w'],'instruction':'NO EXTRA LIMBS'}]}
  return {'document':{'sections':[{'id':'expression','fields':[{'id':'visualStyle','value':json.dumps({'schemaVersion':'ngs.character-style-contract.v1',**{k:k for k in ANIMATION_STYLE_KEYS}})},{'id':'visualProhibitions','value':'GENERAL'},{'id':'new-field','value':json.dumps(rules)}]},{'id':'world','fields':[{'id':'eraCulture','value':'ERA'},{'id':'prohibitions','value':'WORLD'}]}]}}
 def test_portrait_reads_registered_field_without_name_hardcoding(self):
  value=rendering_value(self.concept());self.assertEqual(len(value['masterFields']),3)
 def test_animation_projects_stage_and_keeps_old_style_keys(self):
  c=self.concept();snapshot={'_concept':c,'values':{'unique.npc-identity':{},'unique.npc-design':{}},'catalog':{}};before=copy.deepcopy(snapshot)
  out=animation_catalog(snapshot);raw=json.dumps(out['values']['common.animation-rendering']['value'])
  self.assertIn('NO EXTRA LIMBS',raw);self.assertNotIn('STATIC',raw);self.assertNotIn('WORLD',raw);self.assertEqual(snapshot,before)
 def test_archive_restore(self):
  c=self.concept();c['document']['hierarchy']={'archivedItems':[{'categoryId':'expression','itemId':'new-field'}]}
  self.assertEqual(fields(c),[])
  c['document']['hierarchy']['archivedItems']=[];self.assertEqual(len(fields(c)),1)
  c['document']['hierarchy']['archivedCategoryIds']=['expression'];self.assertEqual(fields(c),[])
 def test_malformed_registered_field_blocks(self):
  c=self.concept();c['document']['sections'][0]['fields'][2]['value']=json.dumps({'schemaVersion':SCHEMA,'rules':'invalid'})
  with self.assertRaises(ValueError):rendering_value(c)
 def test_duplicate_registered_field_blocks(self):
  c=self.concept();c['document']['sections'][0]['fields'].append(c['document']['sections'][0]['fields'][2])
  with self.assertRaises(ValueError):fields(c)
if __name__=='__main__':unittest.main()

class PublicationValidation(Rules):
 def test_valid_registered_and_ordinary_text(self):
  from project_data_service.rendering_rules import validate_document
  self.assertEqual(validate_document(self.concept()['document']),self.concept()['document'])
 def test_malformed_json_marker(self):
  from project_data_service.rendering_rules import validate_document
  c=self.concept()['document'];c['sections'][0]['fields'][2]['value']='{"schemaVersion":"'+SCHEMA+'",'
  with self.assertRaisesRegex(ValueError,'RULES_JSON'):validate_document(c)
 def test_wrong_category_and_active_duplicates(self):
  from project_data_service.rendering_rules import validate_document
  c=self.concept()['document'];c['sections'][0]['id']='world'
  with self.assertRaisesRegex(ValueError,'CATEGORY_REQUIRED'):validate_document(c)
  c=self.concept()['document'];c['sections'][0]['fields'].append(dict(c['sections'][0]['fields'][2],id='duplicate'))
  with self.assertRaisesRegex(ValueError,'AMBIGUOUS'):validate_document(c)
  c['hierarchy']={'archivedItems':[{'categoryId':'expression','itemId':'duplicate'}]};validate_document(c)
