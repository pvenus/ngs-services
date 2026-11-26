import fcntl
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cleanup_expired_temporary import (
    NANOSECONDS_PER_DAY as DAY, RULES, Rule, cleanup, no_open_descriptors,
)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name).resolve()
        self.now = 1_700_000_000 * 1_000_000_000

    def item(self, rule, name, age, directory=False):
        p = self.workspace / rule.directory / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if directory:
            p.mkdir()
        else:
            p.write_bytes(b'fixture')
        stamp = self.now - age * DAY
        os.utime(p, ns=(stamp, stamp))
        return p

    def scan(self, *, dry_run=False, open_check=lambda path: True, rules=RULES):
        return cleanup(self.workspace, rules=rules, now_ns=self.now, dry_run=dry_run, open_check=open_check)

    def test_exact_age_allowed_names_and_dry_run(self):
        wan, ace = RULES
        expired = (
            self.item(wan, 'wan.safetensors.part', 10),
            self.item(ace, 'model.safetensors.hash.incomplete', 12),
        )
        keep = (
            self.item(wan, 'wan-final.safetensors', 20),
            self.item(wan, 'young.part', 9),
            self.item(ace, 'no-etag.incomplete', 20),
            self.item(ace, 'young.hash.incomplete', 9),
        )
        nested_other = self.item(Rule('unmatched', wan.directory + '/child', '.part'), 'not-in-scope.part', 30)
        self.assertEqual(self.scan(dry_run=True)[wan.name]['eligible'], 1)
        self.assertTrue(all(p.exists() for p in expired))
        report = self.scan()
        self.assertEqual([report[r.name]['removed'] for r in RULES], [1, 1])
        self.assertTrue(all(not p.exists() for p in expired))
        self.assertTrue(all(p.exists() for p in (*keep, nested_other)))

    def test_no_symlinks_hardlinks_or_root_symlinks(self):
        (self.workspace / 'elsewhere').mkdir()
        target = self.workspace / 'elsewhere' / 'model'
        target.write_bytes(b'keep')
        wan = RULES[0]
        link = self.item(wan, 'symlink.part', 30)
        link.unlink()
        link.symlink_to(target)
        hard = self.item(wan, 'hard.part', 30)
        hard.unlink()
        os.link(target, hard)
        os.utime(hard, ns=(self.now - DAY * 30, self.now - DAY * 30))
        root = self.workspace / 'linked'
        root.symlink_to(link.parent, target_is_directory=True)
        report = self.scan(rules=(wan, Rule('link-rule', 'linked', '.part')))
        self.assertEqual(report[wan.name]['skipped'], 2)
        self.assertEqual(report['link-rule']['errors'], 1)
        self.assertTrue(target.exists() and link.is_symlink() and hard.exists())

    def test_skip_open_changed_and_producer_lock(self):
        wan, ace = RULES
        opened = self.item(wan, 'opened.part', 20)
        touched = self.item(wan, 'touched.part', 20)
        active = self.item(ace, 'busy.hash.incomplete', 20)
        emerging = self.item(ace, 'emerging.hash.incomplete', 20)
        lock = active.with_name('busy.lock')
        lock.touch()
        def check(path):
            if path == touched:
                path.write_bytes(b'producer resumed download')
            if path == emerging:
                path.with_name('emerging.lock').touch()
            return path != opened
        with lock.open('rb') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            report = self.scan(open_check=check)
        self.assertEqual([report[r.name]['skipped'] for r in RULES], [2, 2])
        self.assertTrue(all(p.exists() for p in (opened, touched, active, emerging)))

    def test_empty_dir_rule_never_recursively_deletes(self):
        rule = Rule('empty-scratch', 'scratch', '.tmp', 'empty-directory')
        empty = self.item(rule, 'empty.tmp', 10, directory=True)
        nonempty = self.item(rule, 'nonempty.tmp', 11, directory=True)
        (nonempty / 'keep').write_text('keep')
        stamp = self.now - DAY * 11
        os.utime(nonempty, ns=(stamp, stamp))
        other = self.item(rule, 'not-matched', 30, directory=True)
        report = self.scan(rules=(rule,))[rule.name]
        self.assertEqual((report['removed'], report['skipped']), (1, 1))
        self.assertFalse(empty.exists())
        self.assertTrue((nonempty / 'keep').exists() and other.exists())

    def test_lsof_clear_busy_and_uncertain_diverge(self):
        def completed(code, out=b'', err=b''):
            return subprocess.CompletedProcess([], code, out, err)
        for return_value, expected in [(completed(1), True), (completed(0, b'p123'), False),
                                       (completed(1, err=b'cannot inspect'), False), (completed(127), False)]:
            with patch('cleanup_expired_temporary.subprocess.run', return_value=return_value):
                self.assertEqual(no_open_descriptors(Path('/fixture/would-delete.part')), expected)
        with patch('cleanup_expired_temporary.subprocess.run', side_effect=subprocess.TimeoutExpired([], 8)):
            self.assertFalse(no_open_descriptors(Path('/fixture/would-delete.part')))

    def test_launchagent_is_daily_and_does_not_run_at_load(self):
        path = Path(__file__).with_name('ops-temp-cleanup.launchagent.template.plist')
        template = plistlib.loads(path.read_bytes())
        self.assertEqual(template['Label'], 'com.vinus.ops.temp-cleanup')
        self.assertEqual(template['StartCalendarInterval'], {'Hour': 4, 'Minute': 0})
        self.assertFalse(template['RunAtLoad'])
        self.assertFalse(template['KeepAlive'])
        self.assertNotIn('StartInterval', template)
        self.assertNotIn('--dry-run', template['ProgramArguments'])
