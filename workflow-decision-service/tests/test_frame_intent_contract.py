"""Frame-only length removal; selector hashes the complete unchanged request."""
import copy,hashlib,importlib.util,json,pathlib,sys,threading,unittest,urllib.request,urllib.error
from wsgiref.simple_server import make_server,WSGIRequestHandler
sys.path.insert(0,str(pathlib.Path(__file__).parents[1]))
from workflow_decision_service.service import WorkflowDecisionService
from workflow_decision_service.http_api import WorkflowDecisionApi

def request(n):
 return {'schemaVersion':'ngs.npc-animation-frames-request.v1','projectId':'binari','npcId':'character.neutral-fixture','posePreset':'idle','presetId':'run','intent':'가'*n}
def envelope(r):return {'mode':'single','requestType':'npc.animation.frames.generate','request':r}
def sha(r):return hashlib.sha256(json.dumps(r,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
class Tests(unittest.TestCase):
 def test_select_original_2802_and_unbounded(self):
  service=WorkflowDecisionService()
  for n in (2000,2802,4001,180000):
   r=request(n);before=copy.deepcopy(r);result=service.select_workflow(envelope(r))
   self.assertEqual(r,before);self.assertEqual(result['requestHash'],sha(r));self.assertEqual(result['stages'][0]['workerId'],'npc-animation-frames')
 def test_active_admission_shape_preserved(self):
  root=pathlib.Path(__file__).parents[2]
  for file in ('contracts/workflow_admission.py',):
   spec=importlib.util.spec_from_file_location('frame_admission_fixture',root/file);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
   for n in (2802,180000):m.validate_pose_selection(request(n))
   for r in ({**request(3),'intent':'  '},{**request(3),'intent':4},{**request(3),'extra':True},{**request(3),'projectId':'other'},{**request(3),'scalePreset':'half'}):
    with self.assertRaises(ValueError):m.validate_pose_selection(r)
   with self.assertRaises(ValueError):m.validate_pose_selection(request(2802),[{'id':'wrong','workerId':'npc-animation-frames'}])
   regen={'schemaVersion':'ngs.npc-pose-regeneration-request.v1','projectId':'binari','npcId':'character.neutral-fixture','posePreset':'idle','referenceType':'pixel','direction':'n','intent':'가'*2001}
   with self.assertRaises(ValueError):m.validate_pose_selection(regen)
 def test_actual_large_http_exact_hash_other_routes_and_auth_unchanged(self):
  class Quiet(WSGIRequestHandler):
   def log_message(self,*a):pass
  api=WorkflowDecisionApi(WorkflowDecisionService(),port=0);server=make_server('127.0.0.1',0,api,handler_class=Quiet);api.port=server.server_port;t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
  def post(p,path='/v1/workflow-selections',headers=None):
   return urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+path,data=json.dumps(p,ensure_ascii=False).encode(),headers={'Content-Type':'application/json',**(headers or {})}),timeout=10)
  try:
   r=request(180000);r['intent']='  '+r['intent']+'\n';p=envelope(r);self.assertGreater(len(json.dumps(p,ensure_ascii=False).encode()),100000)
   with post(p)as response:self.assertEqual(json.load(response)['requestHash'],sha(r))
   for invalid,path in (({**p,'requestType':'npc.animation.generate'},'/v1/workflow-selections'),):
    with self.assertRaises(urllib.error.HTTPError)as e:post(invalid,path)
    self.assertEqual(json.load(e.exception)['error'],'WORKFLOW_BODY_SIZE_INVALID')
   import io
   raw=json.dumps(p,ensure_ascii=False).encode();statuses=[]
   response=api({'REMOTE_ADDR':'127.0.0.1','HTTP_HOST':f'127.0.0.1:{server.server_port}','REQUEST_METHOD':'POST','PATH_INFO':'/v1/request-routing','CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw)},lambda status,headers:statuses.append(status))
   self.assertEqual(json.loads(b''.join(response))['error'],'WORKFLOW_BODY_SIZE_INVALID')
   self.assertTrue(statuses[0].startswith('422'))
   with self.assertRaises(urllib.error.HTTPError)as e:post(envelope(request(2802)),headers={'Origin':'http://other'})
   self.assertEqual(e.exception.code,403)
  finally:server.shutdown();server.server_close();t.join()
