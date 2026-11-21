import json
from pathlib import Path
import tempfile
import unittest

from deploy_control import DeployControl
from deploy_cycle import SPACE_HOST, SPACE_SITE, SPACE_PUBLICATION_SCHEMA, persist
from space_publish_api import THREAD_ID
import test_deploy_control as fixtures
from test_server import started
import server


A='a'*40
B='b'*40
LOCAL_PUBLISH_KEY='9'*64


class Candidate:
    def current(self,*,force=False):
        return A


class PublicationApi(unittest.TestCase):
    req=fixtures.DeployTests.req

    def test_owner_click_and_mcp_claim_no_direct_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            (root/'targets').mkdir(mode=0o700)
            path=root/'targets/ngs-space'
            path.mkdir(mode=0o700)
            (root/'owner-key').write_text('d'*64);(root/'owner-key').chmod(0o600)
            (path/'publisher-key').write_text(LOCAL_PUBLISH_KEY);(path/'publisher-key').chmod(0o600)
            persist(path/'publication.json',{'schemaVersion':SPACE_PUBLICATION_SCHEMA,
                    'provider':'chatgpt-sites','projectId':SPACE_SITE,'hostname':SPACE_HOST,'accessMode':'custom'})
            persist(path/'handoff.json',{'threadId':THREAD_ID,'title':'비나리 스페이스 배포'})
            with started(server.server(0,deploy_control=DeployControl(root),space_candidate=Candidate())) as port:
                import http.client
                def mcp(name,args, token=LOCAL_PUBLISH_KEY, browser=False):
                    msg={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':name,'arguments':args}}
                    c=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
                    try:
                        headers={'Content-Type':'application/json','X-OPS-Space-Publisher':token}
                        if browser:headers['Sec-Fetch-Site']='same-origin'
                        c.request('POST','/mcp',json.dumps(msg),headers)
                        r=c.getresponse(); return r.status,json.loads(r.read())
                    finally:c.close()
                status,cookie,_=self.req(port,'/api/deployments/session',{'ownerKey':'d'*64})
                self.assertEqual(status,200)
                session={'Cookie':cookie.split(';')[0]}
                status,_,raw=self.req(port,'/api/deployments',headers=session)
                self.assertEqual(status,200)
                doc=json.loads(raw)
                item=next(x for x in doc['cycle']['targets'] if x['id']=='ngs-space')
                self.assertEqual(item['publication']['candidateSha'],A)
                self.assertTrue(item['publication']['canRequest'])
                msg={'action':'queue-space','target':'ngs-space','sha':A}
                self.assertEqual(self.req(port,'/api/deployments',msg,session)[0],403)
                authorized={**session,'X-OPS-Deploy-CSRF':doc['csrf']}
                self.assertEqual(self.req(port,'/api/deployments',{**msg,'sha':B},authorized)[0],409)
                status,_,raw=self.req(port,'/api/deployments',msg,authorized);self.assertEqual(status,202)
                ticket=json.loads(raw)['request']; self.assertEqual(ticket['state'],'pending')
                self.assertIn('error',mcp('ops_space_publish_peek',{},'0'*64)[1])
                self.assertEqual(mcp('ops_space_publish_peek',{},browser=True)[0],403)
                self.assertEqual(mcp('ops_space_publish_peek',{})[1]['result']['structuredContent']['request']['commitSha'],A)
                args={'requestId':ticket['requestId']}
                claim=mcp('ops_space_publish_claim',args)[1]['result']['structuredContent']['request']
                self.assertEqual((claim['state'],claim['threadId']),('claimed',THREAD_ID))
                self.assertIn('error',mcp('ops_space_publish_claim',args)[1])
                self.assertIsNone(mcp('ops_space_publish_peek',{})[1]['result']['structuredContent']['request'])
                result={'state':'uncertain','code':'NATIVE_PROOF_UNAVAILABLE'}
                out=mcp('ops_space_publish_finish',{**args,'report':result})[1]['result']['structuredContent']
                self.assertEqual(out['request']['state'],'uncertain')
                self.assertFalse((root/'active.json').exists(),'Space queue must never start a native deployment')
