import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

import deploy_cycle as C
from deploy_control import Rejected


A='a'*40
B='b'*40
HASH='c'*64
STAMP='2026-11-22T00:00:00Z'
SOURCE=b'#!/bin/sh\nexit 1\n'


class Cycle(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve()
        (self.root/'targets').mkdir(mode=0o700)
        self.hits=[]
    def stage(self,id,sha=A,source=SOURCE):
        t=self.root/'targets'/id
        t.mkdir(mode=0o700,exist_ok=True)
        path=t/'run-once';path.write_bytes(source);path.chmod(0o700)
        C.persist(t/'ready.json',{'schemaVersion':C.READY,'target':id,'sha':sha,
         'runnerSha256':hashlib.sha256(source).hexdigest(),'artifactSha256':HASH,'verifiedAt':STAMP})
        return t
    def receipt(self,directory,record):
        self.hits.append((directory.name,record['sha']))
        result={'schemaVersion':C.RECEIPT,'target':directory.name,'sha':record['sha'],'deployedAt':STAMP,
                'state':'deployed','health':'verified'}
        if directory.name=='ngs-space':result.update(providerSha=B,deploymentId='native.published.1')
        return result
    def drain(self,cycle):
        for _ in range(100):
            if cycle.status()['active'] is None:return cycle.status()
            time.sleep(.01)
        self.fail('worker did not finish')
    def target(self,cycle,id):
        return next(r for r in cycle.status()['targets'] if r['id']==id)
    def test_exact_inventory_and_all_preflight_never_partial_if_one_missing(self):
        c=C.DeploymentCycle(self.root,runner=self.receipt)
        self.assertEqual({r['id'] for r in c.status()['targets']},C.IDS)
        self.assertEqual(len(C.IDS),12)
        self.assertNotIn('ops',C.IDS)
        for id in C.IDS-{'ngs-space'}:self.stage(id)
        self.assertFalse(c.status()['allReady'])
        with self.assertRaises(Rejected) as r:c.trigger('all')
        self.assertEqual(r.exception.status,503)
        self.assertEqual(self.hits,[])
        with self.assertRaises(Rejected):c.trigger('ops')
        self.stage('ngs-space')
        self.assertTrue(c.status()['allReady'])
        c.trigger('all')
        self.drain(c)
        self.assertTrue(all(r['state']=='current' for r in c.status()['targets']))
        self.assertEqual([key for key,_ in self.hits],c._selection('all'))
        self.assertEqual(c.status()['comparison'],'match') # provider SHA is independent
        c.trigger('all');c.sweep()
        self.assertEqual(len(self.hits),12)
    def test_failed_candidate_does_not_repeat_and_honors_new_verified_ref(self):
        id='worker-document';folder=self.stage(id)
        def fail(d,r):
            self.hits.append((d.name,r['sha']))
            return {'sha':B}
        c=C.DeploymentCycle(self.root,runner=fail)
        c.sweep();self.drain(c)
        self.assertEqual(self.target(c,id)['state'],'failed')
        self.assertIsNone(self.target(c,id)['observed'])
        self.assertEqual(c.sweep()['active'],None)
        with self.assertRaises(Rejected):c.trigger(id)
        self.assertEqual(self.hits,[(id,A)])
        c.runner=self.receipt
        self.stage(id,B)
        self.assertTrue(self.target(c,id)['canDeploy'])
        c.trigger(id);self.drain(c)
        self.assertEqual(self.target(c,id)['observed']['sha'],B)
        self.assertEqual(json.loads((folder/'last-result.json').read_bytes())['attemptedSha'],B)
    def test_fixed_real_runner_has_no_argv_and_exact_verified_env(self):
        id='ngs-local'
        encoded=(f'#!/bin/sh\n'
            'test "$#" -eq 0 || exit 1\n'
            f'test "$OPS_DEPLOY_ARTIFACT_SHA256" = "{HASH}" || exit 2\n'
            f'test "$OPS_DEPLOY_CANDIDATE_SHA" = "{A}" || exit 3\n'
            f'printf \'{{"schemaVersion":"{C.RECEIPT}","target":"{id}","sha":"{A}",'
            f'"deployedAt":"{STAMP}","state":"deployed","health":"verified"}}\\n\'\n').encode()
        self.stage(id,source=encoded)
        c=C.DeploymentCycle(self.root)
        c.trigger(id);self.drain(c)
        self.assertEqual(self.target(c,id)['state'],'current')
        self.assertEqual(self.target(c,id)['observed']['deployedAt'],STAMP)
    def test_all_drift_preflight_blocks_and_private_receipts_not_publicly_written(self):
        c=C.DeploymentCycle(self.root,runner=self.receipt)
        for id in C.IDS:self.stage(id)
        t=self.root/'targets'/'ngs-space'
        (t/'run-once').write_bytes(b'unexpected')
        self.assertFalse(c.status()['allReady'])
        self.assertEqual(self.target(c,'ngs-space')['state'],'not_configured')
        with self.assertRaises(Rejected):c.trigger('all')
        self.assertEqual(self.hits,[])
        C.persist(t/'observed.json',{'sha':A,'deploymentId':'native.one','providerSha':B,'deployedAt':STAMP})
        C.persist(self.root/'targets'/'ngs-local'/'observed.json',{'sha':B,'deployedAt':STAMP})
        self.assertEqual(c.status()['comparison'],'mismatch')
        self.assertIsNone(self.target(c,'ngs-space')['candidateSha'])
    def test_running_job_exclusive_and_orphan_blocks_new_targets(self):
        d='worker-file';p='workflow-planner'
        self.stage(d);self.stage(p)
        entered,end=threading.Event(),threading.Event()
        self.addCleanup(end.set)
        def runner(directory,record):
            entered.set();end.wait(3);return self.receipt(directory,record)
        c=C.DeploymentCycle(self.root,runner=runner)
        c.trigger(d);self.assertTrue(entered.wait(1))
        self.assertEqual(c.status()['active'],{'selection':d,'targets':[d]})
        other=C.DeploymentCycle(self.root,runner=self.receipt)
        with self.assertRaises(Rejected) as r:other.trigger(p)
        self.assertEqual(r.exception.status,409)
        end.set();self.drain(c)
        C.persist(self.root/'active.json',{'schemaVersion':C.ACTIVE,'selection':p,'targets':[p],'startedAt':STAMP})
        self.assertEqual(self.target(c,p)['state'],'unknown')
        self.assertFalse(c.status()['allReady'])
        with self.assertRaises(Rejected):c.trigger(p)
        self.assertEqual(self.hits,[(d,A)])
    def test_auto_and_manual_share_eligibility_without_cross_target_fallback(self):
        only='request-service'
        self.stage(only)
        c=C.DeploymentCycle(self.root,runner=self.receipt)
        self.assertFalse(c.status()['allReady'])
        with self.assertRaises(Rejected):c.trigger('services')
        c.sweep();self.drain(c)
        self.assertEqual(self.hits,[(only,A)])
        c.sweep();c.trigger(only)
        self.assertEqual(self.hits,[(only,A)])
