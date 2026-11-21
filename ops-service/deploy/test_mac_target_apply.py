import json
from pathlib import Path
import plistlib
import tempfile
import unittest

import mac_target_apply as apply


class FakeHost:
    def __init__(self, fail=False):
        self.events = []
        self.running = 101
        self.serial = 101
        self.fail = fail

    def pid(self, label):
        return self.running

    def remove(self, label):
        self.events.append(('remove', label))
        self.running = None

    def start(self, label, selector):
        self.events.append(('start', label))
        self.serial += 1
        self.running = self.serial

    def wait_healthy(self, catalog, since, previous_pid):
        self.events.append(('health', catalog.label))
        assert self.running != previous_pid
        if self.fail and self.running == 102:
            raise RuntimeError('failed new process')


class TargetTests(unittest.TestCase):
    def make(self, home, target):
        item = apply.TARGETS[target]
        base = home / 'Workspaces' / item.workspace / 'code/releases'
        release = base / (item.release_prefix + 'a' * 40)
        old = base / (item.release_prefix + '1234567')
        release.mkdir(mode=0o700, parents=True)
        entry = release / item.entry
        entry.parent.mkdir(mode=0o700, exist_ok=True, parents=True)
        entry.write_text('test')
        args = ['/test/interpreter', str(old / item.entry), '--db', '/test/same-ledger']
        for key, rel in [('worker-audio','candidate-audio-resident/adapters/load.mjs'),
                         ('worker-postprocess','native-postprocess')]:
            if target == key:
                path = release / rel
                if key == 'worker-audio':
                    path.parent.mkdir(mode=0o700)
                    path.write_text('test')
                    args.extend(['--adapter-module', str(old / rel), '--adapter-config', '/test/private-config'])
                else:
                    path.mkdir(mode=0o700)
                    args.extend(['--worker-source', str(old / rel)])
        selector = home / 'Library/LaunchAgents' / (item.label + '.plist')
        selector.parent.mkdir(mode=0o700, parents=True)
        env = {'X':'same'}
        wd = old / Path(item.entry).parent
        if target == 'rs-mcp':
            args[2:] = ['--watch']
            wd = old
            env.update(NGS_SPACE_MACHINE_CREDENTIALS_FILE='/test/private/missing.json',
                       NGS_SPACE_REQUEST_RELAY_ENABLED='1')
        before = plistlib.dumps(dict(Label=item.label, ProgramArguments=args, RunAtLoad=True,
                              WorkingDirectory=str(wd), EnvironmentVariables=env))
        selector.write_bytes(before)
        selector.chmod(0o600)
        directory = home / target
        directory.mkdir(mode=0o700)
        manifest = dict(schemaVersion='ops.mac-target-candidate.v1', target=target, sha='a' * 40,
                        archiveSha256='b' * 64, treeSha256=apply.tree_sha(release),
                        expectedPriorPlistSha256=apply.digest(before))
        (directory / 'candidate.json').write_text(json.dumps(manifest))
        (directory / 'candidate.json').chmod(0o600)
        return directory, selector, before, entry

    def test_all_fixed_targets_real_contract_and_preservation(self):
        for name in apply.TARGETS:
            with self.subTest(target=name), tempfile.TemporaryDirectory() as root:
                home = Path(root).resolve()
                folder, selector, prior, entry = self.make(home, name)
                host = FakeHost()
                receipt = apply.apply_target(name, 'a' * 40, 'b' * 64, folder, home, host)
                selected = plistlib.loads(selector.read_bytes())
                self.assertEqual(selected['ProgramArguments'][1], str(entry))
                original = plistlib.loads(prior)
                if name == 'rs-mcp':
                    self.assertEqual(selected['ProgramArguments'][2:], ['--watch'])
                    self.assertEqual(selected['WorkingDirectory'], str(entry.parents[3]))
                else:
                    self.assertEqual(selected['ProgramArguments'][3], '/test/same-ledger')
                self.assertEqual(selected['EnvironmentVariables'], original['EnvironmentVariables'])
                self.assertEqual(receipt['health'], 'verified')
                self.assertEqual([x[0] for x in host.events], ['remove', 'start', 'health'])

    def test_health_failure_rolls_back_exact_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root).resolve()
            folder, selector, prior, _ = self.make(home, 'worker-document')
            host = FakeHost(True)
            with self.assertRaisesRegex(RuntimeError, 'ROLLED_BACK'):
                apply.apply_target('worker-document', 'a' * 40, 'b' * 64, folder, home, host)
            self.assertEqual(selector.read_bytes(), prior)
            self.assertEqual([x[0] for x in host.events], ['remove','start','health','remove','start','health'])

    def test_fail_closed_on_plist_and_release_drift(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root).resolve()
            folder, selector, prior, entry = self.make(home, 'worker-document')
            host = FakeHost()
            selector.write_bytes(prior + b' ')
            with self.assertRaisesRegex(ValueError, 'SELECTOR_CHANGED'):
                apply.apply_target('worker-document', 'a' * 40, 'b' * 64, folder, home, host)
            selector.write_bytes(prior)
            entry.write_text('modified')
            with self.assertRaisesRegex(ValueError, 'TREE_MISMATCH'):
                apply.apply_target('worker-document', 'a' * 40, 'b' * 64, folder, home, host)
            self.assertEqual(host.events, [])


if __name__ == '__main__':
    unittest.main()
