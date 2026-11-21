import base64,copy,json,sys,tempfile,threading,sqlite3,unittest,hashlib,struct,zlib
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'filex-writer'))
from npc_store import NPCStore
from project_data_service.filex_npc_files import encode,decode,PREFIX
from project_data_service.filex_npc_store import FilexNPCStore
from project_data_service.filex_npc_authority import FilexCurrentNPC
from project_data_service.filex_read import digest
from project_data_service.npc_animations import AnimationPublisher
class Transport:
 def __init__(self,s):self.s=s
 def __enter__(self):return self
 def __exit__(self,*a):pass
 def capabilities(self):return {'lockedBatch':True,'namespace':PREFIX}
 def snapshot(self):return self.s.snapshot()
 def batch(self,key,ih,plan):return self.s.batch(key,ih,plan['files'],plan['readSet'])
 def receipt(self,key):
  try:return self.s.receipt(key)
  except FileNotFoundError:return None
 def asset_get(self,aid):return base64.b64decode(self.s.asset(aid)['base64'])
 def asset_put(self,aid,data):return self.s.put_asset(aid,data)
class Tests(unittest.TestCase):
 def setUp(self):
  from unittest.mock import patch
  patcher=patch('project_data_service.motion_presets.MotionPresets.list',return_value={'presets':[{'presetId':'sword-attack','posePreset':'combat','directions':[{'direction':d,'sha256':'a'*64,'frameCount':16,'fps':8,'cycleCount':1}for d in ('n','ne','e','se','s','sw','w','nw')]}]});self.motion_registry=patcher.start();self.addCleanup(patcher.stop)
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.s=NPCStore(self.tmp.name)
  def chunk(k,v):return struct.pack('>I',len(v))+k+v+struct.pack('>I',zlib.crc32(k+v)&0xffffffff)
  self.refdata=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',384,384,8,6,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+bytes([20,30,50,255])*384)*384))+chunk(b'IEND',b'');self.ref=hashlib.sha256(self.refdata).hexdigest()+'.png';self.s.put_asset(self.ref,self.refdata)
  self.doc={'characters':[{'id':'character.neutral-fixture','name':'keep','planningCategories':[{'id':'art','content':'keep'}],'portraits':[{'assetId':self.ref}],'portraitSets':{'combat':{'pixelAssetId':self.ref,'portraitAssetId':self.ref}}},{'id':'character.other','name':'untouched'}]}
  self.doc['characters'][0]['poseViews']={'combat':{'pixel':{d:{'schemaVersion':'ngs.npc-pose-view.v1','posePreset':'combat','referenceType':'pixel','direction':d,'sourceAssetId':self.ref,'sourceHash':self.ref[:-4],'assetId':self.ref,'width':384,'height':384}for d in ('n','ne','e','se','s','sw','w','nw')}}}
  files=encode(self.doc);files['metadata.json']['_pdsCurrent']={'revision':1,'savedAt':'start','note':'seed','documentHash':digest(self.doc)}
  self.s.batch('seed','seed',[{'path':PREFIX+p,'body':v,'expectedHash':None}for p,v in files.items()],{PREFIX+p:None for p in files})
  source=FilexCurrentNPC(FilexNPCStore(lambda:Transport(self.s)));db=sqlite3.connect(':memory:');self.addCleanup(db.close)
  self.domain=SimpleNamespace(npcs=source,npc_receipts=SimpleNamespace(connection=db),lock=threading.RLock());self.api=AnimationPublisher(self.domain)
 def packet(self,key='animation-request-1',data=b'\x00\x00\x00\x18ftypmp42'+b'video-fixture'):
  c=self.domain.npcs.current();char=c['document']['characters'][0]
  return {'requestId':key,'projectId':'binari','npcId':'character.neutral-fixture','expectedSourceVersion':c['sourceVersion'],'expectedCharacterHash':digest(char),'slot':{'posePreset':'combat','presetId':'sword-attack','direction':'n','referenceType':'pixel','referenceAssetId':self.ref,'frameCount':16,'fps':8,'cycleCount':1,'presetHash':'a'*64},'artifact':{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'mimeType':'video/mp4','base64':base64.b64encode(data).decode()},'trace':{'test':True,'poseReference':{'policy':'ngs.animation-pose-view-reference.v1','posePreset':'combat','referenceType':'pixel','direction':'n','sourceAssetId':self.ref,'referenceAssetId':char['poseViews']['combat']['pixel']['n']['assetId'],'poseViewHash':digest(char['poseViews']['combat']['pixel']['n'])}}}
 def test_slot_only_roundtrip_idempotence_and_full_backup(self):
  p=self.packet();r=self.api.publish(p);self.assertTrue(r['verified']);self.assertEqual(self.api.publish(p),r)
  d=self.domain.npcs.current()['document'];self.assertEqual(d['characters'][1],self.doc['characters'][1]);self.assertEqual(d['characters'][0]['portraitSets'],self.doc['characters'][0]['portraitSets']);self.assertEqual(decode(encode(d)),d)
  self.assertIn('character.neutral-fixture/animations/combat/sword-attack/n.json',self.s.snapshot()['documents'])
  snap=self.s.snapshot();meta=copy.deepcopy(snap['documents']['metadata.json']);meta['characterOrder']=['character.other'];meta['generationEpoch']='delete-test';meta['_pdsCurrent']={'revision':3}
  receipt=self.s.delete_character('delete-test','i','character.neutral-fixture',snap['sourceVersion'],meta);self.assertTrue(receipt['backupVerified']);self.assertEqual((Path(self.tmp.name)/'.deleted'/receipt['backupId']/'files/assets'/r['assetId']).read_bytes(),base64.b64decode(p['artifact']['base64']))
 def test_corrupt_video_and_stale_reference_fail(self):
  p=self.packet();p['artifact']['sha256']='f'*64
  with self.assertRaisesRegex(ValueError,'MP4_INVALID'):self.api.publish(p)
  p=self.packet();p['slot']['referenceAssetId']='b'*64+'.png'
  with self.assertRaisesRegex(ValueError,'REFERENCE_ASSET'):self.api.publish(p)
 def test_replace_one_slot_preserves_other_and_conflicts(self):
  p=self.packet();self.api.publish(p)
  stale=self.packet('stale-request');q=self.packet('second-request');q['slot']['direction']='e';q['trace']['poseReference'].update(direction='e',poseViewHash=digest(self.domain.npcs.current()['document']['characters'][0]['poseViews']['combat']['pixel']['e']));self.api.publish(q)
  with self.assertRaisesRegex(ValueError,'SOURCE_CHANGED'):self.api.publish(stale)
  r=self.packet('replace-request',b'\x00\x00\x00\x18ftypmp42new-video');self.api.publish(r)
  self.assertEqual(len(self.domain.npcs.current()['document']['characters'][0]['animationSlots']),2)
 def test_unregistered_preset_fails_without_changes(self):
  before=self.s.snapshot();p=Tests.packet(self);p['slot']['presetId']='unknown-motion'
  with self.assertRaisesRegex(ValueError,'PRESET_UNREGISTERED'):AnimationPublisher(self.domain).publish(p)
  self.assertEqual(before,self.s.snapshot())
 def test_registered_motion_hash_mismatch_fails(self):
  p=Tests.packet(self);p['slot']['presetHash']='b'*64
  with self.assertRaisesRegex(ValueError,'PRESET_CHANGED'):AnimationPublisher(self.domain).publish(p)
 def test_bad_direction_rejected(self):
  p=self.packet();p['slot']['direction']='up'
  with self.assertRaisesRegex(ValueError,'SLOT_INVALID'):self.api.publish(p)
if __name__=='__main__':unittest.main()
