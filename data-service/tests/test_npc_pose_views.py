import base64,copy,hashlib,struct,zlib,unittest
from test_npc_animations import Tests as Base
from project_data_service.npc_pose_views import PoseViewPublisher
from project_data_service.filex_npc_files import encode,decode,PREFIX
from project_data_service.filex_read import digest

def png(w=384,h=384,color=20):
 def chunk(k,v):return struct.pack('>I',len(v))+k+v+struct.pack('>I',zlib.crc32(k+v)&0xffffffff)
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,6,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+bytes([color,30,50,255])*w)*h))+chunk(b'IEND',b'')
def envelope(b):return {'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b),'mimeType':'image/png','base64':base64.b64encode(b).decode()}
class Tests(Base):
 def setUp(self):
  super().setUp();self.api=PoseViewPublisher(self.domain)
 def packet(self,key='pose-request-one',direction='n',color=20):
  c=self.domain.npcs.current();raw=envelope(png(64,96,color));out=envelope(png(color=color))
  return {'requestId':key,'projectId':'binari','npcId':'character.neutral-fixture','expectedSourceVersion':c['sourceVersion'],'expectedCharacterHash':digest(c['document']['characters'][0]),'slot':{'posePreset':'combat','referenceType':'pixel','direction':direction,'sourceAssetId':self.ref},'artifact':out,'sourceArtifact':raw,'trace':{'providerProof':{'actualToolOutput':True,'sha256':raw['sha256']},'postprocess':{'profileId':'npc.pose-pixel.384.v1','sourceSha256':raw['sha256']}}}
 def test_slot_only_roundtrip_idempotence_and_full_backup(self):
  p=self.packet();r=self.api.publish(p);self.assertEqual(self.api.publish(p),r);self.assertTrue(r['verified'])
  d=self.domain.npcs.current()['document'];self.assertEqual(decode(encode(d)),d);self.assertEqual(d['characters'][0]['portraitSets'],self.doc['characters'][0]['portraitSets']);self.assertEqual(d['characters'][1],self.doc['characters'][1]);self.assertIn('character.neutral-fixture/pose-views/combat/pixel/n.json',self.s.snapshot()['documents'])
  snap=self.s.snapshot();meta=copy.deepcopy(snap['documents']['metadata.json']);meta['characterOrder']=['character.other'];meta['generationEpoch']='delete-pose-test';meta['_pdsCurrent']={'revision':3}
  deleted=self.s.delete_character('delete-pose-test','i','character.neutral-fixture',snap['sourceVersion'],meta);self.assertTrue(deleted['backupVerified'])
 def test_corrupt_video_and_stale_reference_fail(self):
  p=self.packet();p['artifact']['sha256']='a'*64
  with self.assertRaisesRegex(ValueError,'ARTIFACT_BYTES'):self.api.publish(p)
  p=self.packet();p['slot']['sourceAssetId']='b'*64+'.png'
  with self.assertRaisesRegex(ValueError,'REFERENCE_CHANGED'):self.api.publish(p)
 def test_replace_one_slot_preserves_other_and_conflicts(self):
  self.api.publish(self.packet());stale=self.packet('stale-pose');self.api.publish(self.packet('east-pose-request','e'))
  with self.assertRaisesRegex(ValueError,'SOURCE_CHANGED'):self.api.publish(stale)
  self.api.publish(self.packet('replace-north','n',40));d=self.domain.npcs.current()['document']['characters'][0];self.assertEqual(set(d['poseViews']['combat']['pixel']),{'n','ne','e','se','s','sw','w','nw'})
 def test_bad_direction_rejected(self):
  p=self.packet(direction='../x')
  with self.assertRaisesRegex(ValueError,'SLOT_INVALID'):self.api.publish(p)
 def test_proof_and_postprocess(self):
  p=self.packet();p['trace']['providerProof']['actualToolOutput']=False
  with self.assertRaisesRegex(ValueError,'PROVIDER_PROOF'):self.api.publish(p)
  p=self.packet();p['artifact']=envelope(png(128,128))
  with self.assertRaisesRegex(ValueError,'POSTPROCESS'):self.api.publish(p)
 def test_lost_receipt_recover(self):
  p=self.packet();r=self.api.publish(p)
  self.domain.npc_receipts.connection.execute('DELETE FROM npc_pose_view_receipts');self.domain.npc_receipts.connection.commit()
  current=self.domain.npcs.current();p['expectedSourceVersion']=current['sourceVersion'];p['expectedCharacterHash']=digest(current['document']['characters'][0])
  self.assertEqual(self.api.publish(p),r)
 def test_animation_can_use_current_view_but_not_stale(self):
  from project_data_service.npc_animations import AnimationPublisher
  r=self.api.publish(self.packet());animation=AnimationPublisher(self.domain)
  p=Base.packet(self,'animation-from-view');p['slot']['referenceAssetId']=r['assetId'];animation.publish(p)
  current=self.domain.npcs.current();doc=current['document'];doc['characters'][0]['poseViews']['combat']['pixel']['n']['sourceAssetId']=self.ref
  # Corrupt/stale binding is rejected independently of the original asset's presence.
  doc['characters'][0]['poseViews']['combat']['pixel']['n']['sourceAssetId']=self.ref.replace('.png','.jpg')
  # Use the real bytes under a different valid content-addressed extension.
  self.s.put_asset(self.ref.replace('.png','.jpg'),self.refdata)
  self.domain.npcs.save(current['revision'],'test stale pose source',doc)
  p=Base.packet(self,'animation-stale-view');p['slot']['referenceAssetId']=r['assetId']
  with self.assertRaisesRegex(ValueError,'REFERENCE_STALE'):animation.publish(p)
 def test_regeneration_replaces_only_pinned_slot(self):
  before=self.domain.npcs.current()['document'];p=self.packet('regeneration-one','n',40)
  p['expectedSlotHash']=digest(before['characters'][0]['poseViews']['combat']['pixel']['n'])
  result=self.api.publish(p);self.assertEqual(self.api.publish(p),result)
  after=self.domain.npcs.current()['document'];old=copy.deepcopy(before);old['characters'][0]['poseViews']['combat']['pixel']['n']=after['characters'][0]['poseViews']['combat']['pixel']['n']
  self.assertEqual(after,old)
 def test_regeneration_stale_target_does_not_write(self):
  p=self.packet('regeneration-stale');p['expectedSlotHash']='f'*64;before=self.s.snapshot()
  with self.assertRaisesRegex(ValueError,'TARGET_CHANGED'):self.api.publish(p)
  self.assertEqual(before,self.s.snapshot())
 def test_request_collision_and_bad_raw(self):
  p=self.packet();self.api.publish(p);p['trace']['extra']='changed'
  with self.assertRaisesRegex(ValueError,'IDEMPOTENCY_CONFLICT'):self.api.publish(p)
  p=self.packet('bad-png');p['sourceArtifact']=envelope(b'\x89PNG\r\n\x1a\n'+b'a'*40)
  with self.assertRaises(ValueError):self.api.publish(p)
if __name__=='__main__':unittest.main()
