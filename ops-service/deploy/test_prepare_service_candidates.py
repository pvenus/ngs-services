import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import tempfile
import unittest

import prepare_service_candidates as app
from prepare_service_targets import assemble_source
import test_prepare_rs_relay as relay_fixture
import test_prepare_service_targets as proof_fixture


class ReadOnlyHost:
    def __init__(self):
        self.reports = {}

    def add(self, selector):
        v = plistlib.loads(selector.read_bytes())
        lines = ['\targuments = {', *['\t\t' + a for a in v['ProgramArguments']], '\t}']
        if 'WorkingDirectory' in v:
            lines.append('\tworking directory = '+v['WorkingDirectory'])
        self.reports[v['Label']] = 123, '\n'.join(lines)

    def launch_report(self, label):
        return self.reports[label]


class ServiceCandidates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fi = proof_fixture.ServiceTargets
        fi.setUpClass()
        cls.repo = fi.repo
        cls.head = fi.head
        cls.verified = fi().verified()

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.parent = self.home/'Workspaces/BackendServices/code/releases'
        self.parent.mkdir(parents=True, mode=0o755)
        self.parent.chmod(0o755)
        self.control = self.home/'ops-control'
        self.control.mkdir(mode=0o700)
        (self.control/'targets').mkdir(mode=0o700)
        self.host = ReadOnlyHost()
        self.storage = self.home/'Workspaces/PDS/data'
        self.storage.mkdir(parents=True)
        self.db = self.home/'Workspaces/Workers/data/workflow-planner/workflow.sqlite'
        self.db.parent.mkdir(parents=True)
        self.db.write_bytes(b'private fixture; must not open')
        self.db.chmod(0o600)
        self.install = assemble_source(self.verified, self.repo, self.parent, public_source_parent=True)
        self.old = self.parent/'ngs-services-github-abcdefa'
        shutil.copytree(self.install, self.old)
        self.old.chmod(0o700)
        self.selectors = {}
        for t in app.ORDER:
            self.selectors[t] = self.bind(t)

    def bind(self, t):
        cat = app.TARGETS[t]
        node = self.home/app.NODE
        entry = self.old/cat.entry
        d = {'Label':cat.label, 'ProgramArguments':[str(node), str(entry)],
             'WorkingDirectory':str(entry.parent), 'EnvironmentVariables':{}}
        if t == 'request-service':
            d['EnvironmentVariables']['REQUEST_OFFICIAL_INBOX_DIR'] = str(self.home/'requests')
        elif t == 'workflow-planner':
            d['ProgramArguments'] += ['--db', str(self.db)]
        elif t == 'backend-stack':
            d['ProgramArguments'][0] = '/opt/homebrew/bin/python3.12'
            d['ProgramArguments'] += ['--storage-root', str(self.storage)]
            del d['WorkingDirectory']
        f = self.control/'targets'/t
        f.mkdir(mode=0o700)
        (f/'run-once').write_text('must not execute')
        (f/'run-once').chmod(0o500)
        s = self.home/'Library/LaunchAgents'/(cat.label+'.plist')
        s.parent.mkdir(parents=True, exist_ok=True)
        s.write_bytes(plistlib.dumps(d))
        self.host.add(s)
        return s

    def run_prepare(self, assert_current=None, filex_ready=lambda _:False):
        return app.prepare_service_candidates(
            self.verified, self.repo, self.home, self.control,
            assert_current=assert_current or (lambda sha:self.assertEqual(sha,self.head)),
            filex_ready=filex_ready, host=self.host)

    def test_exact_verified_modes_register_manual_ready_without_live_changes(self):
        before = {t:f.read_bytes() for t,f in self.selectors.items()}
        outcome = self.run_prepare()
        self.assertTrue(all(row['state']=='registered' for row in outcome.values()), outcome)
        self.assertEqual(stat.S_IMODE(self.parent.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.install.stat().st_mode), 0o700)
        files = {}
        for t,p in self.selectors.items():
            self.assertEqual(before[t],p.read_bytes())
            f = self.control/'targets'/t
            candidate = json.loads((f/'candidate.json').read_text())
            self.assertEqual(candidate['sha'], self.head)
            self.assertEqual(candidate['expectedPriorPlistSha256'], app.digest(before[t]))
            files[t] = (f/'ready.json').read_bytes()
            self.assertTrue(json.loads(files[t])['manualOnly'])
        self.assertEqual((self.db.read_bytes(),self.db.stat().st_mode & 0o777),
                         (b'private fixture; must not open',0o600))
        self.assertTrue(all(row['state']=='ready' for row in self.run_prepare().values()))
        for t,b in files.items():
            self.assertEqual((self.control/'targets'/t/'ready.json').read_bytes(), b)

    def test_changed_filex_needs_active_companion_and_abi_never_assumed(self):
        (self.old/app.FILEX_ABI[0]).write_bytes(b'prior older protocol')
        result = self.run_prepare()
        self.assertEqual(result['backend-stack']['code'], 'FILEX_COMPANION_NOT_ACTIVE')
        self.assertFalse((self.control/'targets/backend-stack/ready.json').exists())
        checked=[]
        result = self.run_prepare(filex_ready=lambda sha: checked.append(sha) or True)
        self.assertEqual(result['backend-stack']['state'], 'registered')
        self.assertEqual(checked, [self.head]*3)
        (self.old/app.PLAN_ABI[0]).write_text('db migration drift')
        result = self.run_prepare(filex_ready=lambda _:True)
        self.assertEqual(result['workflow-planner']['code'], 'PLANNER_STARTUP_OR_STORAGE_ABI_CHANGED')

    def test_runner_selector_main_and_active_attempt_fail_closed(self):
        labels = self.host.reports.copy()
        t='request-service'
        label=app.TARGETS[t].label
        self.host.reports[label]=(None,'')
        self.assertEqual(self.run_prepare()[t]['code'], 'SERVICE_PRIOR_SELECTOR_NOT_LOADED')
        self.host.reports=labels
        (self.control/'active.json').write_bytes(b'{}')
        self.assertEqual(self.run_prepare()[t]['code'], 'PREP_ACTIVE_DEPLOYMENT')
        (self.control/'active.json').unlink()
        counts=[]
        def moving(head):
            counts.append(head)
            if len(counts)>1:
                raise ValueError('MAIN_ADVANCED')
        old_ready={f:f.read_bytes() for f in self.control.rglob('ready.json')}
        rows=self.run_prepare(assert_current=moving)
        self.assertTrue(all(r['code']=='MAIN_ADVANCED' for r in rows.values()))
        self.assertEqual({f:f.read_bytes() for f in self.control.rglob('ready.json')}, old_ready)

    def test_observed_identical_code_is_current_not_repeat_deploy(self):
        f=self.control/'targets/request-service'
        prior='abcdefa'+'d'*33
        app.atomic_write(f/'observed.json',app.json_bytes({'sha':prior,'deployedAt':'2026-11-22T00:00:00Z'}))
        result=self.run_prepare()['request-service']
        self.assertEqual(result['state'],'current')
        equivalent=app.read_optional(f/'equivalent.json')
        self.assertEqual(equivalent['observedSha'],prior)
        self.assertEqual(equivalent['sha'],self.head)
        self.assertEqual(app.read_optional(f/'observed.json')['sha'],prior)
        self.assertTrue(app.read_optional(f/'ready.json')['manualOnly'])
        self.assertEqual(self.run_prepare()['request-service']['state'],'current')
        self.assertEqual(app.read_optional(f/'equivalent.json'),equivalent)

    def test_corrupt_reused_staging_refuses_without_overwriting(self):
        f=self.install/'request-service/request-mcp.mjs'
        f.write_bytes(b'wrong')
        with self.assertRaisesRegex(ValueError, 'SERVICE_RELEASE_SOURCE_DRIFT'):
            self.run_prepare()
        self.assertEqual(f.read_bytes(), b'wrong')
        self.assertFalse(list(self.control.rglob('ready.json')))


class RelayPreparation(unittest.TestCase):
    def test_uses_exact_local_artifact_and_marks_manual_without_switching(self):
        fixture=relay_fixture.RelayTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        prior=plistlib.loads(fixture.selector.read_bytes())
        prior['ProgramArguments'][0]=str(fixture.home/app.NODE)
        old=Path(prior['WorkingDirectory'])
        old.mkdir(mode=0o700)
        fixture.selector.write_bytes(plistlib.dumps(prior))
        host=ReadOnlyHost()
        host.add(fixture.selector)
        before=fixture.selector.read_bytes()
        check=lambda head:self.assertEqual(head,fixture.sha)
        manifest=fixture.manifest.read_bytes()
        fixture.manifest.unlink()
        result=app.prepare_rs_mcp_candidate(fixture.control,fixture.home,fixture.sha,assert_current=check,host=host)
        self.assertEqual(result['code'],'SERVICE_LOCAL_ARTIFACT_UNAVAILABLE')
        app.atomic_write(fixture.manifest,manifest)
        result=app.prepare_rs_mcp_candidate(fixture.control,fixture.home,fixture.sha,assert_current=check,host=host)
        self.assertEqual(result['state'],'registered',result)
        self.assertEqual(fixture.selector.read_bytes(),before)
        self.assertTrue(app.read_optional(fixture.folder/'ready.json')['manualOnly'])


if __name__ == '__main__':
    unittest.main()
