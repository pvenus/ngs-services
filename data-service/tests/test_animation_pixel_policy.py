import unittest,copy,json
from unittest.mock import patch
from test_npc_animations import Tests as Base
from project_data_service.npc_animations import AnimationPublisher,legacy_reference_allowed
from project_data_service.filex_read import digest
class Tests(unittest.TestCase):
 setUp=Base.setUp
 def test_new_portrait_fails_without_changes(self):
  p=Base.packet(self);p['slot']['referenceType']='portrait';before=self.s.snapshot()
  with self.assertRaisesRegex(ValueError,'PIXEL_REFERENCE_REQUIRED'):self.api.publish(p)
  self.assertEqual(before,self.s.snapshot())
 def test_pixel_requires_binding(self):
  p=Base.packet(self);p['trace'].pop('poseReference')
  with self.assertRaisesRegex(ValueError,'POSE_REFERENCE_INVALID'):self.api.publish(p)
 def test_frozen_legacy_exact_and_completed_replay(self):
  p=Base.packet(self);p['slot']['referenceType']='portrait';p['trace'].pop('poseReference');record={k:p[k]for k in ('projectId','npcId','slot')};config={'schemaVersion':'pds-animation-reference-legacy.v1','requests':{p['requestId']:record}}
  with patch('project_data_service.filex_read.private_read',return_value=json.dumps(config).encode()):r=self.api.publish(p)
  # Existing receipt stays readable/replayable after temporary migration record is gone.
  self.assertEqual(r,self.api.publish(p))
  q=copy.deepcopy(p);q['slot']['referenceAssetId']='b'*64+'.png'
  with patch('project_data_service.filex_read.private_read',return_value=json.dumps(config).encode()):
   with self.assertRaisesRegex(ValueError,'LEGACY_BINDING_CHANGED'):legacy_reference_allowed(q)
 def test_no_client_opt_in(self):
  p=Base.packet(self);p['legacy']=True
  with self.assertRaisesRegex(ValueError,'REQUEST_INVALID'):self.api.publish(p)
if __name__=='__main__':unittest.main()
