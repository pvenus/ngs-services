import json
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import Mock, patch

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
        self.inbox = control / 'ngs-local-evidence'
        for folder in (control, control / 'targets', self.target, control / 'mac-releases', control / 'bin',
                       self.inbox, self.releases, self.scratch):
            folder.mkdir(mode=0o700)
        self.write(self.target / 'run-once', b'REFUSE TO RUN ME', 0o500)
        self.old = '0' * 40
        self.select(self.old)
        self.fix = proof.EvidenceFixture()
        self.fix.setUp()
        self.fix.run['workflow_id'] = auto.CONNECTOR_WORKFLOW
        self.head = self.fix.head
        self.latest = self.head
        self.write_evidence()
        self.github = auto.ConnectorEvidence(self.inbox, self.ref)
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

    def write_evidence(self, **updates):
        shape = {'schemaVersion': auto.CONNECTOR_SCHEMA, 'headSha': self.head,
                 'run': self.fix.run, 'artifact': self.fix.artifact, **updates}
        self.write(self.inbox / 'evidence.json', json.dumps(shape).encode())
        self.write(self.inbox / 'ci.zip', self.fix.zip)

    def ref(self, route):
        self.assertEqual(route, auto.MAIN_ROUTE)
        return {'ref': prep.REF, 'object': {'type': 'commit', 'sha': self.latest}}

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

    def run_once(self, verify=None):
        return auto.prepare_once(self.github, self.assembler, control=self.control, releases=self.releases,
                                 scratch=self.scratch, prior_plist=self.selector, node=self.node,
                                 npm_cache=self.base / 'cache', verify=self.github.verify if verify is None else verify)

    def not_published(self):
        self.assertFalse((self.control / 'prepared.json').exists())
        self.assertFalse((self.target / 'ready.json').exists())

    def test_connectors_exact_proof_becomes_button_candidate_only_and_never_downloads_same_ready(self):
        before = self.selector.read_bytes()
        self.assertEqual(self.run_once(), {'target':'ngs-local','sha':self.head,'state':'registered'})
        self.assertEqual(self.selector.read_bytes(), before)
        self.assertFalse((self.control / 'active.json').exists())
        ready = (self.target / 'ready.json').read_bytes()
        self.assertEqual(json.loads(ready)['sha'], self.head)
        self.assertEqual(json.loads(ready)['runnerSha256'], prep.digest(b'REFUSE TO RUN ME'))
        self.assertEqual(self.run_once(Mock(side_effect=AssertionError('no second proof download')))['state'],'ready')
        self.assembler.assemble_verified.assert_called_once()
        self.assertEqual((self.target / 'ready.json').read_bytes(), ready)

    def test_already_selected_never_registers_or_fetches_zip(self):
        self.select(self.head)
        self.assertEqual(self.run_once(Mock(side_effect=AssertionError('no proof download')))['state'],'current')
        self.not_published()
        self.assembler.assemble_verified.assert_not_called()

    def test_deny_tampered_zip_foreign_workflow_or_not_current_before_assembly(self):
        self.write(self.inbox / 'ci.zip', self.fix.zip + b'X')
        with self.assertRaisesRegex(auto.Refused, 'ZIP_DIGEST_MISMATCH'): self.run_once()
        self.write_evidence(run={**self.fix.run, 'workflow_id': 5})
        with self.assertRaisesRegex(auto.Refused, 'RUN_NOT_VERIFIED'): self.run_once()
        self.write_evidence()
        self.latest = 'b' * 40
        with self.assertRaisesRegex(auto.Refused, 'MAIN_ADVANCED'): self.run_once()
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

    def test_no_artifact_or_untrusted_permissions_never_assemble(self):
        (self.inbox/'ci.zip').unlink()
        with self.assertRaisesRegex(auto.Refused, 'UPSTREAM_ARTIFACT_UNAVAILABLE'): self.run_once()
        self.write(self.inbox/'ci.zip', self.fix.zip, 0o644)
        with self.assertRaises(prep.Refused): self.run_once()
        self.assembler.assemble_verified.assert_not_called()
        self.not_published()

    def test_git_fallback_fixed_ref_no_credentials_and_only_loopback_proxy(self):
        local='http://127.0.0.1:8080'
        raw=(self.head+'\trefs/heads/main\n').encode()
        with patch.dict(os.environ,{'HTTPS_PROXY':local,'https_proxy':'https://secret@example.org:443',
                                    'NO_PROXY':'github.com','GH_TOKEN':'never forward'},clear=True), \
             patch.object(auto.subprocess,'run',return_value=Mock(stdout=raw,returncode=0)) as call:
            self.assertEqual(auto.read_git_main(),self.ref(auto.MAIN_ROUTE))
        args=call.call_args.args[0]
        self.assertIn('credential.helper=',args)
        self.assertEqual(args[-2:],[auto.REMOTE,prep.REF])
        self.assertEqual(call.call_args.kwargs['env'],{'PATH':'/usr/bin:/bin','GIT_CONFIG_NOSYSTEM':'1',
              'GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0','HTTPS_PROXY':local})
        self.assertEqual(auto.loopback_https_proxy({'https_proxy':'https://[::1]:9000'}),{'https_proxy':'https://[::1]:9000'})
        for value in ('http://127.0.0.1:1/path','http://127.0.0.1:99999','https://127.0.0.1:9?q=1',
                      'http://someone@127.0.0.1:9','http://127.0.0.1:9#fragment'):
            self.assertEqual(auto.loopback_https_proxy({'https_proxy':value}),{})
