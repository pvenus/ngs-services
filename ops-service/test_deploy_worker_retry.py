"""Isolated authenticated image retry and preserved first attempt."""
import hashlib
import json
import plistlib
import threading
import unittest

import deploy_control as D
import deploy_cycle as C
import server as S
import test_deploy_cycle as F
import test_deploy_control as T
from test_deploy_cycle import A,B,HASH,STAMP
from test_server import started


class ImageRetry(unittest.TestCase):
    setUp=F.Cycle.setUp
    stage=F.Cycle.stage
    drain=F.Cycle.drain
    target=F.Cycle.target
    req=T.DeployTests.req
    auth=T.DeployTests.auth

    def fixture(self,runner=None):
        self.folder=self.stage('worker-image',worker_proof=False)
        old=C.optional(self.folder/'ready.json')
        C.persist(self.folder/'ready.json',{**old,'manualOnly':True})
        self.selector=self.root/'Library/LaunchAgents/com.vinus.workflow.image-worker.plist'
        self.selector.parent.mkdir(mode=0o700,parents=True)
        T.private(self.selector,plistlib.dumps({'Label':'com.vinus.workflow.image-worker'}))
        self.last={'schemaVersion':C.TERMINAL,'target':'worker-image','attemptedSha':A,'state':'failed',
                   'finishedAt':STAMP,'observed':{'sha':B,'deployedAt':STAMP},
                   'failure':{'phase':'health','code':'TARGET_HEALTH_TIMEOUT','rollback':'failed'}}
        C.persist(self.folder/'last-result.json',self.last)
        C.persist(self.folder/'candidate.json',{'schemaVersion':'ops.mac-target-candidate.v1','target':'worker-image',
           'sha':A,'archiveSha256':HASH,'treeSha256':'d'*64,'expectedPriorPlistSha256':
                    hashlib.sha256(self.selector.read_bytes()).hexdigest()})
        self.offer={'schemaVersion':C.IMAGE_RETRY_READY,'target':'worker-image','sha':A,
                    'failureSha256':self.file_sha('last-result.json'),'candidateSha256':self.file_sha('candidate.json'),
                    'runnerSha256':old['runnerSha256']}
        C.persist(self.folder/'retry-ready.json',self.offer)
        self.body={'action':'retry-worker','target':'worker-image','sha':A,'offerSha256':self.file_sha('retry-ready.json')}
        self.preparation(A,'ready')
        self.healthy=True
        T.private(self.root/'owner-key',(T.KEY+'\n').encode())
        return C.DeploymentCycle(self.root,runner=runner or (lambda d,r:F.Cycle.receipt(self,d,r)),
             home=self.root,retry_probe=lambda *_:self.healthy)

    def preparation(self,sha,state):
        C.persist(self.root/'ops-candidate-preparation-status.json',
                  {'schemaVersion':C.PREPARATION,'checkedAt':C.now(),'targets':{'worker-image':{'state':state,'sha':sha}}})

    def file_sha(self,name):
        return hashlib.sha256((self.folder/name).read_bytes()).hexdigest()

    def test_explicit_http_only_history_and_double_click(self):
        entered,release=threading.Event(),threading.Event()
        self.addCleanup(release.set)
        def runner(d,r):
            entered.set()
            release.wait(5)
            return F.Cycle.receipt(self,d,r)
        cycle=self.fixture(runner)
        old=(self.folder/'last-result.json').read_bytes()
        row=self.target(cycle,'worker-image')
        self.assertEqual(row['state'],'failed')
        self.assertEqual(row['failure']['rollback'],'failed')
        self.assertTrue(row['canDeploy'])
        self.assertFalse(cycle.status()['canDeployAll'])
        cycle.sweep();cycle.trigger('all')
        with self.assertRaises(D.Rejected):cycle.trigger('worker-image')
        self.assertEqual(self.hits,[])
        with started(S.server(0,deploy_control=D.DeployControl(self.root,cycle=cycle))) as port:
            cookies,status=self.auth(port)
            auth={**cookies,'X-OPS-Deploy-CSRF':status['csrf']}
            self.assertEqual(self.req(port,'/api/deployments',self.body,cookies)[0],403)
            self.assertEqual(self.req(port,'/api/deployments',{**self.body,'target':'worker-audio'},auth)[0],400)
            self.assertEqual(self.req(port,'/api/deployments',{**self.body,'offerSha256':'f'*64},auth)[0],409)
            code,_,raw=self.req(port,'/api/deployments',self.body,auth)
            self.assertEqual(code,202)
            admission=json.loads(raw)['workerRetry']
            attempt=admission['attemptId']
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.req(port,'/api/deployments',self.body,auth)[0],409)
            self.assertEqual((self.folder/'last-result.json').read_bytes(),old)
            self.assertEqual((self.folder/'manual-retries'/f'{attempt}.previous.json').read_bytes(),old)
            release.set();self.drain(cycle)
            self.assertEqual(self.req(port,'/api/deployments',self.body,auth)[0],409)
        self.assertEqual(self.hits,[('worker-image',A)])
        self.assertEqual(self.target(cycle,'worker-image')['state'],'current')
        self.assertEqual(C.optional(self.folder/'last-result.json')['attemptId'],attempt)

    def test_health_selector_runner_failure_drift_and_orphan_journal_block(self):
        cycle=self.fixture()
        def blocked():
            with self.assertRaises(D.Rejected):
                cycle.retry_worker('worker-image',A,self.body['offerSha256'])
        self.healthy=False
        blocked()
        self.healthy=True
        self.preparation(B,'ready')
        blocked()
        self.preparation(A,'blocked')
        blocked()
        self.preparation(A,'ready')
        C.persist(self.root/'active.json',{'schemaVersion':C.ACTIVE,'selection':'worker-image',
            'targets':['worker-image'],'startedAt':STAMP})
        blocked()
        (self.root/'active.json').unlink()
        selector=self.selector.read_bytes()
        T.private(self.selector,selector+b'\n')
        blocked()
        T.private(self.selector,selector)
        runner=self.folder/'run-once'
        source=runner.read_bytes()
        runner.write_bytes(source+b'\n')
        blocked()
        runner.write_bytes(source)
        C.persist(self.folder/'last-result.json',{**self.last,'finishedAt':'2026-11-22T01:00:00Z'})
        blocked()
        self.assertEqual(self.hits,[])
        self.assertFalse((self.folder/'manual-retries').exists())
