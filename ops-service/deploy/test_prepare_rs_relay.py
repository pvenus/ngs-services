import hashlib
import io
from pathlib import Path
import plistlib
import tarfile
import tempfile
import unittest

import prepare_ngs_release as source
from prepare_rs_relay import prepare_rs_relay
from mac_target_apply import apply_target, TARGETS
from test_mac_target_apply import FakeHost


class RelayTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.sha = 'a' * 40
        self.control = self.home / 'control'
        self.package = self.control / 'mac-releases' / self.sha
        self.folder = self.control / 'targets' / 'rs-mcp'
        target = TARGETS['rs-mcp']
        root = self.home / 'Workspaces/NGS/code/releases'
        release = root / (target.release_prefix + self.sha)
        old = root / (target.release_prefix + 'b' * 7)
        self.selector = self.home / 'Library/LaunchAgents' / (target.label + '.plist')
        self.entry = release / target.entry
        for path in (self.package, self.folder, self.selector.parent, self.entry.parent):
            path.mkdir(mode=0o700, parents=True)
            while path != self.home:
                path.chmod(0o700)
                path = path.parent
        self.entry.write_bytes(b'fixture')
        self.runner = self.folder / 'run-once'
        self.runner.write_bytes(b'fixture runner')
        self.runner.chmod(0o700)
        self.cfg = {'NGS_SPACE_MACHINE_CREDENTIALS_FILE': '/never/read/missing.json',
                    'NGS_SPACE_REQUEST_RELAY_ENABLED': '1'}
        self.selector.write_bytes(plistlib.dumps(dict(Label=target.label,
            WorkingDirectory=str(old), ProgramArguments=['/private/node',str(old / target.entry),'--watch'],
            EnvironmentVariables=self.cfg)))
        self.prior = hashlib.sha256(self.selector.read_bytes()).hexdigest()
        files = {target.entry: self.entry.read_bytes()}
        files['source-manifest.json'] = source.json_bytes(dict(files={target.entry: source.digest(files[target.entry])}))
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode='w') as tar:
            for name, body in files.items():
                f = tarfile.TarInfo(name)
                f.size = len(body)
                tar.addfile(f, io.BytesIO(body))
        self.raw = out.getvalue()
        self.arc = self.package / source.TAR
        source.atomic_write(self.arc, self.raw)
        self.digest = source.digest(self.raw)
        self.manifest = self.package / 'manifest.json'
        source.atomic_write(self.manifest, source.json_bytes(dict(schemaVersion='ops.ngs-mac-release.v1',
            sha=self.sha, repo=source.REPO, ref=source.REF, archiveSha256=self.digest,
            treeSha256=source.tree_sha(release), expectedPriorPlistSha256='c' * 64,
            workflow=dict(headSha=self.sha, headBranch='main', conclusion='success',
                          runId='1', runAttempt='1', artifactId='2'))))

    def prepare(self):
        return prepare_rs_relay(self.control, self.home, sha=self.sha, expected_prior_sha256=self.prior)

    def test_exact_candidate_handoff_preserves_private_configuration(self):
        original = self.selector.read_bytes()
        ready = self.prepare()
        self.assertEqual(original, self.selector.read_bytes())
        self.assertEqual(ready['artifactSha256'], self.digest)
        self.assertEqual(ready['runnerSha256'], source.digest(self.runner.read_bytes()))
        host = FakeHost()
        result = apply_target('rs-mcp', self.sha, self.digest, self.folder, self.home, host)
        self.assertEqual(result['health'], 'verified')
        new = plistlib.loads(self.selector.read_bytes())
        self.assertEqual(new['EnvironmentVariables'], self.cfg)
        self.assertEqual(new['ProgramArguments'][2:], ['--watch'])
        self.assertEqual(new['WorkingDirectory'], str(self.entry.parents[3]))

    def test_drift_never_publishes_ready(self):
        self.entry.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'LOCAL_TREE_CHANGED'):
            self.prepare()
        self.entry.write_bytes(b'fixture')
        self.arc.write_bytes(b'unverified')
        with self.assertRaisesRegex(ValueError, 'LOCAL_ARCHIVE_CHANGED'):
            self.prepare()
        source.atomic_write(self.arc, self.raw)
        self.selector.write_bytes(self.selector.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'PRIOR_CHANGED'):
            self.prepare()
        self.assertFalse((self.folder / 'candidate.json').exists())
        self.assertFalse((self.folder / 'ready.json').exists())
