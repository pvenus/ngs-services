import copy,json,unittest
from pathlib import Path
from workflow_decision_service.one_stop import select,digest,stages_from
class Tests(unittest.TestCase):
 def request(self,combat=False):
  r={'schemaVersion':'ngs.npc-one-stop.v1','projectId':'binari','includeCombat':combat}
  if combat:r['combatMotionPresets']=['fixture.motion-a','fixture.motion-b']
  return r
 def test_server_target_limits_pilot_and_other_requests_remain_v3(self):
  from workflow_decision_service.service import WorkflowDecisionService
  server=WorkflowDecisionService(one_stop_direction_streaming=True,one_stop_direction_target='npc.fixture')
  for npc,version in [('npc.fixture','4'),('npc.fixture-other','3'),(None,'3')]:
   body={**self.request(),'character':{'npcId':npc}}
   self.assertEqual(server.one_stop_plan(body)['version'],version)
  with self.assertRaisesRegex(ValueError,'TARGET_INVALID'):
   WorkflowDecisionService(one_stop_direction_target='npc.fixture')
 def test_noncombat_dag_parallel_video_and_frame(self):
  p=select(self.request());s={x['id']:x['dependsOn']for x in p['stages']}
  self.assertEqual(p['schemaVersion'],'ngs.npc-one-stop-plan.v2');self.assertEqual(len(s),12);self.assertEqual(p['version'],'3');self.assertEqual(s['portrait_representative'],['planning_idle'])
  self.assertEqual(s['animation_run'],['idle_8way']);self.assertEqual(s['animation_walk'],['idle_8way']);self.assertEqual(s['frames_walk'],['animation_walk'])
  self.assertFalse(any('combat'in x for x in s))
 def test_combat_explicit_presets_dag_and_binding(self):
  r=self.request(True);before=copy.deepcopy(r);p=select(r);s={x['id']:x['dependsOn']for x in p['stages']}
  self.assertEqual(r,before);self.assertEqual(len(s),19);self.assertEqual(s['portrait_combat'],['planning_combat','portrait_idle']);self.assertEqual(s['animation_combat_1'],['combat_8way']);self.assertEqual(s['frames_combat_0'],['animation_combat_0']);self.assertEqual(p['requestHash'],digest(r));self.assertEqual(p['planHash'],digest(p['stages']));self.assertEqual(p['modelCalls']+p['workerCalls'],0)
 def test_combat_global_revision_writers_are_serial_then_branches_parallel(self):
  stages=select(self.request(True))['stages'];deps={s['id']:s['dependsOn']for s in stages}
  def ancestors(stage):return set(deps[stage]).union(*(ancestors(d)for d in deps[stage]))
  chain=['planning_representative','planning_idle','planning_combat','portrait_representative','portrait_idle','portrait_combat']
  for before,after in zip(chain,chain[1:]):self.assertIn(before,ancestors(after))
  for stage in ('idle_8way','combat_8way'):self.assertIn('portrait_combat',ancestors(stage))
  self.assertNotIn('idle_8way',ancestors('combat_8way'));self.assertNotIn('frames_walk',ancestors('animation_run'))
 def test_no_implicit_attack_or_preset_guess(self):
  for presets in (None,[],['a','a'],['../a'],[''],[1],['a/b'],['motion.'+str(i)for i in range(17)]):
   r=self.request(True)
   if presets is None:r.pop('combatMotionPresets')
   else:r['combatMotionPresets']=presets
   with self.assertRaises(ValueError):select(r)
 def test_false_rejects_preset_field_and_bool_coercion(self):
  r=self.request();r['combatMotionPresets']=[]
  with self.assertRaises(ValueError):select(r)
  for value in (None,0,1,'true'):
   with self.assertRaises(ValueError):select(dict(self.request(),includeCombat=value))
 def test_registry_invalid_dag_rejected(self):
  c=json.loads((Path(__file__).parents[1]/'workflow_decision_service/one_stop_registry.json').read_text());c['stages'][0]['dependsOn']=['frames_run']
  with self.assertRaisesRegex(ValueError,'DEPENDENCIES'):stages_from(c,self.request())
 def test_deterministic_preset_order_is_request_bound(self):
  r=self.request(True);self.assertEqual(select(r),select(r));p=select(r);r['combatMotionPresets'].reverse();self.assertNotEqual(p['requestHash'],select(r)['requestHash'])
 def test_real_api_returns_v2_and_rejects_missing_combat_inputs(self):
  import io
  from workflow_decision_service.service import WorkflowDecisionService
  from workflow_decision_service.http_api import WorkflowDecisionApi
  api=WorkflowDecisionApi(WorkflowDecisionService())
  def call(request):
   data=json.dumps(request).encode();status=[]
   result=api({'PATH_INFO':'/v1/npc-one-stop-plans','REQUEST_METHOD':'POST','REMOTE_ADDR':'127.0.0.1','HTTP_HOST':'127.0.0.1:8040','CONTENT_LENGTH':str(len(data)),'wsgi.input':io.BytesIO(data)},lambda code,headers:status.append(code))
   return status[0],json.loads(b''.join(result))
  code,p=call(self.request(True));self.assertEqual(code,'200 OK');self.assertEqual(p['schemaVersion'],'ngs.npc-one-stop-plan.v2')
  r=self.request(True);del r['combatMotionPresets'];code,p=call(r);self.assertEqual(code,'422 Unprocessable Entity');self.assertEqual(p['error'],'ONE_STOP_COMBAT_PRESETS_REQUIRED')
if __name__=='__main__':unittest.main()
