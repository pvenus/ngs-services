import copy,hashlib,importlib.util,json,pathlib,sqlite3,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]))
from workflow_decision_service.service import WorkflowDecisionService
class Tests(unittest.TestCase):
 def setUp(self):self.service=WorkflowDecisionService()
 def request(self,n,pose='idle'):return {'schemaVersion':'ngs.npc-pose-expansion-request.v1','projectId':'binari','npcId':'character.neutral-fixture','posePreset':pose,'referenceType':'pixel','intent':'가'*n}
 def test_preserves_full_accepted_intent_and_worker(self):
  for n in (1,2000,2001,2802,4000,4001,12000):
   for pose in ('idle','combat'):
    request=self.request(n,pose);before=copy.deepcopy(request);r=self.service.select_workflow({'mode':'single','requestType':'npc.pose.expand-eight-directions','request':request})
    self.assertEqual(request,before);self.assertEqual(r['requestHash'],hashlib.sha256(json.dumps(request,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest());self.assertEqual(r['stages'][0]['workerId'],'npc-pose-expansion')
 def test_overflow_unknown_field_wrong_target_rejected(self):
  for request in (self.request(0),{**self.request(2802),'extra':True},{**self.request(2802),'projectId':'other'}):
   with self.assertRaises(ValueError):self.service.select_workflow({'mode':'single','requestType':'npc.pose.expand-eight-directions','request':request})

class ParityTests(unittest.TestCase):
 def test_active_expansion_regeneration_admission(self):
  root=pathlib.Path(__file__).parents[2]
  for file in ('contracts/workflow_admission.py',):
   spec=importlib.util.spec_from_file_location('admission_fixture',root/file);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
   for schema in ('ngs.npc-pose-expansion-request.v1','ngs.npc-pose-regeneration-request.v1'):
    r={'schemaVersion':schema,'projectId':'binari','npcId':'character.neutral-fixture','posePreset':'combat','referenceType':'pixel','intent':'가'*(12000 if 'expansion'in schema else 2000)}
    if 'regeneration'in schema:r['direction']='n'
    m.validate_pose_selection(r)
    with self.assertRaises(ValueError):m.validate_pose_selection({**r,'intent':'   '})

class LargeHTTPTests(unittest.TestCase):
 def test_large_original_http_and_other_operation_cap(self):
  import threading,urllib.request,urllib.error
  from wsgiref.simple_server import make_server,WSGIRequestHandler
  from workflow_decision_service.http_api import WorkflowDecisionApi
  class Quiet(WSGIRequestHandler):
   def log_message(self,*args):pass
  service=WorkflowDecisionService();api=WorkflowDecisionApi(service,port=0);server=make_server('127.0.0.1',0,api,handler_class=Quiet);api.port=server.server_port;t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
  try:
   request={'schemaVersion':'ngs.npc-pose-expansion-request.v1','projectId':'binari','npcId':'character.neutral-fixture','posePreset':'combat','referenceType':'pixel','intent':'원문 보존 '*30000}
   p={'mode':'single','requestType':'npc.pose.expand-eight-directions','request':request};raw=json.dumps(p,ensure_ascii=False).encode();self.assertGreater(len(raw),100000)
   def post(payload,path='/v1/workflow-selections'):
    return urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+path,data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'}),timeout=10)
   with post(p)as response:r=json.load(response)
   expected=hashlib.sha256(json.dumps(request,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest();self.assertEqual(r['requestHash'],expected)
   for other,path in (({**p,'requestType':'npc.pose.regenerate-one'},'/v1/workflow-selections'),(p,'/v1/request-routing')):
    with self.assertRaises(urllib.error.HTTPError)as e:post(other,path)
    self.assertEqual(json.load(e.exception)['error'],'WORKFLOW_BODY_SIZE_INVALID')
  finally:server.shutdown();server.server_close();t.join()
