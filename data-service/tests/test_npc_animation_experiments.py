from test_npc_animations import Tests as Base
import copy,base64
from pathlib import Path
from project_data_service.npc_animation_experiments import AnimationExperimentPublisher
from project_data_service.filex_npc_files import encode,decode
class ExperimentTests(Base):
 def setUp(self):
  super().setUp()
  c=self.domain.npcs.current();d=c['document'];d['characters'][0]['portraitSets']['idle']=d['characters'][0]['portraitSets']['combat'];self.domain.npcs.save(c['revision'],'fixture',d)
  self.doc=d
  self.exp=AnimationExperimentPublisher(self.domain)
 def experiment(self):
  p=self.packet('experiment-request');v=p.pop('slot');p['experiment']={k:v[k]for k in ('referenceType','referenceAssetId','frameCount','fps','cycleCount','presetHash')};p['experiment']['experimentId']='eight-up-run-4x2';return p
 def test_isolated_and_recovery(self):
  self.api.publish(self.packet());before=self.domain.npcs.current()['document'];p=self.experiment();r=self.exp.publish(p);after=self.domain.npcs.current()['document']
  self.assertEqual(after['characters'][0]['animationSlots'],before['characters'][0]['animationSlots']);self.assertEqual(decode(encode(after)),after)
  self.domain.npc_receipts.connection.execute('DELETE FROM npc_animation_experiment_receipts');self.domain.npc_receipts.connection.commit()
  self.assertEqual(self.exp.publish(p),r)
  self.assertIn('character.neutral-fixture/animation-experiments/eight-up-run-4x2.json',self.s.snapshot()['documents'])
 def test_invalid_experiment(self):
  p=self.experiment();p['experiment']['experimentId']='other'
  with self.assertRaises(ValueError):self.exp.publish(p)

 def test_experiment_backup_contains_video(self):
  p=self.experiment();r=self.exp.publish(p);snap=self.s.snapshot();meta=copy.deepcopy(snap['documents']['metadata.json']);meta['characterOrder']=['character.other'];meta['generationEpoch']='delete-experiment';meta['_pdsCurrent']={'revision':3}
  receipt=self.s.delete_character('delete-experiment','i','character.neutral-fixture',snap['sourceVersion'],meta)
  self.assertTrue(receipt['backupVerified'])
  self.assertEqual((Path(self.tmp.name)/'.deleted'/receipt['backupId']/'files/assets'/r['assetId']).read_bytes(),base64.b64decode(p['artifact']['base64']))

 def test_matched_reference_preserves_original_experiment(self):
  p=self.experiment();self.exp.publish(p)
  original=self.domain.npcs.current()['document']['characters'][0]['animationExperiments']['eight-up-run-4x2']
  q=self.experiment();q['requestId']='matched-reference-request';q['experiment']['experimentId']='eight-up-run-4x2-matched-reference'
  receipt=self.exp.publish(q)
  current=self.domain.npcs.current()['document'];slots=current['characters'][0]['animationExperiments']
  self.assertTrue(receipt['verified']);self.assertEqual(slots['eight-up-run-4x2'],original)
  self.assertEqual(set(slots),{'eight-up-run-4x2','eight-up-run-4x2-matched-reference'})
  self.assertEqual(decode(encode(current)),current)
