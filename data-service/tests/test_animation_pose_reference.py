import copy,unittest
from project_data_service.npc_animations import validate_pose_reference
from project_data_service.filex_read import digest
class Tests(unittest.TestCase):
 def setUp(self):
  self.source='a'*64+'.png';self.slot={'posePreset':'combat','referenceType':'pixel','direction':'n','referenceAssetId':'b'*64+'.png'}
  self.view={**self.slot,'schemaVersion':'ngs.npc-pose-view.v1','sourceAssetId':self.source,'sourceHash':'a'*64,'assetId':self.slot['referenceAssetId'],'width':384,'height':384}
  self.binding={'policy':'ngs.animation-pose-view-reference.v1',**self.slot,'sourceAssetId':self.source,'poseViewHash':digest(self.view)}
 def test_valid(self):validate_pose_reference(self.binding,self.view,self.slot,self.source)
 def test_stale_lineage(self):
  self.view['sourceHash']='c'*64
  with self.assertRaisesRegex(ValueError,'STALE'):validate_pose_reference(self.binding,self.view,self.slot,self.source)
 def test_changed_binding(self):
  self.binding['poseViewHash']='c'*64
  with self.assertRaisesRegex(ValueError,'CHANGED'):validate_pose_reference(self.binding,self.view,self.slot,self.source)
 def test_wrong_direction(self):
  self.view['direction']='s'
  with self.assertRaisesRegex(ValueError,'SLOT'):validate_pose_reference(self.binding,self.view,self.slot,self.source)
 def test_small_pixel(self):
  self.view['width']=128
  with self.assertRaisesRegex(ValueError,'DIMENSIONS'):validate_pose_reference(self.binding,self.view,self.slot,self.source)
if __name__=='__main__':unittest.main()
