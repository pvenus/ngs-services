import copy,hashlib,json,unittest
from test_fenced_store import Tests as Fixture
from npc_store import NPC_PREFIX,encoded,digest
from npc_document import decode
from server import Conflict
class ClosureTests(unittest.TestCase):
 setUp=Fixture.setUp
 tearDown=Fixture.tearDown
 def publish_all(self):
  assets={}
  for name in ['portrait','pixel','sd','direction','direction-source','video','raw','alpha','full','half']:
   b=('fixture-'+name).encode();aid=hashlib.sha256(b).hexdigest()+('.mp4' if name=='video' else '.png');self.s.put_asset(aid,b,self.g);assets[name]=aid
  docs={'metadata.json':{'id':'npc','portraitSets':{'combat':{'portraitAssetId':assets['portrait'],'pixelAssetId':assets['pixel'],'pixelSourceAssetId':assets['sd']}}},'planning/overview.json':{'id':'overview','content':'planning retained'},'pose-views/combat/pixel/n.json':{'assetId':assets['direction'],'sourceArtifactId':assets['direction-source']},'animations/combat/sword-attack/n.json':{'assetId':assets['video']}}
  for scale in ['full','half']:
   path='animation-frames/combat/sword-attack'+('/half' if scale=='half' else '')+'.json'
   docs[path]={'directions':{'n':{'sourceAssetId':assets['video'],'frames':[{'index':0,'assetId':assets[scale],'rawAssetId':assets['raw'],'alphaAssetId':assets['alpha']}]}}}
  snap=self.s.snapshot();files=[{'path':NPC_PREFIX+'npc/'+k,'expectedHash':snap['hashes'].get('npc/'+k),'body':v} for k,v in docs.items()]
  self.s.batch('all-types','all-hash',files,{f['path']:f['expectedHash'] for f in files},self.g);self.s.finish(self.g)
  return assets
 def delete(self):
  self.s.prepare_delete('npc',self.gen,'delete-all','a'*64);snap=self.s.snapshot()
  return self.s.delete_character('delete-all','h','npc',snap['sourceVersion'],{'characterOrder':[],'generationEpoch':'delete-all'},{'generationId':self.gen})
 def test_all_current_assets_restore_new_generation_legacy_preserved(self):
  self.publish_all();legacy=self.s.safe('assets/unowned.bin');self.s.write(legacy,b'unknown legacy');before=self.s.owner_files('npc');r=self.delete()
  self.assertEqual(r['dependencyClosure']['slotCount'],10);self.assertEqual(legacy.read_bytes(),b'unknown legacy');self.assertFalse(self.s.owner_files('npc'))
  snap=self.s.snapshot();guard=self.s.prepare_restore('npc','restore-all',r['backupId'],snap['sourceVersion']);self.s.restore_character('restore-all','rh',r['backupId'],snap['sourceVersion'],{'characterOrder':['npc'],'generationEpoch':'restore-all'},guard)
  self.assertEqual(self.s.owner_files('npc'),before);self.assertNotEqual(guard['generationId'],self.gen)
  with self.assertRaises(Conflict):self.s.admit('npc',self.gen,'stale')
  self.assertEqual(legacy.read_bytes(),b'unknown legacy')
 def test_missing_alpha_prevents_delete(self):
  self.publish_all();manifest=self.s.manifests()['npc'];path=next(k for k in manifest['slots'] if '/alpha-' in k);self.s.safe('npc/'+path).unlink()
  with self.assertRaises((Conflict,FileNotFoundError)):self.delete()
  self.assertTrue(self.s.safe('npc/metadata.json').exists())
 def test_backup_manifest_cannot_omit_declared_sd(self):
  self.publish_all();r=self.delete();m,data=self.s.verified_backup(r['backupId']);path=next(k for k in m['files'] if '/pixel-source.' in k);del m['files'][path];self.s.write(self.s.safe('.deleted/'+r['backupId']+'/manifest.json'),encoded(m))
  with self.assertRaisesRegex(Conflict,'closure missing'):self.s.backup_preview(r['backupId'])
 def test_changed_slot_manifest_is_rejected(self):
  self.publish_all();path=self.s.safe('npc/.owned-assets.json');m=json.loads(path.read_bytes());m['slots'].pop(next(iter(m['slots'])));self.s.write(path,encoded(m))
  with self.assertRaisesRegex(Conflict,'closure manifest'):self.delete()
 def test_staged_backup_precedes_external_binding_and_is_reused(self):
  self.publish_all();snap=self.s.snapshot();e=self.s.stage_delete('npc',self.gen,'delete-all',snap['sourceVersion'])
  self.assertEqual(e,self.s.stage_delete('npc',self.gen,'delete-all',snap['sourceVersion']))
  self.assertFalse(e['fence']['legacyResumeBlocked']);self.assertTrue(e['archive']['verified'])
  self.assertEqual(self.s.generation('npc')['state'],'delete-staged')
  with self.assertRaises(Conflict):self.s.admit('npc',self.gen,'late')
  with self.assertRaises(Conflict):self.s.prepare_delete('npc',self.gen,'wrong','a'*64)
  before=self.s.safe('.deleted/'+e['backupId']+'/manifest.json').read_bytes()
  self.s.prepare_delete('npc',self.gen,'delete-all','a'*64)
  result=self.s.delete_character('delete-all','h','npc',snap['sourceVersion'],{'characterOrder':[],'generationEpoch':'delete-all'},{'generationId':self.gen})
  self.assertEqual(result['backupId'],e['backupId']);self.assertEqual(before,self.s.safe('.deleted/'+e['backupId']+'/manifest.json').read_bytes())
 def test_staged_backup_corruption_preserves_current_owner(self):
  self.publish_all();snap=self.s.snapshot();e=self.s.stage_delete('npc',self.gen,'delete-all',snap['sourceVersion'])
  p=self.s.safe('.deleted/'+e['backupId']+'/files/npc/metadata.json');self.s.write(p,b'corrupt')
  with self.assertRaises(Conflict):self.s.prepare_delete('npc',self.gen,'delete-all','a'*64)
  self.assertTrue(self.s.owner_files('npc'))
 def test_unknown_reference_graph_is_not_complete(self):
  self.publish_all();path=self.s.safe('npc/planning/new-unknown.json');self.s.write(path,encoded({'assetId':'a'*64+'.png'}))
  # Tampering cannot pass the manifest guard, let alone claim complete closure.
  with self.assertRaises((Conflict,FileNotFoundError)):self.s.stage_delete('npc',self.gen,'delete-all',self.s.snapshot()['sourceVersion'])
if __name__=='__main__':unittest.main()
