import fcntl
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest


class ServiceRetentionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        script = Path(__file__).with_name('cleanup_expired_temporary.py')
        self.runner = runpy.run_path(str(script))
        fragment = script.with_name('service_release_retention_fragment.py')
        if fragment.exists():
            exec(compile(fragment.read_bytes(), fragment, 'exec'), self.runner)
        else:
            self.runner = self.runner['sre_cleanup'].__globals__
        self.prefix = 'workflows-github-'
        self.entry = 'document/cli.mjs'
        self.target = 'worker-document'
        self.runner['SRE_WORKERS'] = ((self.target, 'test-label', self.prefix, self.entry),)
        self.control = self.home / self.runner['CONTROL']
        self.control.mkdir(mode=0o700, parents=True)
        self.root = self.home / 'Workspaces/Workers/code/releases'
        self.root.mkdir(mode=0o700, parents=True)
        self.state = self.control / 'targets' / self.target
        self.state.mkdir(mode=0o700, parents=True)
        for n in ('prepare-ops-candidates.lock', 'prepare-workers.lock', 'cycle.lock'):
            (self.control / n).touch(mode=0o600)
        self.now = 30 * self.runner['NANOSECONDS_PER_DAY']
        self.current, self.prior = self.release('a'), self.release('b')
        self.record('observed.json', {'sha': 'b' * 40, 'deployedAt': '2026-11-21T00:00:00Z'})

    def release(self, digit, days=19, *, name=None):
        folder = self.root / (name or (self.prefix + digit * 40))
        leaf = folder / self.entry
        leaf.parent.mkdir(parents=True, mode=0o700)
        leaf.write_text('trusted release fixture\n')
        leaf.chmod(0o600)
        for p in [leaf, leaf.parent, folder]:
            ticks = self.now - days * self.runner['NANOSECONDS_PER_DAY']
            os.utime(p, ns=(ticks, ticks))
        return folder.name

    def record(self, name, data):
        path = self.state / name
        path.write_text(json.dumps(data))
        path.chmod(0o600)

    def run_retention(self, **kwargs):
        return self.runner['cleanup_worker_releases'](
            self.home, now_ns=self.now,
            live=lambda *_: ({self.target: self.current}, {self.current}),
            open_check=lambda _: kwargs.get('closed', True),
            **{k: v for k, v in kwargs.items() if k != 'closed'})

    def test_aged_only_and_keep_all_deployment_pins(self):
        eligible, pending, other, fresh = self.release('c'), self.release('d'), self.release('e'), self.release('f', days=9)
        legacy = self.release('0', name=self.prefix + '0000000')
        self.record('candidate.json', {'schemaVersion': 'ops.mac-target-candidate.v1', 'target': self.target, 'sha': 'd' * 40})
        self.record('equivalent.json', {'schemaVersion': 'ops.deploy-target-equivalent.v1', 'target': self.target,
                                       'sha': 'a' * 40, 'observedSha': 'e' * 40})
        report = self.run_retention(dry_run=True)
        self.assertEqual(report['eligible'], 1)
        self.assertEqual(report['removed'], 0)
        self.assertTrue((self.root / eligible).exists())
        report = self.run_retention(dry_run=False)
        self.assertEqual(report['removed'], 1)
        self.assertFalse((self.root / eligible).exists())
        self.assertTrue(all((self.root / name).exists() for name in (pending, other, fresh, legacy, self.current, self.prior)))

    def test_unproved_rollback_and_active_deploy_fail_closed(self):
        self.release('c')
        self.record('observed.json', {'sha': 'a' * 40, 'deployedAt': '2026-11-21T00:00:00Z'})
        report = self.run_retention()
        self.assertEqual(report['eligible'], 0)
        self.assertGreater(report['heldNoRollback'], 0)
        (self.control / 'active.json').touch()
        self.assertTrue(self.run_retention()['blocked'])

    def test_competing_producer_lock(self):
        self.release('c')
        with (self.control / 'prepare-workers.lock').open('r+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(self.run_retention()['blocked'])

    def test_symlink_open_and_unknown_content_are_held(self):
        eligible = self.release('c')
        self.assertEqual(self.run_retention(closed=False)['removed'], 0)
        tree = self.root / eligible
        (tree / 'symlink').symlink_to(self.root / self.current)
        t = self.now - 19 * self.runner['NANOSECONDS_PER_DAY']
        os.utime(tree / 'symlink', ns=(t, t), follow_symlinks=False)
        os.utime(tree, ns=(t, t))
        self.assertEqual(self.run_retention()['removed'], 0)
        self.assertTrue(tree.exists())

    def test_unverified_deployment_record_blocks(self):
        self.record('ready.json', {'schemaVersion': 'unexpected', 'target': self.target, 'sha': 'a' * 40})
        self.assertTrue(self.run_retention()['blocked'])

    def test_extracts_literal_working_and_env_refs(self):
        x = [str(self.root / self.current / self.entry),
             {'X': '/x:' + str(self.root / self.prior) + ':/z'}]
        self.assertEqual(self.runner['sre_refs'](x, self.root), {self.current, self.prior})
