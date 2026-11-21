import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

import deploy_control as D
import server as S
from test_server import started


SHA='a'*40
OTHER='b'*40
KEY='d'*64
def private(path,data):
    path.write_bytes(data)
    path.chmod(0o600)


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        private(self.root/'owner-key',(KEY+'\n').encode())
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
    def wake(self,p,auth,target='ngs-local'):
        return self.req(p,'/api/deployments',{'action':'wake','target':target},auth)

    def test_default_off_and_not_discoverable_through_heartbeat(self):
        self.assertIsNone(D.from_environment({},root=self.root))
        with started(S.server(0)) as port:
            self.assertEqual(self.req(port,'/api/deployments')[0],503)
            self.assertEqual(self.wake(port,{})[0],503)
            self.assertEqual(self.req(port,'/deployments')[0],200)
            bad={"jsonrpc":"2.0","id":71,"method":"tools/call","params":{"name":"deploy","arguments":{"target":"all"}}}
            self.assertEqual(self.req(port,'/mcp',bad)[0],403)

    def test_owner_origin_csrf_only_fixed_cycle_and_no_legacy_bypass(self):
        hits=[]
        class Cycle:
            def status(self): return {'allReady':False,'targets':[]}
            def trigger(self, target):
                if target not in ('ngs-local','worker-file'):
                    D.reject(503,'NOT_READY')
                hits.append(target)
                return {'active':{'selection':target}}
        c=D.DeployControl(self.root,cycle=Cycle())
        with started(S.server(0,deploy_control=c)) as port:
            self.assertEqual(self.req(port,'/api/deployments')[0],401)
            self.assertEqual(self.req(port,'/api/deployments/session',{'ownerKey':KEY},{'Origin':'http://wrong.invalid'})[0],403)
            self.assertEqual(self.req(port,'/api/deployments/session',{'ownerKey':KEY},{'Sec-Fetch-Site':'cross-site'})[0],403)
            self.assertEqual(self.req(port,'/api/deployments/session',{'ownerKey':'e'*64})[0],403)
            sid,data=self.auth(port)
            self.assertEqual(data['cycle'],{'allReady':False,'targets':[],'sourceComparison':'unknown'})
            self.assertEqual(self.wake(port,sid)[0],403)
            self.assertEqual(self.wake(port,{**sid,'X-OPS-Deploy-CSRF':'e'*64})[0],403)
            verified={**sid,'X-OPS-Deploy-CSRF':data['csrf']}
            for value in [
                {'action':'deploy','sha':SHA,'target':'local'},
                {'action':'wake','target':'worker-file','argv':['bad']},
                {'action':'wake','target':[]},
                {'action':'wake','target':'../../ops'},
                b'{"action":"wake","action":"wake","target":"all"}',
            ]:
                self.assertEqual(self.req(port,'/api/deployments',value,verified)[0],400)
            self.assertEqual(self.wake(port,verified,'all')[0],503)
            self.assertEqual(self.wake(port,verified,'ops')[0],503)
            code,_,body=self.wake(port,verified,'worker-file')
            self.assertEqual((code,json.loads(body)),(202,{'active':{'selection':'worker-file'}}))
            self.assertEqual(hits,['worker-file'])

    def test_private_permissions_stale_session(self):
        clock=[1]
        class Cycle:
            def status(self): return {}
            def trigger(self, target): raise AssertionError('no execution')
        c=D.DeployControl(self.root,cycle=Cycle(),clock=lambda:clock[0])
        with started(S.server(0,deploy_control=c)) as port:
            sid,data=self.auth(port)
            clock[0]+=D.TTL
            self.assertEqual(self.wake(port,{**sid,'X-OPS-Deploy-CSRF':data['csrf']})[0],401)
            (self.root/'owner-key').chmod(0o644)
            self.assertEqual(self.req(port,'/api/deployments/session',{'ownerKey':KEY})[0],503)

    def test_prior_verified_receipts_are_read_only(self):
        class Cycle:
            def status(self): return {}
        when='2026-11-22T04:05:00Z'
        obj={'sha':SHA,'deployedAt':when}
        native={**obj,'providerSha':OTHER,'deploymentId':'release.real-42'}
        def observed(local,space):
            private(self.root/'observed.json',json.dumps({
                'schemaVersion':'ops.ngs-observed-deployments.v1','local':local,'space':space}).encode())
        observed(obj,native)
        with started(S.server(0,deploy_control=D.DeployControl(self.root,cycle=Cycle()))) as port:
            sid,data=self.auth(port)
            self.assertEqual(data['observed'],{'local':obj,'space':native})
            observed(obj,{**native,'deploymentId':'../../fake'})
            self.assertEqual(self.req(port,'/api/deployments',headers=sid)[0],503)
            self.assertNotEqual(self.req(port,'/api/deployments/native-receipt',native,headers=sid)[0],200)

    def test_owner_private_manual_opt_in_requires_exact_file(self):
        toggle=self.root/'manual-enabled'
        private(toggle,b'1\n')
        self.assertIsNotNone(D.from_environment({},root=self.root))
        self.assertIsNone(D.from_environment({'NGS_OPS_DEPLOY_ENABLED':'0'},root=self.root))
        toggle.chmod(0o644)
        self.assertIsNone(D.from_environment({},root=self.root))
        private(toggle,b'0\n')
        self.assertIsNone(D.from_environment({},root=self.root))

    def test_periodic_path_calls_the_same_cycle_sweep(self):
        stop=threading.Event()
        class Cycle:
            count=0
            def sweep(self):
                self.count+=1
                stop.set()
        cycle=Cycle()
        S.run_deploy_schedule(cycle,stop,interval=0)
        self.assertEqual(cycle.count,1)
