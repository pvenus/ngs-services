import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import Mock
from urllib.parse import urlsplit
from test_github_actions_keychain import Response, PAT, LOCATION
import github_actions_keychain as transport
from check_ngs_local_github_auth import check

import prepare_ngs_local_once as auto
import prepare_ngs_release as prep
import test_prepare_ngs_release as proof


class Tests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        base = Path(t.name).resolve()
        self.base = base
        self.control = control = base / 'control'
        self.releases, self.scratch = base / 'releases', base / 'execution'
        self.target = control / 'targets/ngs-local'
        self.selector, self.node = base / 'ngs.plist', base / 'node'
        for folder in (control, control / 'targets', self.target, control / 'mac-releases', control / 'bin',
                       self.releases, self.scratch):
            folder.mkdir(mode=0o700)
        self.write(self.target / 'run-once', b'REFUSE TO RUN ME', 0o500)
        self.old = '0' * 40
        self.select(self.old)
        self.fix = proof.EvidenceFixture()
        self.fix.setUp()
        self.head = self.fix.head
        self.latest = self.head
        self.zip = self.fix.zip
        self.lookup = Mock(return_value=PAT)
        self.get = Mock(side_effect=self.open)
        opener = Mock(open=self.get)
        self.github = transport.KeychainGitHubEvidence(token=self.lookup, opener=opener)
        self.assembler = Mock()
        self.assembler.assemble_verified.side_effect = self.assemble
        self.on_assemble = lambda: None

    def write(self, path, content, mode=0o600):
        path.write_bytes(content)
        path.chmod(mode)

    def select(self, head):
        data = {'Label': auto.SELECTOR, 'ProgramArguments': [str(self.node),
                str(self.releases / ('project-page-github-' + head) / auto.ENTRY)],
                'WorkingDirectory': str(self.scratch / head)}
        self.write(self.selector, plistlib.dumps(data))

    def open(self, request, *, timeout):
        url=urlsplit(request.full_url)
        if request.full_url == LOCATION:
            self.assertNotIn("Authorization",request.headers)
            return Response(200,self.zip)
        self.assertEqual(url.netloc,'api.github.com')
        route=url.path[1:]
        if route==auto.MAIN_ROUTE:
            self.assertNotIn("Authorization",request.headers)
            return Response(200,prep.json_bytes({'ref':prep.REF,'object':{'type':'commit','sha':self.latest}}))
        self.assertEqual(request.get_header("Authorization"),"Bearer "+PAT)
        name=route.removeprefix(transport.PREFIX)
        if name=='actions/workflows/prepare-ngs-release.yml': result=self.fix.workflow
        elif name=='actions/workflows/5/runs': result={'workflow_runs':[self.fix.run]}
        elif name=='actions/runs/14/artifacts': result={'artifacts':[self.fix.artifact],'total_count':1}
        elif name=='actions/artifacts/63': result=self.fix.artifact
        elif name=='actions/artifacts/63/zip': return Response(302,headers=[('Location',LOCATION)])
        else: self.fail(name)
        return Response(200,prep.json_bytes(result))

    def assemble(self, verified, *, control, releases, scratch, prior_plist, expected_prior_sha256, node, npm_cache):
        self.assertEqual(prep.digest(prior_plist.read_bytes()), expected_prior_sha256)
        self.assertEqual((control, releases, scratch, node),
                         (self.control, self.releases, self.scratch, self.node))
        source, execution = releases / ('project-page-github-' + verified['head']), scratch / verified['head']
        source.mkdir(mode=0o700); execution.mkdir(mode=0o700)
        for name, raw in verified['files'].items():
            path = source / name
            path.parent.mkdir(mode=0o700, exist_ok=True, parents=True)
            self.write(path, raw)
        path = source / 'local-miniflare/node_modules/miniflare/package.json'
        path.parent.mkdir(mode=0o700, parents=True)
        self.write(path, b'{"version":"test-locked"}')
        self.on_assemble()
        return source, execution

    def run_once(self):
        return auto.prepare_once(self.github, self.assembler, control=self.control, releases=self.releases,
                                 scratch=self.scratch, prior_plist=self.selector, node=self.node,
                                 npm_cache=self.base / 'cache')

    def not_published(self):
        self.assertFalse((self.control / 'prepared.json').exists())
        self.assertFalse((self.target / 'ready.json').exists())

    def test_keychain_transport_exact_proof_becomes_button_candidate_only_and_never_downloads_same_ready(self):
        before = self.selector.read_bytes()
        self.assertEqual(self.run_once(), {'target':'ngs-local','sha':self.head,'state':'registered'})
        self.assertEqual(self.selector.read_bytes(), before)
        self.assertFalse((self.control / 'active.json').exists())
        ready = (self.target / 'ready.json').read_bytes()
        self.assertEqual(json.loads(ready)['sha'], self.head)
        self.assertEqual(json.loads(ready)['runnerSha256'], prep.digest(b'REFUSE TO RUN ME'))
        before_calls=self.get.call_count
        self.assertEqual(self.run_once()['state'],'ready')
        self.assertEqual(self.get.call_count-before_calls,1)
        self.assembler.assemble_verified.assert_called_once()
        self.assertEqual((self.target / 'ready.json').read_bytes(), ready)

    def test_already_selected_never_registers_or_fetches_zip(self):
        self.select(self.head)
        self.assertEqual(self.run_once()['state'],'current')
        self.lookup.assert_not_called()
        self.not_published()
        self.assembler.assemble_verified.assert_not_called()

    def test_deny_tampered_zip_foreign_workflow_or_not_current_before_assembly(self):
        self.zip=self.fix.zip[:-1] + b'X'
        with self.assertRaisesRegex(auto.Refused, 'ZIP_DIGEST_MISMATCH'): self.run_once()
        self.zip=self.fix.zip
        self.fix.run['workflow_id']=6
        with self.assertRaisesRegex(auto.Refused, 'RUN_NOT_VERIFIED'): self.run_once()
        self.fix.run['workflow_id']=5
        self.latest = 'b' * 40
        with self.assertRaisesRegex(auto.Refused, 'CI_CANDIDATE_NOT_READY'): self.run_once()
        self.assembler.assemble_verified.assert_not_called()
        self.not_published()

    def test_advanced_main_during_offline_assembly_never_registers_stale_candidate(self):
        self.on_assemble = lambda: setattr(self, 'latest', 'b' * 40)
        with self.assertRaisesRegex(auto.Refused, 'MAIN_ADVANCED'): self.run_once()
        self.not_published()

    def test_selector_drift_or_attempt_marker_never_rebases_implicitly(self):
        self.on_assemble = lambda: self.select('b' * 40)
        with self.assertRaisesRegex(auto.Refused, 'PRIOR_PLIST_CHANGED'): self.run_once()
        self.not_published()
        with self.assertRaisesRegex(auto.Refused, 'PREP_RECONCILE_REQUIRED'): self.run_once()
        self.assembler.assemble_verified.assert_called_once()

    def test_cycle_lock_busy_keeps_old_state_then_exact_staged_pair_is_idempotently_resumed(self):
        path=self.control/'cycle.lock'; self.write(path, b'')
        with auto.lock(path):
            with self.assertRaisesRegex(auto.Refused, 'PREP_LOCK_BUSY'): self.run_once()
        self.not_published()
        self.assertEqual(self.run_once()['state'],'registered')
        self.assembler.assemble_verified.assert_called_once()

    def test_unknown_journal_and_bad_private_inputs_are_not_ignored(self):
        self.write(self.control/'active.json', b'{}')
        with self.assertRaisesRegex(auto.Refused, 'PREP_ACTIVE_DEPLOYMENT'): self.run_once()
        self.not_published()
        self.write(self.control/'bin/assemble_mac_release.py',b'raise AssertionError("never execute")')
        with self.assertRaisesRegex(auto.Refused, 'NGS_ASSEMBLER_PIN_MISMATCH'): auto.load_assembler(self.control)

    def test_keychain_absent_no_artifact_or_assembly(self):
        self.lookup.side_effect=auto.Refused("GITHUB_KEYCHAIN_ITEM_MISSING")
        with self.assertRaisesRegex(auto.Refused, "GITHUB_KEYCHAIN_ITEM_MISSING"): self.run_once()
        self.assembler.assemble_verified.assert_not_called()
        self.not_published()

    def test_prior_attempt_blocks_before_keychain_and_probe_is_independent_read_only(self):
        release=self.control/'mac-releases'/self.head
        release.mkdir(mode=0o700)
        self.write(release/'result.json',b'{"state":"rolled-back"}')
        with self.assertRaisesRegex(auto.Refused, 'PREP_RECONCILE_REQUIRED'): self.run_once()
        self.lookup.assert_not_called()
        self.assertEqual(check(self.github),{'target':'ngs-local','state':'artifact_read_verified','sha':self.head})
        self.lookup.assert_called_once()
        self.not_published()
        self.assembler.assemble_verified.assert_not_called()
        self.assertEqual((release/'result.json').read_bytes(),b'{"state":"rolled-back"}')

    def test_status_states_are_bounded_and_sanitized_without_readiness(self):
        stamp='2026-11-22T00:00:00Z'
        statuses={'GITHUB_KEYCHAIN_ITEM_MISSING':'auth_missing','GITHUB_KEYCHAIN_LOCKED_OR_DENIED':'auth_locked_or_denied',
                  'GITHUB_ACTIONS_READ_DENIED':'auth_rejected','CI_CANDIDATE_NOT_READY':'pending',
                  'https://private?token=secret':'blocked'}
        for code,state in statuses.items():
            result=auto.status_record({'code':code}, self.control, stamp=stamp)
            self.assertEqual(result['state'],state)
            path=self.control/auto.STATUS
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            raw=path.read_bytes()
            self.assertLess(len(raw),4096)
            self.assertNotIn(b'secret',raw)
        status=auto.status_record({'sha':self.head,'state':'registered'},self.control,stamp=stamp)
        self.assertEqual(status['sha'],self.head)
        self.assertEqual(status['state'],'ready')
        self.not_published()
