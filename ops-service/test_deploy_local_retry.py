"""Authenticated simulated OPS → fixed adapter → disposable native worker."""
import hashlib
import json
import os
from pathlib import Path
import unittest

import deploy_control as D
import deploy_cycle as C
import server as S
import test_deploy_control as T
from test_deploy_control import KEY, private
import test_deploy_cycle as F
from test_deploy_cycle import A, B, HASH, STAMP
from test_server import started


class RetryTests(unittest.TestCase):
    setUp=F.Cycle.setUp
    stage=F.Cycle.stage
    drain=F.Cycle.drain
    target=F.Cycle.target
    req=T.DeployTests.req
    auth=T.DeployTests.auth

    def fixture(self):
        # Real adapter source, with only its fixed sandbox root redirected.
        src=(Path(__file__).parent/'deploy/ngs-local-run-once').read_text()
        src=src.replace("CONTROL = Path.home() / 'Workspaces/OPS/runtime/deploy-control'",
                        f"CONTROL = Path({str(self.root)!r})")
        src=src.replace('#!/opt/homebrew/bin/python3.12',f'#!{os.sys.executable}')
        path=self.stage('ngs-local',source=src.encode())
        self.path=path
        self.original_result={'schemaVersion':C.TERMINAL,'target':'ngs-local',
         'attemptedSha':A,'state':'failed','finishedAt':STAMP,'observed':{'sha':B,'deployedAt':STAMP}}
        C.persist(path/'last-result.json',self.original_result)
        self.folder=self.root/'mac-releases'/A
        self.folder.parent.mkdir(mode=0o700)
        self.folder.mkdir(mode=0o700)
        self.prior=b'archived private contents'
        private(self.folder/'prior.plist',self.prior)
        C.persist(self.folder/'result.json',{'state':'rolled-back'})
        C.persist(self.folder/'manifest.json',{'schemaVersion':'ops.ngs-mac-release.v1','sha':A,
                  'archiveSha256':HASH,'expectedPriorPlistSha256':hashlib.sha256(self.prior).hexdigest()})
        C.persist(self.root/'prepared.json',{'schemaVersion':'ops.ngs-prepared-deploy.v1','sha':A,
                  'provenance':{'archiveSha256':HASH},'mac':{'ready':True}})
        self.bin=self.root/'bin'
        self.bin.mkdir(mode=0o700)
        self.executable=self.bin/'ngs-mac-apply'
        # Deliberately stale ordinary ready runner pin. A separate private
        # activation record enables only explicit retry, never wake/sweep.
        C.persist(path/'ready.json',{**C.optional(path/'ready.json'),'runnerSha256':'e'*64})
        private(self.root/'owner-key',(KEY+'\n').encode())
        return C.DeploymentCycle(self.root)

    def fake_native(self, first_state, second_state=None):
        src=f"#!{os.sys.executable}\n"+'''import sys,json,os,pathlib
os.umask(0o077)
root=pathlib.Path.cwd()
sha,new,previous=sys.argv[1:]
folder=root/'mac-releases'/sha
history=folder/'attempts'
ledger=history/'current.json'
ids=json.loads(ledger.read_text())['attemptIds'] if ledger.is_file() else []
assert previous==(ids[-1] if ids else 'initial')
assert new not in ids
invocations=root/'native-invocations'
with invocations.open('a') as f:f.write(json.dumps([sha,new,previous])+'\\n')
if MODE is None:
    print(json.dumps({'schemaVersion':'ops.ngs-mac-apply-error.v1','failure':
        {'stage':'check_current','code':'MAC_RETRY_PRIOR_DRIFT'}}),file=sys.stderr)
    sys.exit(1)
history.mkdir(exist_ok=True,mode=0o700)
attempt=history/new
attempt.mkdir(mode=0o700)
ledger.write_text(json.dumps({'attemptIds':ids+[new]}))
(attempt/'prior.plist').write_bytes((folder/'prior.plist').read_bytes())
mode=SECOND if ids else MODE
if mode=='rolled-back':
    receipt={'state':'rolled-back','failure':{'stage':'check_candidate','code':'NATIVE_HEALTH_UNVERIFIED'}}
elif mode=='rollback-failed':
    receipt={'state':'rollback-failed','failure':{'stage':'check_candidate','code':'NATIVE_HEALTH_UNVERIFIED'},
             'rollbackFailure':{'stage':'rollback_check','code':'MAC_ROLLBACK_CHECK_FAILED'}}
else:
    assert mode=='deployed'
    receipt={'sha':sha,'target':'mac','state':'deployed','health':'verified','deployedAt':'2026-11-22T15:00:00Z'}
(attempt/'result.json').write_text(json.dumps(receipt))
sys.exit(mode!='deployed')
'''
        src=src.replace('import sys,json,os,pathlib','import sys,json,os,pathlib\nMODE='+repr(first_state)+'\nSECOND='+repr(second_state))
        self.executable.write_text(src);self.executable.chmod(0o700)
        C.persist(self.path/'retry-runner.json',{'schemaVersion':C.RETRY_RUNNER,
           'nativeSha256':C.verified_runner(self.executable),'adapterSha256':C.verified_runner(self.path/'run-once')})

    def payload(self, previous='initial'):
        return {'action':'retry-local','target':'ngs-local','sha':A,'expectedPreviousAttemptId':previous}

    def post(self, port, payload, headers):
        code,_,raw=self.req(port,'/api/deployments',payload,headers)
        return code,json.loads(raw)

    def row(self, cycle):
        return self.target(cycle,'ngs-local')

    def test_real_owner_http_explicit_retry_rollback_stale_then_success_and_no_auto(self):
        cycle=self.fixture()
        self.fake_native('rolled-back','deployed')
        raw=(self.folder/'result.json').read_bytes()
        raw_prior=(self.folder/'prior.plist').read_bytes()
        raw_ready=(self.path/'ready.json').read_bytes()
        c=D.DeployControl(self.root,cycle=cycle)
        with started(S.server(0,deploy_control=c)) as port:
            sid,body=self.auth(port)
            auth={**sid,'X-OPS-Deploy-CSRF':body['csrf']}
            self.assertEqual(self.row(cycle)['retry'],{'sha':A,'previousAttemptId':'initial'})
            self.assertFalse(self.row(cycle)['canDeploy'])
            self.assertIsNone(cycle.sweep()['active'])
            self.assertEqual(self.post(port,{'action':'wake','target':'ngs-local'},auth)[0],503)
            self.assertEqual(self.post(port,{'action':'wake','target':'all'},auth)[0],503)
            self.assertEqual(self.post(port,self.payload(),sid)[0],403)
            self.assertEqual(self.post(port,self.payload(),{'X-OPS-Deploy-CSRF':body['csrf']})[0],401)
            self.assertEqual(self.post(port,self.payload(),{**auth,'Origin':'http://bad.invalid'})[0],403)
            self.assertEqual(self.post(port,{**self.payload(),'target':'ngs-space'},auth)[0],400)
            self.assertEqual(self.post(port,{**self.payload(),'sha':B},auth)[0],409)
            self.assertFalse((self.root/'native-invocations').exists())
            code,retry=self.post(port,self.payload(),auth)
            self.assertEqual(code,202)
            first=retry['retry']['attemptId']
            self.assertTrue(C.retry_id(first))
            self.assertEqual(self.post(port,self.payload(),auth)[0],409)
            self.drain(cycle)
            self.assertEqual(self.row(cycle)['failure']['rollback'],'restored')
            self.assertEqual(self.row(cycle)['retry']['previousAttemptId'],first)
            self.assertEqual(self.post(port,self.payload(),auth)[0],409)
            code,retry=self.post(port,self.payload(first),auth)
            self.assertEqual(code,202)
            second=retry['retry']['attemptId']
            self.assertNotEqual(first,second)
            self.drain(cycle)
            self.assertEqual(self.row(cycle)['state'],'current')
            self.assertNotIn('retry',self.row(cycle))
            self.assertEqual(self.post(port,self.payload(first),auth)[0],409)
        self.assertEqual(json.loads((self.path/'manual-retries'/f'{first}.json').read_bytes())['previousResult'],self.original_result)
        self.assertEqual(json.loads((self.path/'manual-retries'/f'{second}.json').read_bytes())['previousResult']['attemptId'],first)
        self.assertEqual([json.loads(line) for line in (self.root/'native-invocations').read_text().splitlines()],
                         [[A,first,'initial'],[A,second,first]])
        self.assertEqual((self.folder/'result.json').read_bytes(),raw)
        self.assertEqual((self.folder/'prior.plist').read_bytes(),raw_prior)
        self.assertEqual((self.path/'ready.json').read_bytes(),raw_ready)
        self.assertEqual(C.optional(self.folder/'attempts'/first/'result.json')['state'],'rolled-back')
        self.assertEqual(C.optional(self.folder/'attempts'/second/'result.json')['state'],'deployed')

    def test_manifest_pins_in_doubt_native_prior_and_rollback_failure_block(self):
        cycle=self.fixture()
        self.fake_native('rollback-failed')
        pin=self.path/'retry-runner.json'
        original=pin.read_bytes()
        pin.unlink()
        self.assertNotIn('retry',self.row(cycle))
        private(pin,original)
        saved=self.executable.read_bytes()
        self.executable.write_bytes(saved+b'\n')
        self.assertNotIn('retry',self.row(cycle))
        self.executable.write_bytes(saved)
        private(self.folder/'prior.plist',b'drift')
        self.assertNotIn('retry',self.row(cycle))
        private(self.folder/'prior.plist',self.prior)
        (self.folder/'attempts').mkdir(mode=0o700)
        self.assertNotIn('retry',self.row(cycle))
        (self.folder/'attempts').rmdir()
        self.assertIn('retry',self.row(cycle))
        cycle.retry_local(A,'initial');self.drain(cycle)
        self.assertEqual(self.row(cycle)['failure']['rollback'],'failed')
        self.assertNotIn('retry',self.row(cycle))

    def test_native_pre_cutover_refusal_does_not_reuse_prior_or_offer_next_attempt(self):
        cycle=self.fixture()
        self.fake_native(None)
        accepted=cycle.retry_local(A,'initial')
        self.drain(cycle)
        row=self.row(cycle)
        self.assertEqual(row['failure'],{'phase':'check_current','code':'MAC_RETRY_PRIOR_DRIFT','rollback':'not_attempted'})
        self.assertNotIn('retry',row)
        self.assertFalse((self.folder/'attempts').exists())
        self.assertEqual(C.optional(self.path/'last-result.json')['attemptId'],accepted['retry']['attemptId'])
        with self.assertRaises(D.Rejected):cycle.retry_local(A,'initial')


if __name__=='__main__':
    unittest.main()
