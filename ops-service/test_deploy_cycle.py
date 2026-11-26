import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

import deploy_cycle as C
from release_equivalence import SELECTORS
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
    def stage(self,id,sha=A,source=SOURCE,*,worker_proof=True):
        t=self.root/'targets'/id
        t.mkdir(mode=0o700,exist_ok=True)
        if id=='ngs-space':
            C.persist(t/'publication.json',{'schemaVersion':C.SPACE_PUBLICATION_SCHEMA,
             'provider':'chatgpt-sites','projectId':C.SPACE_SITE,'hostname':C.SPACE_HOST,'accessMode':'custom'})
        path=t/'run-once';path.write_bytes(source);path.chmod(0o700)
        runner=hashlib.sha256(source).hexdigest()
        ready={'schemaVersion':C.READY,'target':id,'sha':sha,
               'runnerSha256':runner,'artifactSha256':HASH,'verifiedAt':STAMP}
        if id in C.WORKER_IDS and worker_proof:
            old='e'*40
            ready['manualOnly']=True
            role=id.removeprefix('worker-')
            selector=self.root/'Library/LaunchAgents'/(SELECTORS[id]+'.plist')
            selector.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            C.persist(selector,{'fixtureSelector':id})
            pin=hashlib.sha256(selector.read_bytes()).hexdigest()
            C.persist(t/'observed.json',{'sha':old,'deployedAt':STAMP})
            C.persist(t/'candidate.json',{'target':id,'sha':sha,'archiveSha256':HASH,'expectedPriorPlistSha256':pin})
            C.persist(t/'worker-source-decision.json',{'schemaVersion':C.WORKER_DECISION,'target':id,'sha':sha,
             'artifactSha256':HASH,'runnerSha256':runner,'selectorSha256':pin,'observedSha':old,
             'activeTreeSha256':'0'*64,'candidateTreeSha256':'1'*64,'state':'changed',
             'manifestSha256':hashlib.sha256((t/'candidate.json').read_bytes()).hexdigest()})
            path=self.root/'ops-candidate-preparation-status.json'
            prior=C.optional(path) or {'targets':{}}
            C.persist(path,{'schemaVersion':C.PREPARATION,'checkedAt':C.now(),
                       'targets':{**prior['targets'],id:{'state':'ready','sha':sha}}})
        C.persist(t/'ready.json',ready)
        return t

    def cycle(self,**kwargs):
        return C.DeploymentCycle(self.root,home=self.root,worker_probe=lambda *_:True,**kwargs)
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
    def test_bulk_uses_only_ready_rows_and_does_not_include_space(self):
        c=self.cycle(runner=self.receipt)
        self.assertEqual({r['id'] for r in c.status()['targets']},C.IDS)
        self.assertEqual(len(C.IDS),17)
        self.assertNotIn('ops',C.IDS)
        target='worker-document'
        self.stage(target)
        self.assertFalse(c.status()['allReady'])
        self.assertTrue(c.status()['canDeployAll'])
        c.trigger('all');self.drain(c)
        self.assertEqual(self.hits,[(target,A)])
        self.assertFalse(c.status()['canDeployAll'])
        with self.assertRaises(Rejected):c.trigger('ops')
        self.stage('ngs-space')
        self.assertTrue(self.target(c,'ngs-space')['canDeploy'])
        self.assertFalse(c.status()['canDeployAll'])
        c.trigger('all');self.drain(c)
        self.assertEqual(self.hits,[(target,A)])
        for id in C.IDS-{'ngs-space',target}:self.stage(id)
        c.trigger('all');self.drain(c)
        self.assertEqual(set(key for key,_ in self.hits),C.IDS-{'ngs-space'})
        self.assertFalse(c.status()['canDeployAll'])
    def test_failed_candidate_does_not_repeat_and_honors_new_verified_ref(self):
        id='worker-document';folder=self.stage(id)
        def fail(d,r):
            self.hits.append((d.name,r['sha']))
            return {'sha':B}
        c=self.cycle(runner=fail)
        c.trigger(id);self.drain(c)
        self.assertEqual(self.target(c,id)['state'],'failed')
        self.assertEqual(self.target(c,id)['observed']['sha'],'e'*40)
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
        c=self.cycle()
        c.trigger(id);self.drain(c)
        self.assertEqual(self.target(c,id)['state'],'current')
        self.assertEqual(self.target(c,id)['observed']['deployedAt'],STAMP)
    def test_all_drift_preflight_blocks_and_private_receipts_not_publicly_written(self):
        c=self.cycle(runner=self.receipt)
        for id in C.IDS:self.stage(id)
        t=self.root/'targets'/'ngs-space'
        (t/'run-once').write_bytes(b'unexpected')
        self.assertFalse(c.status()['allReady'])
        self.assertEqual(self.target(c,'ngs-space')['state'],'not_configured')
        self.assertTrue(c.status()['canDeployAll'])
        c.trigger('all');self.drain(c)
        self.assertNotIn('ngs-space',{key for key,_ in self.hits})
        C.persist(t/'observed.json',{'sha':A,'deploymentId':'native.one','providerSha':B,'deployedAt':STAMP})
        C.persist(self.root/'targets'/'ngs-local'/'observed.json',{'sha':B,'deployedAt':'2026-11-22T23:59:59Z'})
        self.assertEqual(c.status()['comparison'],'mismatch')
        self.assertIsNone(self.target(c,'ngs-space')['candidateSha'])
    def test_running_job_exclusive_and_orphan_blocks_new_targets(self):
        d='worker-file';p='workflow-planner'
        self.stage(d);self.stage(p)
        entered,end=threading.Event(),threading.Event()
        self.addCleanup(end.set)
        def runner(directory,record):
            entered.set();end.wait(3);return self.receipt(directory,record)
        c=self.cycle(runner=runner)
        c.trigger(d);self.assertTrue(entered.wait(1))
        self.assertEqual(c.status()['active'],{'selection':d,'targets':[d]})
        other=self.cycle(runner=self.receipt)
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
        c=self.cycle(runner=self.receipt)
        self.assertFalse(c.status()['allReady'])
        with self.assertRaises(Rejected):c.trigger('services')
        c.sweep();self.drain(c)
        self.assertEqual(self.hits,[(only,A)])
        c.sweep();c.trigger(only)
        self.assertEqual(self.hits,[(only,A)])
    def test_auto_registered_candidate_is_actionable_only_from_manual_cycle(self):
        target='worker-audio'
        folder=self.stage(target)
        record=json.loads((folder/'ready.json').read_bytes())
        C.persist(folder/'ready.json',{**record,'manualOnly':True})
        c=self.cycle(runner=self.receipt)
        self.assertTrue(self.target(c,target)['manualOnly'])
        self.assertTrue(self.target(c,target)['canDeploy'])
        self.assertIsNone(c.sweep()['active'])
        self.assertIsNone(c.trigger(target,automatic=True)['active']) # re-check after sweep
        self.assertEqual(self.hits,[])
        c.trigger(target);self.drain(c)
        self.assertEqual(self.hits,[(target,A)])
        C.persist(folder/'ready.json',{**record,'sha':B,'manualOnly':False})
        self.assertFalse(self.target(c,target)['canDeploy'])

    def test_preparation_exposes_only_bounded_status_and_adds_no_deploy_authority(self):
        c=self.cycle(runner=self.receipt)
        file=self.root/'ops-candidate-preparation-status.json'
        data={'schemaVersion':C.PREPARATION,'checkedAt':STAMP,'targets':{
            'worker-audio':{'state':'pending','sha':A},
            'filex-writer':{'state':'not_configured','code':'FILEX_ADAPTER_REQUIRED'}}}
        C.persist(file,data)
        self.assertEqual(c.status()['preparation'],{'checkedAt':STAMP,'targets':data['targets']})
        self.assertFalse(self.target(c,'worker-audio')['canDeploy'])
        self.assertIn('filex-writer',C.IDS)
        self.assertFalse(self.target(c,'filex-writer')['canDeploy'])
        with self.assertRaises(Rejected):c.trigger('filex-writer')
        for invalid in ({**data,'token':'never return token'},
                        {**data,'targets':{'worker-audio':{'state':'blocked','code':'/Users/private'}}},
                        {**data,'targets':{'intruder':{'state':'ready'}}}):
            C.persist(file,invalid)
            self.assertNotIn('preparation',c.status())
        self.assertEqual(self.hits,[])

    def test_fresh_blocked_service_prep_is_not_old_current_or_actionable(self):
        c=self.cycle(runner=self.receipt)
        for target in ('request-service','workflow-planner'):
            folder=self.stage(target)
            C.persist(folder/'observed.json',{'sha':A,'deployedAt':STAMP})
            self.assertEqual(self.target(c,target)['state'],'current')
            status=self.root/'ops-candidate-preparation-status.json'
            def prep(state, revision, **kw):
                C.persist(status,{'schemaVersion':C.PREPARATION,'checkedAt':C.now(),
                                  'targets':{target:{'state':state,'sha':revision,**kw}}})
            prep('blocked',B,code='REQUEST_SELECTOR_STORAGE_DRIFT')
            row=self.target(c,target)
            self.assertEqual((row['state'],row['candidateSha'],row['observed']['sha']),('blocked',B,A))
            self.assertFalse(row['canDeploy'])
            with self.assertRaises(Rejected):c.trigger(target)
            ready=C.optional(folder/'ready.json')
            C.persist(folder/'ready.json',{**ready,'sha':B,'manualOnly':True})
            self.assertFalse(self.target(c,target)['canDeploy'])
            prep('ready',A)
            self.assertEqual(self.target(c,target)['state'],'pending')
            prep('ready',B)
            self.assertTrue(self.target(c,target)['canDeploy'])
            # The click can race a newly blocked preparation; the runner must revalidate.
            prep('failed',B,code='PREP_FAILED')
            c.trigger('all')
            self.assertFalse(self.target(c,target)['canDeploy'])
            self.assertEqual(self.hits,[])
            status.unlink()
            self.assertFalse(self.target(c,target)['canDeploy'])

    def test_failure_receipt_preserves_only_exact_phase_code_and_rollback(self):
        data={'phase':'start','code':'TARGET_BOOTSTRAP_FAILED','rollback':'restored'}
        raw={'schemaVersion':C.ERROR,'target':'rs-mcp','sha':A,**data}
        source=("#!/bin/sh\nprintf '%s\\n' '"+json.dumps(raw)+"'\nexit 1\n").encode()
        t=self.stage('rs-mcp',source=source)
        c=self.cycle()
        c.trigger('rs-mcp');self.drain(c)
        self.assertEqual(self.target(c,'rs-mcp')['failure'],data)
        self.assertEqual(json.loads((t/'last-result.json').read_bytes())['failure'],data)
        self.assertFalse(self.target(c,'rs-mcp')['canDeploy'])
        with self.assertRaisesRegex(ValueError,'OPS_TARGET_ERROR_INVALID'):
            C.safe_failure({**data,'code':'/private/credentials'})
    def test_space_is_a_fixed_site_not_a_local_path_or_implicit_permission(self):
        c=self.cycle(runner=self.receipt)
        target=self.stage('ngs-space')
        config=target/'publication.json'
        row=self.target(c,'ngs-space')
        self.assertEqual(row['publication'],{'provider':'chatgpt-sites','hostname':C.SPACE_HOST})
        self.assertNotIn(C.SPACE_SITE,json.dumps(row))
        config.unlink()
        self.assertEqual(self.target(c,'ngs-space')['state'],'not_configured')
        with self.assertRaises(Rejected):c.trigger('ngs-space')
        C.persist(config,{'provider':'chatgpt-sites','projectId':'another','hostname':C.SPACE_HOST})
        self.assertFalse(self.target(c,'ngs-space')['canDeploy'])
        self.assertEqual(self.hits,[])

    def test_local_natively_failed_receipt_reaches_api_result_and_remains_blocked(self):
        failure={'phase':'check_candidate','code':'NATIVE_RELEASE_SHA_DRIFT','rollback':'failed',
                 'rollbackFailure':{'stage':'rollback_check','code':'ROLLBACK_STORAGE_IDENTITY_DRIFT'}}
        raw={'schemaVersion':C.ERROR,'target':'ngs-local','sha':A,**failure}
        source=("#!/bin/sh\nprintf '%s\\n' '"+json.dumps(raw)+"'\nexit 1\n").encode()
        path=self.stage('ngs-local',source=source)
        C.persist(path/'observed.json',{'sha':B,'deployedAt':STAMP})
        cycle=self.cycle()
        cycle.trigger('ngs-local');self.drain(cycle)
        result=self.target(cycle,'ngs-local')
        self.assertEqual(result['failure'],failure)
        self.assertEqual(result['observed'],{'sha':B,'deployedAt':STAMP})
        self.assertEqual(json.loads((path/'last-result.json').read_bytes())['failure'],failure)
        self.assertFalse(result['canDeploy'])
        with self.assertRaises(Rejected):cycle.trigger('ngs-local')
        with self.assertRaises(ValueError):C.safe_failure(failure,'rs-mcp')
        for bad in ({**failure,'code':'NATIVE_TOKEN_SECRET'}, {**failure,'phase':'/owner/home'},
                    {**failure,'rollback':'restored'},
                    {**failure,'rollbackFailure':{'stage':'rollback_check','code':'/private/secret'}}):
            with self.assertRaises(ValueError):C.safe_failure(bad,'ngs-local')
        (path/'run-once').write_bytes(b'new adapter staged, still failed')
        self.assertEqual(self.target(cycle,'ngs-local')['state'],'failed')
        self.assertFalse(self.target(cycle,'ngs-local')['canDeploy'])

    def test_read_only_old_exact_rollback_history_without_inventing_cause(self):
        path=self.stage('ngs-local')
        C.persist(path/'last-result.json',{'schemaVersion':C.TERMINAL,'target':'ngs-local',
         'attemptedSha':A,'state':'failed','finishedAt':STAMP,'observed':{'sha':B,'deployedAt':STAMP}})
        (self.root/'mac-releases'/A).mkdir(mode=0o700,parents=True)
        (self.root/'mac-releases').chmod(0o700)
        result_file=self.root/'mac-releases'/A/'result.json'
        C.persist(result_file,{'state':'rolled-back'})
        saved=(path/'last-result.json').read_bytes(),result_file.read_bytes()
        cycle=self.cycle(runner=self.receipt)
        row=self.target(cycle,'ngs-local')
        self.assertEqual(row['failure'],{'phase':'unknown','code':'NGS_LOCAL_CAUSE_NOT_RECORDED','rollback':'restored'})
        self.assertFalse(row['canDeploy'])
        self.assertEqual(((path/'last-result.json').read_bytes(),result_file.read_bytes()),saved)
        C.persist(result_file,{'state':'rolled-back','raw':'not trusted'})
        self.assertNotIn('failure',self.target(cycle,'ngs-local'))

    def test_worker_delta_requires_recent_exact_supervisor_and_live_click_proof(self):
        target='worker-document'
        row=self.stage(target)
        c=self.cycle(runner=self.receipt)
        self.assertTrue(self.target(c,target)['canDeploy'])
        status=self.root/'ops-candidate-preparation-status.json'
        saved=C.optional(status)
        for state in ('blocked','current'):
            C.persist(status,{**saved,'targets':{target:{'state':state,'sha':A}}})
            self.assertFalse(self.target(c,target)['canDeploy'])
            with self.assertRaises(Rejected):c.trigger(target)
        C.persist(status,{**saved,'targets':{target:{'state':'ready','sha':B}}})
        self.assertFalse(self.target(c,target)['canDeploy'])
        C.persist(status,saved)
        c.worker_probe=lambda *_:False
        with self.assertRaises(Rejected):c.trigger(target)
        self.assertEqual(self.hits,[])
        (row/'worker-source-decision.json').unlink()
        self.assertFalse(self.target(c,target)['canDeploy'])
        with self.assertRaises(Rejected):c.trigger(target)

    def test_worker_equivalent_makes_old_ready_non_actionable_for_manual_and_all(self):
        target='worker-animation'
        row=self.stage(target)
        c=self.cycle(runner=self.receipt)
        d=C.optional(row/'worker-source-decision.json')
        d={**d,'state':'equivalent','candidateTreeSha256':d['activeTreeSha256']}
        del d['manifestSha256']
        eq={k:d[k] for k in ('target','sha','observedSha','artifactSha256','runnerSha256','selectorSha256')}
        C.persist(row/'equivalent.json',{**eq,'schemaVersion':'ops.deploy-target-equivalent.v1',
                   'runtimeTreeSha256':d['activeTreeSha256'],'verifiedAt':STAMP})
        C.persist(row/'worker-source-decision.json',d)
        self.assertEqual(self.target(c,target)['state'],'current')
        self.assertFalse(self.target(c,target)['canDeploy'])
        c.trigger(target);c.trigger('all');c.sweep()
        self.assertEqual(self.hits,[])
