import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

import deploy_control as D
import server as S
from test_server import started


SHA='a'*40
OTHER='b'*40
KEY='d'*64
PREPARED={
    'schemaVersion':'ops.ngs-prepared-deploy.v1','sha':SHA,'verifiedAt':'2026-11-22T04:00:00Z',
    'mac':{'ready':True},'provenance':{'runId':'1','runAttempt':'2','artifactId':'3','archiveSha256':'c'*64},
}
def private(path,data):
    path.write_bytes(data)
    path.chmod(0o600)


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        (self.root/'jobs').mkdir(mode=0o700)
        private(self.root/'owner-key',(KEY+'\n').encode())
        self.prepared()
    def prepared(self,**more):private(self.root/'prepared.json',json.dumps({**PREPARED,**more}).encode())
    def req(self,port,path,value=None,headers=None,*,method=None):
        c=http.client.HTTPConnection('127.0.0.1',port,timeout=2)
        try:
            body=None if value is None else value if isinstance(value,bytes) else json.dumps(value)
            h={'Sec-Fetch-Site':'same-origin'}
            if body is not None:
                h.update({'Content-Type':'application/json','Origin':f'http://127.0.0.1:{port}'})
            c.request(method or ('POST' if body is not None else 'GET'),path,body,headers={**h,**(headers or {})})
            r=c.getresponse();raw=r.read()
            return r.status,r.getheader('Set-Cookie'),raw
        finally:c.close()
    def auth(self,port):
        status,cookie,reply=self.req(port,'/api/deployments/session',{'ownerKey':KEY})
        self.assertEqual((status,json.loads(reply)),(200,{'ok':True}))
        self.assertIn('HttpOnly; SameSite=Strict; Path=/api/deployments',cookie)
        sid={'Cookie':cookie.split(';')[0]}
        status,_,raw=self.req(port,'/api/deployments',headers=sid)
        self.assertEqual(status,200)
        return sid,json.loads(raw)
    def apply(self,p,auth,sha=SHA):
        return self.req(p,'/api/deployments',{'sha':sha,'action':'deploy'},auth)
    def test_default_off_and_not_discoverable_through_heartbeat(self):
        self.assertIsNone(D.from_environment({}))
        with started(S.server(0)) as port:
            self.assertEqual(self.req(port,'/api/deployments')[0],503)
            self.assertEqual(self.apply(port,{})[0],503)
            self.assertEqual(self.req(port,'/deployments')[0],200)
            bad={"jsonrpc":"2.0","id":71,"method":"tools/call","params":{"name":"deploy","arguments":{"sha":SHA}}}
            # Heartbeat still rejects all browser-origin POSTs.
            self.assertEqual(self.req(port,'/mcp',bad)[0],403)
    def test_owner_origin_csrf_exact_sha_and_atomic_concurrent_replay(self):
        running,end=threading.Event(),threading.Event()
        hits=[]
        def runner(root,sha):
            hits.append((root,sha));running.set()
            self.assertTrue(end.wait(4))
            return {'sha':sha,'target':'mac','state':'deployed','health':'verified'}
        c=D.DeployControl(self.root,run_mac=runner)
        self.prepared(space={'sourceSha':SHA,'packageBaseSha':OTHER,'archiveSha256':'f'*64})
        with started(S.server(0,deploy_control=c)) as port:
            self.assertEqual(self.req(port,'/api/deployments')[0],401)
            self.assertEqual(self.req(port,'/api/deployments/session',{'ownerKey':KEY},{'Origin':'http://wrong.invalid'})[0],403)
            self.assertEqual(self.req(port,'/api/deployments/session',{'ownerKey':KEY},{'Sec-Fetch-Site':'cross-site'})[0],403)
            self.assertEqual(self.req(port,'/api/deployments/session',{'ownerKey':'e'*64})[0],403)
            sid,data=self.auth(port)
            self.assertEqual((data['sha'],data['mac'],data['space'],data['canDeploy']),(SHA,'ready','pending',True))
            self.assertNotIn('archiveSha256',data)
            self.assertEqual(self.apply(port,sid)[0],403)
            self.assertEqual(self.apply(port,{**sid,'X-OPS-Deploy-CSRF':'x'*64})[0],403)
            verified={**sid,'X-OPS-Deploy-CSRF':data['csrf']}
            self.assertEqual(self.apply(port,verified,OTHER)[0],409)
            self.assertEqual(self.req(port,'/api/deployments',{'action':'deploy','sha':SHA,'argv':['bad']},verified)[0],400)
            self.assertEqual(self.req(port,'/api/deployments',b'{"sha":"'+SHA.encode()+b'","action":"deploy","action":"deploy"}',verified)[0],400)
            with ThreadPoolExecutor(2) as threads:
                result=list(threads.map(lambda _:self.apply(port,verified),range(2)))
            self.assertTrue(running.is_set())
            self.assertEqual([r[0] for r in result],[202,202])
            self.assertEqual([json.loads(r[2])['mac'] for r in result],['running','running'])
            self.prepared(sha=OTHER)
            self.assertEqual(self.apply(port,verified,OTHER)[0],409)
            self.prepared(space={'sourceSha':SHA,'packageBaseSha':OTHER,'archiveSha256':'f'*64})
            end.set()
            for _ in range(80):
                _,_,raw=self.req(port,'/api/deployments',headers=sid)
                if json.loads(raw)['mac']=='deployed':break
                time.sleep(.02)
            else:self.fail('durable completion not observed')
            self.assertEqual(json.loads(self.apply(port,verified)[2])['mac'],'deployed')
            self.assertEqual(len(hits),1)
            self.assertEqual(next((self.root/'jobs').glob('*.json')).stat().st_mode&0o777,0o600)
        # Persisted completion/replay accounting survives an OPS restart.
        c=D.DeployControl(self.root,run_mac=runner)
        with started(S.server(0,deploy_control=c)) as port:
            _,data=self.auth(port)
            self.assertEqual(data['mac'],'deployed')
            self.assertEqual(len(hits),1)
    def test_private_permissions_stale_session_and_uncertain_not_automatically_retried(self):
        clock=[1]
        c=D.DeployControl(self.root,run_mac=lambda *_:self.fail('no execution'),clock=lambda:clock[0])
        private(self.root/'jobs'/f'{SHA}.json',json.dumps({'sha':SHA,'state':'running'}).encode())
        with started(S.server(0,deploy_control=c)) as port:
            sid,data=self.auth(port)
            self.assertEqual((data['mac'],data['canDeploy']),('unknown',False))
            verified={**sid,'X-OPS-Deploy-CSRF':data['csrf']}
            self.assertEqual(json.loads(self.apply(port,verified)[2])['mac'],'unknown')
            clock[0]+=D.TTL
            self.assertEqual(self.apply(port,verified)[0],401)
            (self.root/'owner-key').chmod(0o644)
            self.assertEqual(self.req(port,'/api/deployments/session',{'ownerKey':KEY})[0],503)
    def test_real_fixed_adapter_invoked_once_and_exact_verified_receipt_required(self):
        (self.root/'bin').mkdir(mode=0o700)
        exec_path=self.root/'bin/ngs-mac-apply'
        source=("#!/bin/sh\n"
                "test \"$#\" = 1 || exit 1\n"
                "printf '%s\\n' \"$1\" >> invocations\n"
                "printf '{\"sha\":\"%s\",\"target\":\"mac\",\"state\":\"deployed\",\"health\":\"verified\"}\\n' \"$1\"\n")
        exec_path.write_text(source);exec_path.chmod(0o700)
        with started(S.server(0,deploy_control=D.DeployControl(self.root))) as port:
            sid,data=self.auth(port)
            verified={**sid,'X-OPS-Deploy-CSRF':data['csrf']}
            self.assertEqual(self.apply(port,verified)[0],202)
            for _ in range(80):
                _,_,raw=self.req(port,'/api/deployments',headers=sid)
                if json.loads(raw)['mac']=='deployed':break
                time.sleep(.02)
            else:self.fail('no completion')
            self.assertEqual(self.apply(port,verified)[0],202)
            self.assertEqual((self.root/'invocations').read_text().splitlines(),[SHA])
    def test_unconfigured_or_unverified_package_never_launches(self):
        c=D.DeployControl(self.root)
        with started(S.server(0,deploy_control=c)) as port:
            sid,data=self.auth(port)
            self.assertEqual(data['mac'],'not_configured')
            self.assertEqual(self.apply(port,{**sid,'X-OPS-Deploy-CSRF':data['csrf']})[0],503)
            self.prepared(provenance={'runId':'x'})
            self.assertEqual(self.req(port,'/api/deployments',headers=sid)[0],503)
