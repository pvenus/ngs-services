import copy,unittest,hashlib
from test_npc_animations import Tests as Base
from test_npc_pose_views import png,envelope
from project_data_service.npc_animation_frames import AnimationFramePublisher,DIRECTIONS
from project_data_service.filex_npc_files import encode,decode
from project_data_service.filex_read import digest

class Tests(unittest.TestCase):
 setUp=Base.setUp
 tearDown=Base.tearDown
 def packet(self):
  # Existing current video slots only; no source changes by extraction.
  from project_data_service.npc_animations import AnimationPublisher
  for row in self.motion_registry.return_value['presets'][0]['directions']:row.update(frameCount=2,fps=24)
  for d in DIRECTIONS:
   p=Base.packet(self,'video-'+d+'-request');p['slot']['direction']=d;p['slot']['frameCount']=2;p['slot']['fps']=24;p['trace']['poseReference'].update(direction=d,poseViewHash=digest(self.domain.npcs.current()['document']['characters'][0]['poseViews']['combat']['pixel'][d]))
   AnimationPublisher(self.domain).publish(p)
  c=self.domain.npcs.current();character=c['document']['characters'][0]
  a=envelope(png(32,32));aid=a['sha256']+'.png'
  directions={d:{'sourceAssetId':character['animationSlots']['combat/sword-attack/'+d]['assetId'],'frameCount':2,'fps':24,'frames':[{'index':i,'assetId':aid,'rawAssetId':aid,'alphaAssetId':aid}for i in range(2)]}for d in DIRECTIONS}
  return dict(requestId='frames-first-request',projectId='binari',npcId='character.neutral-fixture',expectedSourceVersion=c['sourceVersion'],expectedCharacterHash=digest(character),manifest=dict(schemaVersion='ngs.npc-animation-frame-set.v1',posePreset='combat',presetId='sword-attack',canvas=dict(width=32,height=32),directions=directions),artifacts=[a],trace={})
 def test_dynamic_slot_requires_string_identifiers(self):
  from project_data_service.npc_animation_frames import validate_manifest
  for invalid in (None,123,True,[],{}):
   with self.assertRaisesRegex(ValueError,'NPC_FRAMES_SLOT'):
    validate_manifest({'schemaVersion':'ngs.npc-animation-frame-set.v1','posePreset':'idle','presetId':invalid},{})
  with self.assertRaisesRegex(ValueError,'NPC_FRAMES_SLOT'):
   validate_manifest({'schemaVersion':'ngs.npc-animation-frame-set.v1','posePreset':'idle'},{})
 def test_walk_dynamic_slot_publish_preserves_videos(self):
  p=self.packet();current=self.domain.npcs.current();doc=copy.deepcopy(current['document']);npc=doc['characters'][0]
  npc['animationSlots'].update({k.replace('combat/sword-attack','idle/walk'):{**v,'posePreset':'idle','presetId':'walk'} for k,v in npc['animationSlots'].items()})
  self.domain.npcs.save(current['revision'],'fixture dynamic preset',doc)
  current=self.domain.npcs.current();before=copy.deepcopy(current['document'])
  p.update(expectedSourceVersion=current['sourceVersion'],expectedCharacterHash=digest(before['characters'][0]))
  p['manifest'].update(posePreset='idle',presetId='walk')
  api=AnimationFramePublisher(self.domain);receipt=api.publish(p)
  self.assertEqual(api.publish(p),receipt)
  after=self.domain.npcs.current()['document']
  self.assertIn('idle/walk',after['characters'][0]['animationFrameSets'])
  self.assertEqual(after['characters'][0]['animationSlots'],before['characters'][0]['animationSlots'])
  self.assertEqual(after['characters'][1],before['characters'][1])
  self.assertEqual(decode(encode(after)),after)
 def test_publish_roundtrip_replay_and_originals(self):
  p=self.packet();before=self.domain.npcs.current()['document'];api=AnimationFramePublisher(self.domain);receipt=api.publish(p)
  self.assertEqual(api.publish(p),receipt);after=self.domain.npcs.current()['document'];self.assertEqual(decode(encode(after)),after)
  self.assertEqual(after['characters'][0]['animationSlots'],before['characters'][0]['animationSlots']);self.assertEqual(after['characters'][1],before['characters'][1])
  self.assertEqual(receipt['frameCount'],16)
  self.domain.npc_receipts.connection.execute('DELETE FROM npc_animation_frame_receipts');self.domain.npc_receipts.connection.commit()
  self.assertEqual(api.publish(p),receipt)
 def test_complete_and_uniform_required(self):
  p=self.packet();api=AnimationFramePublisher(self.domain)
  p['manifest']['directions']['n']['frames'].pop()
  with self.assertRaisesRegex(ValueError,'COMPLETE_SEQUENCE'):api.publish(p)
  p['manifest']['directions']['n']['frames'].append({'index':1,'assetId':p['artifacts'][0]['sha256']+'.png','rawAssetId':p['artifacts'][0]['sha256']+'.png'})
  p['manifest']['canvas']['width']=64
  with self.assertRaisesRegex(ValueError,'CANVAS_MISMATCH'):api.publish(p)
 def test_alpha_asset_is_required_when_declared(self):
  p=self.packet();p['manifest']['directions']['n']['frames'][0]['alphaAssetId']='f'*64+'.png'
  with self.assertRaisesRegex(ValueError,'ALPHA_ASSET_MISSING'):AnimationFramePublisher(self.domain).publish(p)
 def test_alpha_dimensions_must_match_raw(self):
  p=self.packet();a=envelope(png(16,16));p['artifacts'].append(a)
  p['manifest']['directions']['n']['frames'][0]['alphaAssetId']=a['sha256']+'.png'
  with self.assertRaisesRegex(ValueError,'ALPHA_DIMENSIONS'):AnimationFramePublisher(self.domain).publish(p)
 def test_source_changed_rejected(self):
  p=self.packet();p['manifest']['directions']['n']['sourceAssetId']='f'*64+'.mp4'
  with self.assertRaisesRegex(ValueError,'VIDEO_CHANGED'):AnimationFramePublisher(self.domain).publish(p)
 def test_half_is_separate_from_full_and_videos(self):
  p=self.packet();alpha=envelope(png(32,32,44));p['artifacts'].append(alpha)
  for row in p['manifest']['directions'].values():
   for f in row['frames']:f['alphaAssetId']=alpha['sha256']+'.png'
  api=AnimationFramePublisher(self.domain);api.publish(p)
  current=self.domain.npcs.current();document=copy.deepcopy(current['document'])
  old=document['characters'][0]['animationSlots']['combat/sword-attack/n'];newbytes=self.domain.npcs.asset_get(old['assetId'])+b'new';newid=hashlib.sha256(newbytes).hexdigest()+'.mp4';self.domain.npcs.asset_put(newid,newbytes);old['assetId']=newid;old['bytes']=len(newbytes)
  self.domain.npcs.save(current['revision'],'newer video independent of saved full frames',document)
  current=self.domain.npcs.current();before=copy.deepcopy(current['document']['characters'][0])
  p['requestId']='frames-half-request';p['expectedSourceVersion']=current['sourceVersion'];p['expectedCharacterHash']=digest(before)
  full=before['animationFrameSets']['combat/sword-attack'];p['manifest'].update(scalePreset='half',pixelScale=.5,sourceFrameSet=dict(slotKey='combat/sword-attack',requestId=full['requestId'],inputHash=full['inputHash'],contentHash=digest(full)),sourceVideoStatus=dict(staleDirections=['n']));p['manifest']['canvas'].update(scale=.5,width=16,height=16)
  processed=envelope(png(16,16));p['artifacts'].append(processed)
  for d in p['manifest']['directions'].values():
   for f in d['frames']:f['assetId']=processed['sha256']+'.png'
  get=self.domain.npcs.asset_get
  def no_video(asset_id):
   if asset_id.endswith('.mp4'):raise AssertionError('half must consume pinned RGBA, not obsolete video')
   return get(asset_id)
  self.domain.npcs.asset_get=no_video
  api.publish(p);after=self.domain.npcs.current()['document']['characters'][0]
  self.assertEqual(after['animationFrameSets']['combat/sword-attack'],before['animationFrameSets']['combat/sword-attack'])
  self.assertIn('combat/sword-attack/half',after['animationFrameSets']);self.assertEqual(before['animationSlots'],after['animationSlots'])
  self.assertEqual(decode(encode({'characters':[after]})),{'characters':[after]})
 def test_alpha_must_exist_and_match_raw_dimensions(self):
  p=self.packet();frame=p['manifest']['directions']['n']['frames'][0];frame['alphaAssetId']='f'*64+'.png'
  with self.assertRaisesRegex(ValueError,'ALPHA_ASSET_MISSING'):AnimationFramePublisher(self.domain).publish(p)
  alpha=envelope(png(16,16,40));p['artifacts'].append(alpha);frame['alphaAssetId']=alpha['sha256']+'.png'
  with self.assertRaisesRegex(ValueError,'ALPHA_DIMENSIONS'):AnimationFramePublisher(self.domain).publish(p)
 def test_alpha_lineage_cannot_be_swapped_during_half(self):
  p=self.packet();alpha=envelope(png(32,32,40));p['artifacts'].append(alpha)
  for row in p['manifest']['directions'].values():
   for frame in row['frames']:frame['alphaAssetId']=alpha['sha256']+'.png'
  api=AnimationFramePublisher(self.domain);api.publish(p);c=self.domain.npcs.current();character=c['document']['characters'][0];full=character['animationFrameSets']['combat/sword-attack']
  p['requestId']='half-alpha-swapped';p['expectedSourceVersion']=c['sourceVersion'];p['expectedCharacterHash']=digest(character)
  p['manifest'].update(scalePreset='half',pixelScale=.5,sourceFrameSet=dict(slotKey='combat/sword-attack',requestId=full['requestId'],inputHash=full['inputHash'],contentHash=digest(full)),sourceVideoStatus=dict(staleDirections=[]));p['manifest']['canvas'].update(scale=.5,width=16,height=16)
  processed=envelope(png(16,16));swapped=envelope(png(32,32,99));p['artifacts']=[p['artifacts'][0],processed,swapped]
  for row in p['manifest']['directions'].values():
   for frame in row['frames']:frame['assetId']=processed['sha256']+'.png';frame['alphaAssetId']=swapped['sha256']+'.png'
  with self.assertRaisesRegex(ValueError,'DERIVED_LINEAGE'):api.publish(p)
if __name__=='__main__':unittest.main()
