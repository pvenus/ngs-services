"""Offline Git evidence and six-role registration; no worker or deploy runner starts."""
import fcntl
import io
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import audio_cutover as audio
import prepare_service_targets as source
import prepare_worker_candidates as workers
from prepare_ngs_release import atomic_write, digest, json_bytes, json_load


def write(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o600)
    return path


def dir_pin(path):
    st=path.stat()
    return {'device':st.st_dev,'inode':st.st_ino}


class WorkerCandidates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp=tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.repo=Path(temp.name).resolve()
        def git(*args):
            return subprocess.check_output(['/usr/bin/git','-C',str(cls.repo),*args],stderr=subprocess.DEVNULL)
        git('init','-q')
        for root_set, entries in source.SPECS[source.WORKERS].values():
            for root in root_set:
                write(cls.repo/root/'README.md',b'test')
            for entry in entries:
                write(cls.repo/entry,b'// do not run this fixture\n')
        paths={'native-wan/workers/animation/__init__.py','native-postprocess/workers/postprocess/__init__.py',
               'native-image/workers/image/__init__.py','candidate-audio-resident/adapters/load.mjs'}
        paths.update(f'{audio.NATIVE}/{name}' for name in audio.CODE)
        for path in paths:
            write(cls.repo/'workflow-production'/path,('fixture '+path).encode())
        def commit(label,*options):
            git('add','workflow-production')
            git('-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-qm',label,*options)
            return git('rev-parse','HEAD').decode().strip()
        cls.prior_sha=commit('old worker contents')
        for _, entries in source.SPECS[source.WORKERS].values():
            for entry in entries:
                write(cls.repo/entry,b'// changed worker candidate fixture\n')
        cls.head=commit('new worker contents')
        cls.same_tree_sha=commit('equivalent worker contents','--allow-empty')
        targets={}
        for target,(roots,entries) in source.SPECS[source.WORKERS].items():
            trees={key:git('rev-parse','HEAD:'+key).decode().strip() for key in roots}
            counts=dict.fromkeys(source.SOURCE.values(),0)
            for name in git('ls-tree','-r','--name-only','HEAD','--',*roots).decode().splitlines():
                ext=Path(name).suffix
                if ext in source.SOURCE: counts[source.SOURCE[ext]]+=1
            targets[target]={'roots':trees,'checked':counts,'entrypoints':list(entries),
                 'sourceFingerprint':digest(json_bytes(trees).rstrip(b'\n'))}
        proof={'schemaVersion':'ops.target-source-readiness.v1','repository':source.WORKERS,
               'headSha':cls.head,'ref':source.REF,'runId':'123','runAttempt':'1','targets':targets}
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as zip_:zip_.writestr('release.json',json_bytes(proof))
        raw=buf.getvalue()
        workflow={'id':7,'path':'.github/workflows/prepare-worker-targets.yml'}
        identity={'full_name':source.WORKERS,'id':321}
        run={'id':123,'run_attempt':1,'head_sha':cls.head,'head_branch':'main','event':'push','status':'completed',
             'conclusion':'success','path':workflow['path'],'workflow_id':7,'repository':identity,'head_repository':identity}
        artifact={'id':125,'name':f'worker-targets-{cls.head}-1','expired':False,'size_in_bytes':len(raw),
                 'digest':'sha256:'+digest(raw),'workflow_run':{'id':123,'head_sha':cls.head,'head_branch':'main',
                                                              'repository_id':321,'head_repository_id':321}}
        cls.evidence={'head':cls.head,'workflow':workflow,'run':run,'artifact':artifact,'raw':raw}

    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home=Path(temp.name).resolve()
        self.control=self.home/'control'
        self.targets=self.control/'targets'
        self.targets.mkdir(mode=0o700,parents=True)
        self.control.chmod(0o700)
        root=self.home/'Workspaces/Workers'
        self.releases=root/'code/releases'
        self.releases.mkdir(mode=0o700,parents=True)
        self.prior=self.releases/('workflows-github-'+self.prior_sha[:7])
        self.install_source(self.prior,self.prior_sha)
        self.runtime=root/'runtime'
        self.runtime.mkdir(mode=0o700)
        self.selector_paths={}
        self.old_bindings={}
        self.blocked=set()
        self.calls=[]
        exe=write(self.home/'bin/node',b'fixture must not execute');exe.chmod(0o700)
        self.image_python=write(self.home/'bin/python3.12',b'fixture must not execute')
        self.image_python.chmod(0o700)
        accessible=workers._accessible
        def fixture_accessible(value, **options):
            # The runtime intentionally pins this Mac path. Exercise the same
            # real filesystem/X_OK guards on a fixture on every CI platform.
            if value=='/opt/homebrew/bin/python3.12':
                value=str(self.image_python)
            return accessible(value, **options)
        self.enterContext(patch.object(workers,'_accessible',side_effect=fixture_accessible))
        db=write(self.runtime/'workflow.sqlite',b'not opened')
        native=self.releases/('workflow-native-audio-resident-github-'+self.prior_sha[:7])
        def depth(path):return len(path.parts)
        for old in sorted((self.prior/audio.NATIVE).rglob('*'),key=depth):
            dest=native/old.relative_to(self.prior/audio.NATIVE)
            if old.is_dir():
                dest.mkdir(mode=0o700,parents=True,exist_ok=True)
                dest.chmod(0o700)
            elif old.is_file():
                write(dest,old.read_bytes())
        native.chmod(0o700)
        runtime_root=self.runtime/'audio-runtime'
        artifact_root=self.runtime/'audio-scratch'
        artifact_root.mkdir(mode=0o700)
        python=write(runtime_root/'source/.venv/bin/python',b'fixture')
        venv=write(python.parent.parent/'pyvenv.cfg',b'fixture')
        shared=write(self.runtime/'shared.lock',b'lock')
        legacy=write(self.runtime/'legacy.lock',b'lock')
        inner=write(self.runtime/'audio-inner.json',json_bytes({
            'schema':audio.INNER,'release_root':str(native),'runtime_root':str(runtime_root),
            'artifact_root':str(artifact_root),'runner_path':str(native/audio.RUNNER),'python_path':str(python),
            'shared_lock':{'path':str(shared),**dir_pin(shared)},'legacy_lock':{'path':str(legacy),**dir_pin(legacy)},
            'directory_pins':{'runtime_root':dir_pin(runtime_root),'artifact_root':dir_pin(artifact_root)},
            'file_pins':{str(f):audio._hash(f) for f in [*(native/p for p in audio.CODE),python,venv]},
        }))
        outer=write(self.runtime/'audio-outer.json',json_bytes({'schemaVersion':audio.OUTER,'hqBinding':str(inner)}))
        self.old_bindings={p:p.read_bytes() for p in (inner,outer)}
        self.trusted=Path(workers.__file__).parent
        for target in workers.TARGET_IDS:
            definition=workers.TARGETS[target]
            old=self.prior/definition.entry
            args=[str(exe),str(old),'--run','--db',str(db)]
            env={}
            if target=='worker-file': args=[str(exe),str(old),'--single-target','--resident','--db',str(db)]
            if target=='worker-animation':
                bind=write(self.runtime/'wan-binding.json',b'{}')
                env={'WORKFLOW_WAN_RELEASE':str(self.prior/'native-wan'),
                     'WORKFLOW_WAN_PYTHON':str(exe),'WORKFLOW_WAN_BINDING':str(bind)}
            if target=='worker-image':
                config=write(self.runtime/'image-config.json',b'{}')
                env={'WORKFLOW_IMAGE_HOST_CONFIG':str(config),'WORKFLOW_IMAGE_HOST_CONFIG_SHA256':digest(config.read_bytes())}
            if target=='worker-postprocess':
                sam=self.runtime/'sam'
                sam.mkdir(mode=0o700)
                args+=['--config',str(write(self.runtime/'pp-config.json',b'{}')),
                       '--model-pins',str(write(self.runtime/'pp-pins.json',b'{}')),
                       '--worker-source',str(self.prior/'native-postprocess'),'--sam-source',str(sam)]
            if target=='worker-audio':
                adapter=self.prior/'candidate-audio-resident/adapters/load.mjs'
                args+=['--adapter-module',str(adapter),'--adapter-config',str(outer)]
            p=self.home/'Library/LaunchAgents'/(definition.label+'.plist')
            self.selector_paths[target]=write(p,plistlib.dumps({'Label':definition.label,
                'ProgramArguments':args,'WorkingDirectory':str(old.parent),'EnvironmentVariables':env}))
            folder=self.targets/target
            folder.mkdir(mode=0o700)
            self.observed(target,self.prior_sha)
            write(folder/'run-once',(self.trusted/'mac_target_apply.py').read_bytes()).chmod(0o500)
            for module in workers.SUPPORT:
                write(folder/module,(self.trusted/module).read_bytes())

    def install_source(self,release,revision):
        paths=source.git(self.repo,'ls-tree','-r','--name-only',revision,'--','workflow-production').decode().splitlines()
        for path in paths:
            dst=release/Path(path).relative_to('workflow-production')
            for parent in reversed([x for x in dst.parents if x.is_relative_to(release)]):
                parent.mkdir(mode=0o700,exist_ok=True)
            write(dst,source.git(self.repo,'show',revision+':'+path))

    def observed(self,target,sha):
        atomic_write(self.targets/target/'observed.json',json_bytes({'sha':sha,'deployedAt':'2026-11-21T12:28:46Z'}))

    def loaded(self,target,pin):
        return pin==digest(self.selector_paths[target].read_bytes())

    def idle(self,target,pin):
        self.calls.append(target)
        return target not in self.blocked and pin==digest(self.selector_paths[target].read_bytes())

    def prepare(self,evidence=None,main=None,retry_probe=None):
        return workers.prepare_worker_candidates(self.evidence if evidence is None else evidence,
                repo=self.repo,home=self.home,control=self.control,
                read_main=main or (lambda:self.head), idle=self.idle, retry_probe=retry_probe,
                loaded=self.loaded,prior_repo=lambda _:self.repo)

    def test_all_six_real_artifact_bindings_publish_manual_only_and_rerun_is_noop(self):
        original={p:p.read_bytes() for p in self.selector_paths.values()}|self.old_bindings
        result=self.prepare()
        self.assertEqual(set(result['targets']),set(workers.TARGET_IDS))
        self.assertTrue(all(row=={'state':'ready'} for row in result['targets'].values()),result)
        snapshots={}
        for target in workers.TARGET_IDS:
            root=self.targets/target
            c,r=(json_load((root/n).read_bytes()) for n in ('candidate.json','ready.json'))
            self.assertEqual(c['expectedPriorPlistSha256'],digest(original[self.selector_paths[target]]))
            self.assertEqual(c['archiveSha256'],self.evidence['artifact']['digest'].removeprefix('sha256:'))
            self.assertEqual(c['archiveSha256'],r['artifactSha256'])
            self.assertTrue(r['manualOnly'])
            self.assertEqual('audioBindingSha256' in c,target=='worker-audio')
            snapshots[root/'ready.json']=(root/'ready.json').stat().st_ino
        self.assertTrue(all(r['state']=='already_ready' for r in self.prepare()['targets'].values()))
        self.assertEqual({p:p.stat().st_ino for p in snapshots},snapshots)
        self.assertEqual({p:p.read_bytes() for p in original},original)

    def test_active_failed_runner_absent_and_scope_deny_independently(self):
        self.blocked.add('worker-animation')
        image=self.targets/'worker-image'
        atomic_write(image/'last-result.json',json_bytes({'schemaVersion':workers.TERMINAL,
                        'attemptedSha':self.head,'state':'failed','target':'worker-image'}))
        (self.targets/'worker-audio/audio_cutover.py').unlink()
        rows=self.prepare()['targets']
        self.assertEqual(rows['worker-animation']['code'],'WORKER_IDLE_UNPROVEN')
        self.assertEqual(rows['worker-image']['code'],'WORKER_ATTEMPT_FAILED')
        self.assertEqual(rows['worker-audio']['state'],'blocked')
        for key in ('worker-image','worker-animation','worker-audio'):
            self.assertFalse((self.targets/key/'ready.json').exists())
        self.assertEqual(rows['worker-file']['state'],'ready')

    def test_digest_main_deploy_journal_and_existing_release_drift_never_register(self):
        with self.assertRaisesRegex(ValueError,'TARGET_ARTIFACT_MISMATCH'):
            self.prepare(evidence={**self.evidence,'raw':self.evidence['raw']+b'changed'})
        with self.assertRaisesRegex(ValueError,'WORKER_MAIN_ADVANCED'):
            sha=iter([self.head,'a'*40]);self.prepare(main=lambda:next(sha))
        self.assertEqual(list(self.targets.rglob('ready.json')),[])
        release=self.releases/('workflows-github-'+self.head)
        write(release/'extra.mjs',b'untracked')
        with self.assertRaisesRegex(ValueError,'WORKER_RELEASE_EXTRA_FILE'):self.prepare()
        self.assertEqual(list(self.targets.rglob('ready.json')),[])
        write(self.control/'active.json',b'untouched')
        with self.assertRaisesRegex(ValueError,'WORKER_DEPLOY_ACTIVE_OR_UNVERIFIED'):self.prepare()

    def test_existing_cycle_lock_blocks_publication(self):
        with (self.control/'cycle.lock').open('w') as f:
            (self.control/'cycle.lock').chmod(0o600)
            fcntl.flock(f,fcntl.LOCK_EX)
            with self.assertRaisesRegex(ValueError,'PREP_LOCK_BUSY'):self.prepare()
        self.assertEqual(list(self.targets.rglob('ready.json')),[])

    def test_same_sha_with_conflicting_artifact_preserves_existing_pointer(self):
        self.prepare()
        target=self.targets/'worker-file'
        ready=target/'ready.json'
        record=json_load(ready.read_bytes())
        atomic_write(ready,json_bytes({**record,'artifactSha256':'f'*64}))
        original=ready.read_bytes()
        self.assertEqual(self.prepare()['targets']['worker-file'],
                         {'state':'blocked','code':'WORKER_REGISTERED_CANDIDATE_DRIFT'})
        self.assertEqual(ready.read_bytes(),original)

    def test_image_requires_executable_pinned_interpreter(self):
        self.image_python.chmod(0o600)
        result=self.prepare()['targets']
        self.assertEqual(result['worker-image'],
                         {'state':'blocked','code':'WORKER_RUNTIME_DEPENDENCY_UNVERIFIED'})
        self.assertFalse((self.targets/'worker-image/ready.json').exists())
        self.assertEqual(result['worker-document'],{'state':'ready'})

    def test_failed_image_retry_only_rebinds_prior_selector_after_verified_recovery(self):
        self.prepare()
        target='worker-image'
        folder=self.targets/target
        prior=folder/'candidate.json'
        ready=folder/'ready.json'
        old_plan=json_load(prior.read_bytes())
        old_ready=ready.read_bytes()
        baseline=self.prior_sha
        last=folder/'last-result.json'
        atomic_write(last,json_bytes({'schemaVersion':workers.TERMINAL,'target':target,'attemptedSha':self.head,
             'state':'failed','finishedAt':'2026-11-22T16:08:26Z','observed':{'sha':baseline,
               'deployedAt':'2026-11-21T12:28:46Z'},'failure':{'phase':'health','code':'TARGET_HEALTH_TIMEOUT',
               'rollback':'failed'}}))
        last_bytes=last.read_bytes()
        selector=self.selector_paths[target]
        changed=plistlib.loads(selector.read_bytes())
        cfg=Path(changed['EnvironmentVariables']['WORKFLOW_IMAGE_HOST_CONFIG'])
        write(cfg,b'verified revised Codex pin')
        changed['EnvironmentVariables']['WORKFLOW_IMAGE_HOST_CONFIG_SHA256']=digest(cfg.read_bytes())
        write(selector,plistlib.dumps(changed))
        self.assertEqual(self.prepare(retry_probe=lambda *_:False)['targets'][target],
                         {'state':'blocked','code':'WORKER_RETRY_BASELINE_UNPROVEN'})
        self.assertFalse((folder/'retry-ready.json').exists())
        def authoritative_probe(t,h,p,finished):
            self.assertEqual((t,p,finished),(target,baseline,'2026-11-22T16:08:26Z'))
            return h==digest(selector.read_bytes())
        self.assertEqual(self.prepare(retry_probe=authoritative_probe)['targets'][target],{'state':'ready'})
        new_plan=json_load(prior.read_bytes())
        self.assertEqual(new_plan,{**old_plan,'expectedPriorPlistSha256':digest(selector.read_bytes())})
        self.assertEqual(ready.read_bytes(),old_ready)
        self.assertEqual(last.read_bytes(),last_bytes)
        retry=json_load((folder/'retry-ready.json').read_bytes())
        self.assertEqual(retry['failureSha256'],digest(last_bytes))
        self.assertEqual(retry['candidateSha256'],digest(prior.read_bytes()))
        atomic_write(prior,json_bytes({**new_plan,'treeSha256':'c'*64}))
        self.assertEqual(self.prepare(retry_probe=authoritative_probe)['targets'][target],
                         {'state':'blocked','code':'WORKER_REGISTERED_CANDIDATE_DRIFT'})

    def test_equivalent_loaded_worker_stays_current_even_if_busy_and_replaces_stale_ready(self):
        self.prepare()
        target='worker-document'
        self.blocked.add(target)
        selector=self.selector_paths[target]
        v=plistlib.loads(selector.read_bytes())
        equivalent=self.releases/('workflows-github-'+self.same_tree_sha)
        self.install_source(equivalent,self.same_tree_sha)
        v['ProgramArguments'][1]=str(equivalent/workers.TARGETS[target].entry)
        v['WorkingDirectory']=str((equivalent/workers.TARGETS[target].entry).parent)
        write(selector,plistlib.dumps(v))
        self.observed(target,self.same_tree_sha)
        folder=self.targets/target
        saved=(folder/'candidate.json').read_bytes()
        rows=self.prepare()['targets']
        self.assertEqual(rows[target],{'state':'current'})
        self.assertEqual(rows['worker-animation'],{'state':'already_ready'})
        self.assertEqual((folder/'candidate.json').read_bytes(),saved)
        proof=json_load((folder/'equivalent.json').read_bytes())
        self.assertEqual(proof['observedSha'],self.same_tree_sha)
        marker=json_load((folder/'worker-source-decision.json').read_bytes())
        self.assertEqual(marker['activeTreeSha256'],marker['candidateTreeSha256'])
        self.assertEqual(marker['state'],'equivalent')

    def test_active_tree_or_native_sidecar_drift_is_never_a_change(self):
        write(self.prior/'native-wan/workers/animation/unexpected.py',b'untested extra')
        sidecar=self.releases/('workflow-native-audio-resident-github-'+self.prior_sha[:7])
        write(sidecar/'extra.py',b'untested extra')
        rows=self.prepare()['targets']
        for target in ('worker-animation','worker-audio'):
            self.assertEqual(rows[target],{'state':'blocked','code':'WORKER_ACTIVE_TREE_DRIFT'})
            self.assertFalse((self.targets/target/'ready.json').exists())
        self.assertEqual(rows['worker-file'],{'state':'ready'})

    def test_unproven_loaded_selector_never_registers_earlier_observed_release(self):
        self.loaded=lambda target,pin:False
        rows=self.prepare()['targets']
        self.assertTrue(all(row=={'state':'blocked','code':'WORKER_LOADED_UNPROVEN'}
                            for row in rows.values()))
        self.assertEqual(list(self.targets.rglob('ready.json')),[])
