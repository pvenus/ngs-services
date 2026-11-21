"""Original decision HTTP boundary; no new route or worker calls."""
import io
import json
import unittest
from workflow_decision_service.http_api import WorkflowDecisionApi
from workflow_decision_service.service import WorkflowDecisionService
from test_one_stop_representative_first import body

class Tests(unittest.TestCase):
    def test_exact_internal_hint_with_existing_pilot_only(self):
        request=body(False)
        app=WorkflowDecisionApi(WorkflowDecisionService(one_stop_direction_streaming=True,
            one_stop_direction_target='npc.fixture'))
        def call(payload,hint=None,app=app):
            data=json.dumps(payload).encode(); out=[]
            env={'PATH_INFO':'/v1/npc-one-stop-plans','REQUEST_METHOD':'POST','REMOTE_ADDR':'127.0.0.1',
                 'HTTP_HOST':'127.0.0.1:8040','CONTENT_LENGTH':str(len(data)),'wsgi.input':io.BytesIO(data)}
            if hint is not None:env['HTTP_X_NGS_ONE_STOP_PLAN']=hint
            obj=json.loads(b''.join(app(env,lambda status,headers:out.append(status))))
            return out[0],obj
        self.assertEqual(call(request)[1]['version'],'4')
        self.assertEqual(call(request,'representative-first-v5')[1]['version'],'5')
        for wrong in ({**request,'includeCombat':True},
                      {**request,'character':{**request['character'],'npcId':'npc.other'}}):
            self.assertEqual(call(wrong,'representative-first-v5')[0],'422 Unprocessable Entity')
        self.assertEqual(call(request,'unrecognized')[0],'422 Unprocessable Entity')
        self.assertEqual(call(request,'representative-first-v5',
             app=WorkflowDecisionApi(WorkflowDecisionService()))[0],'422 Unprocessable Entity')
