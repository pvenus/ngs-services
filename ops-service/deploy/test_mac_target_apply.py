import json
from pathlib import Path
import plistlib
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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
        if target == 'workflow-planner':
            old_entry = old / item.entry
            old_entry.parent.mkdir(parents=True)
            old_entry.write_text('old')
            db = home / 'Workspaces/Workers/data/workflow-planner/workflow.sqlite'
            db.parent.mkdir(mode=0o700, parents=True)
            db.touch(mode=0o600)
            args[3] = str(db)
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
        for name in set(apply.TARGETS)-{'workflow-planner'}:
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

    def test_planner_same_database_and_old_no_revision_rollback(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as root:
                home = Path(root).resolve()
                folder, selector, prior, entry = self.make(home, 'workflow-planner')
                host = FakeHost()
                before = plistlib.loads(prior)
                db = Path(before['ProgramArguments'][3])
                inode = db.stat().st_ino
                def launch_report(label):
                    spec = plistlib.loads(selector.read_bytes())
                    return host.running, ('\targuments = {\n' +
                        ''.join('\t\t' + arg + '\n' for arg in spec['ProgramArguments']) +
                        '\t}\n\tworking directory = ' + spec['WorkingDirectory'])
                host.launch_report = launch_report
                observed = []
                def wait(**kwargs):
                    args, _, identity, sha = kwargs['expected']
                    self.assertEqual(kwargs['database_identity'](), identity)
                    self.assertEqual(identity[1], inode)
                    self.assertEqual(args[2:], ('--db', str(db)))
                    self.assertEqual(kwargs['live']()[0], host.running)
                    observed.append(sha)
                    if fail and sha:
                        raise ValueError('TARGET_PLANNER_HEALTH_TIMEOUT')
                with patch.object(apply,'wait_planner',side_effect=wait):
                    if fail:
                        with self.assertRaises(apply.ApplyFailure) as error:
                            apply.apply_target('workflow-planner','a'*40,'b'*64,folder,home,host)
                        self.assertEqual(error.exception.rollback,'restored')
                        self.assertEqual(selector.read_bytes(),prior)
                    else:
                        self.assertEqual(apply.apply_target('workflow-planner','a'*40,'b'*64,folder,home,host)['health'],'verified')
                        proposed = plistlib.loads(selector.read_bytes())
                        self.assertEqual(proposed['ProgramArguments'][1],str(entry))
                        self.assertEqual(proposed['EnvironmentVariables'],{'X':'same',apply.REVISION:'a'*40})
                self.assertEqual(observed,['a'*40,None] if fail else ['a'*40])

    def test_health_failure_rolls_back_exact_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root).resolve()
            folder, selector, prior, _ = self.make(home, 'worker-document')
            host = FakeHost(True)
            with self.assertRaises(apply.ApplyFailure) as failure:
                apply.apply_target('worker-document', 'a' * 40, 'b' * 64, folder, home, host)
            self.assertEqual(apply.error_receipt('worker-document', 'a' * 40, failure.exception), {
                'schemaVersion':'ops.deploy-target-error.v1', 'target':'worker-document', 'sha':'a' * 40,
                'phase':'health', 'code':'TARGET_APPLY_REFUSED','rollback':'restored',
            })
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


class NativeSequenceTests(unittest.TestCase):
    def host(self, results):
        host = apply.LaunchHost(501)
        expected = iter(results)
        def command(verb, *_):
            match, status, stdout = next(expected)
            self.assertEqual(verb, match)
            return SimpleNamespace(returncode=status, stdout=stdout)
        host.ctl = command
        # Bounded fake clock path; exact launchctl reads still exercised.
        host.wait_for = lambda check: any(check() for _ in range(2))
        self.addCleanup(lambda: self.assertEqual(list(expected), []))
        return host

    def test_bootout_waits_for_label_not_just_pid(self):
        host = self.host([('print',0,'pid = 101'),('bootout',0,''),
                          ('print',0,'state = waiting'),('print',113,'')])
        host.remove('com.vinus.ngs.rs-mcp')

    def test_kickstart_race_requires_positive_pid(self):
        host = self.host([('bootstrap',0,''),('print',0,''),('kickstart',5,''),
                          ('print',0,''),('print',0,'pid = 102')])
        host.start('com.vinus.ngs.rs-mcp', Path('/not-accessed'))
        host = self.host([('bootstrap',0,''),('print',0,''),('kickstart',5,''),
                          ('print',0,''),('print',0,'')])
        with self.assertRaisesRegex(ValueError, 'TARGET_START_FAILED'):
            host.start('com.vinus.ngs.rs-mcp', Path('/not-accessed'))

    def test_other_launchctl_error_is_not_disappearance(self):
        host = self.host([('print',0,'pid = 101'),('bootout',0,''),('print',1,'')])
        with self.assertRaisesRegex(ValueError, 'TARGET_LAUNCH_STATUS_FAILED'):
            host.remove('com.vinus.ngs.rs-mcp')

    def test_diagnostic_never_exposes_error_details(self):
        r = apply.error_receipt('rs-mcp','a'*40, RuntimeError('/not-for-retention /private/path'))
        self.assertEqual(r['phase'], 'preflight')
        self.assertEqual(r['code'], 'TARGET_APPLY_REFUSED')
        self.assertEqual(r['rollback'], 'not_attempted')


if __name__ == '__main__':
    unittest.main()
