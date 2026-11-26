import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import cleanup_expired_temporary as cleanup_module
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


class ReleaseCleanupTests(unittest.TestCase):
    def setUp(self):
        CleanupTests.setUp(self)
        self.home = self.workspace
        self.root = self.home / cleanup_module.RELEASES
        self.control = self.home / cleanup_module.CONTROL
        self.launch_agents = self.home / 'Library/LaunchAgents'
        self.launch_agents.mkdir(parents=True)
        self.root.mkdir(parents=True)
        self.control.mkdir(parents=True, mode=0o700)
        self.current, self.rollback, self.pending, self.loaded_only, self.candidate = [str(i)*40 for i in range(1, 6)]
        for name in ('prepare-ngs-local.lock', 'prepare-ops-candidates.lock', 'prepare-services.lock', 'cycle.lock'):
            (self.control / name).touch(mode=0o600)
        for sha in (self.current, self.rollback, self.pending, self.loaded_only, self.candidate):
            self.make_release(sha)
        self.brand = 'project-page-git-19d48f1'
        self.plist = self.ngs_plist(self.current, self.brand)
        (self.launch_agents / (cleanup_module.NGS_LOCAL + '.plist')).write_bytes(plistlib.dumps(self.plist))
        prior_plist_obj = self.ngs_plist(self.rollback, self.brand)
        Path(prior_plist_obj['WorkingDirectory']).mkdir(parents=True)
        prior_entry = Path(prior_plist_obj['ProgramArguments'][1])
        prior_entry.parent.mkdir(parents=True)
        prior_entry.write_text('// prior rollback fixture')
        prior_plist = plistlib.dumps(prior_plist_obj)
        self.make_journal(self.current, prior_plist)
        self.make_journal(self.rollback)
        self.make_journal(self.pending)
        self.make_journal(self.loaded_only)
        self.make_journal(self.candidate)
        self.jsonfile('prepared.json', 'ops.ngs-prepared-deploy.v1', sha=self.pending)
        self.jsonfile('ngs-local-preparation-status.json', 'ops.ngs-local-preparation-status.v1',
                      target='ngs-local', sha=self.pending, state='ready')
        for target in ('ngs-local', 'rs-mcp'):
            self.jsonfile('targets/' + target + '/ready.json', 'ops.deploy-target-ready.v1',
                          target=target, sha=self.pending)
            self.jsonfile('targets/' + target + '/last-result.json', 'ops.deploy-target-result.v1',
                          target=target, attemptedSha=self.current,
                          observed={'sha': self.current, 'deployedAt': '2026-11-01T11:00:00Z'})
        self.jsonfile('targets/rs-mcp/candidate.json', 'ops.mac-target-candidate.v1',
                      target='rs-mcp', sha=self.pending)

    def make_release(self, sha, age=20, prefix=cleanup_module.RELEASE_PREFIX):
        path = self.root / (prefix + sha)
        path.mkdir()
        payload = path / 'bundle.js'
        payload.write_bytes(b'release fixture')
        stamp = self.now - age * DAY
        os.utime(payload, ns=(stamp, stamp))
        os.utime(path, ns=(stamp, stamp))
        return path

    def jsonfile(self, path, schema, **entries):
        p = self.control / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({'schemaVersion': schema, **entries}))

    def make_journal(self, sha, prior=None):
        journal = self.control / 'mac-releases' / sha
        journal.mkdir(parents=True)
        if prior is not None:
            (journal / 'prior.plist').write_bytes(prior)
        self.jsonfile(f'mac-releases/{sha}/manifest.json', 'ops.ngs-mac-release.v1', sha=sha,
                      repo='pvenus/ngs-platform', ref='refs/heads/main', archiveSha256='c'*64,
                      treeSha256='d'*64, expectedPriorPlistSha256=hashlib.sha256(prior or b'').hexdigest())
        (journal / 'result.json').write_text(json.dumps(dict(state='deployed', target='mac', sha=sha,
            health='verified', deployedAt='2026-11-01T11:00:00Z')))

    def ngs_plist(self, sha, brand):
        return {'Label': cleanup_module.NGS_LOCAL,
                'ProgramArguments': ['/bin/node', str(self.path(sha)) + cleanup_module.LOCAL_ENTRY],
                'WorkingDirectory': str(self.root.parent.parent / 'runtime/local-miniflare-execution' / sha),
                'EnvironmentVariables': {'NGS_BRAND_MARK_PATH': str(self.root / brand / 'brand.png')}}

    def path(self, sha):
        return self.root / (cleanup_module.RELEASE_PREFIX + sha)

    def launch(self, target):
        if target.endswith('/' + cleanup_module.NGS_LOCAL):
            return ('service = {\n\tstate = running\n\tpid = 501\n\t' +
                    self.plist['ProgramArguments'][1] + '\n}\n')
        if target.endswith('/com.vinus.ngs.extra'):
            return f'service = {{\n\tstate = running\n\t{self.path(self.loaded_only)}/bundle.js\n}}\n'
        return ('domain = {\n\tservices = {\n\t501 - com.vinus.ngs.local-mcp\n'
                '\t502 - com.vinus.ngs.extra\n\t}\n}\n')

    def run_release(self, *, dry_run=False, health=True, open_check=lambda path: True):
        def launch(home, releases):
            return cleanup_module.active_releases(home, releases, launch=self.launch, health=lambda pid: health)
        return cleanup_module.cleanup_ngs(
            self.home, now_ns=self.now, dry_run=dry_run, active=launch, open_check=open_check)

    def test_old_candidate_only_and_dry_run(self):
        legacy = self.make_release('1234', prefix='project-page-old-')
        no_proof = self.make_release('f'*40)
        recent = self.make_release('e'*40, age=9)
        self.make_journal('e'*40)
        report = self.run_release(dry_run=True)
        self.assertFalse(report['blocked'])
        self.assertEqual((report['eligible'], report['removed']), (1, 0))
        self.assertTrue(self.path(self.candidate).exists())
        report = self.run_release()
        self.assertEqual(report['removed'], 1)
        self.assertFalse(self.path(self.candidate).exists())
        for sha in (self.current, self.rollback, self.loaded_only, self.pending):
            self.assertTrue(self.path(sha).exists())
        self.assertTrue(all(path.exists() for path in (legacy, no_proof, recent)))

    def test_bad_health_or_missing_rollback_blocks_all(self):
        self.assertTrue(self.run_release(health=False)['blocked'])
        (self.control / 'mac-releases' / self.current / 'prior.plist').unlink()
        self.assertTrue(self.run_release()['blocked'])
        self.assertTrue(self.path(self.candidate).exists())

    def test_recent_child_symlink_open_or_drift_blocks_removal(self):
        release = self.path(self.candidate)
        self.assertEqual(self.run_release(open_check=lambda path: False)['removed'], 0)

        def mutate(path):
            child = path / 'bundle.js'
            child.write_bytes(b'edit with same size? no')
            os.utime(child, ns=(self.now - 20*DAY, self.now - 20*DAY))
            return True
        self.assertEqual(self.run_release(open_check=mutate)['removed'], 0)
        child = release / 'bundle.js'
        os.utime(child, ns=(self.now, self.now))
        self.assertEqual(self.run_release()['removed'], 0)
        child.unlink()
        child.symlink_to(self.path(self.current) / 'bundle.js')
        old = self.now - 20*DAY
        os.utime(release, ns=(old, old))
        self.assertEqual(self.run_release()['removed'], 0)
        self.assertTrue(child.is_symlink())

    def test_deploy_or_prepare_lock_and_invalid_metadata_block_all(self):
        (self.control / 'active.json').touch()
        self.assertTrue(self.run_release()['blocked'])
        (self.control / 'active.json').unlink()
        with (self.control / 'prepare-ngs-local.lock').open('rb') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertTrue(self.run_release()['blocked'])
        (self.control / 'targets/rs-mcp/candidate.json').write_text('not json')
        self.assertTrue(self.run_release()['blocked'])
        self.assertTrue(self.path(self.candidate).exists())

    def make_legacy(self, kind, sha, age=20, **overrides):
        rel = self.root / f'{kind}-{sha[:7]}'
        child = rel / dict(cleanup_module.LEGACY_RELEASES)[kind]
        child.mkdir(parents=True)
        (child / 'bundle.js').write_bytes(b'tracked fixture')
        data = dict(commit=sha, files={'bundle.js': hashlib.sha256(b'tracked fixture').hexdigest()})
        if kind == 'runtime-space':
            data['baseCommit'] = 'a' * 40
        data.update(overrides)
        (rel / 'git-release.json').write_text(json.dumps(data))
        stamp = self.now - age * DAY
        for p in [child / 'bundle.js', rel / 'git-release.json', child, rel]:
            os.utime(p, ns=(stamp, stamp))
        return rel

    def setup_legacy(self):
        target = self.control / 'targets/ngs-space'
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
        (target / 'publish-queue.lock').touch(mode=0o600)
        self.jsonfile('targets/ngs-space/publication.json', 'ops.ngs-space-publication.v1',
                      provider='chatgpt-sites', projectId='appgprj_test')
        self.space_commit = '8' * 40
        self.space_provider = 'b' * 40
        self.space_result = dict(commitSha=self.space_commit, providerSha=self.space_provider,
                                 state='succeeded', versionId='appgprj_test~version',
                                 deployedAt='2026-11-01T11:00:00Z')
        self.jsonfile('targets/ngs-space/publish-queue.json', 'ops.space-publish-queue.v1',
                      items=[dict(state='succeeded', commitSha=self.space_commit, result=self.space_result)])
        self.legacy_backup = self.make_legacy('runtime-space', 'e' * 40, age=12)
        self.source_backup = self.make_legacy('source-git', 'e' * 40, age=11)
        self.runtime_backup = self.make_legacy('runtime-git', 'e' * 40, age=12)

    def run_legacy(self, *, dry_run=False, open_paths=lambda path: set()):
        def active(home, releases):
            return cleanup_module.active_releases(home, releases, launch=self.launch, health=lambda pid: True)
        return cleanup_module.cleanup_legacy_ngs(self.home, now_ns=self.now, dry_run=dry_run,
                                                active=active, open_paths=open_paths)

    def test_legacy_scope_and_live_last_good_startup_refs(self):
        self.setup_legacy()
        eligible = [self.make_legacy(prefix, 'a'*40)
                    for prefix in ('runtime-space', 'runtime-git', 'source-git')]
        pinned = [self.make_legacy('runtime-space', self.space_commit),
                  self.make_legacy('runtime-space', self.space_provider),
                  self.make_legacy('source-git', '9'*40),
                  self.make_legacy('runtime-git', 'c'*40)]
        (self.home / '.zprofile').write_text('export RUNTIME="$HOME/some/path/' + pinned[2].name + '"')
        unknown = self.make_legacy('source-git', '0'*40, commit='1'*40)
        report = self.run_legacy(dry_run=True, open_paths=lambda path: {pinned[3].name})
        self.assertEqual((report['blocked'], report['eligible'], report['removed']), (False, 3, 0))
        self.assertTrue(all(p.exists() for p in eligible))
        self.assertEqual(self.run_legacy(open_paths=lambda path: {pinned[3].name})['removed'], 3)
        self.assertTrue(all(not p.exists() for p in eligible))
        self.assertTrue(all(p.exists() for p in (*pinned, unknown, self.legacy_backup,
                                                  self.source_backup, self.runtime_backup)))

    def test_legacy_protects_busy_space_and_files_opening_during_probe(self):
        self.setup_legacy()
        candidate = self.make_legacy('runtime-space', 'a'*40)
        self.jsonfile('targets/ngs-space/publish-queue.json', 'ops.space-publish-queue.v1',
                      items=[dict(state='claimed', commitSha=self.space_commit)])
        self.assertTrue(self.run_legacy()['blocked'])
        self.assertTrue(candidate.exists())
        self.jsonfile('targets/ngs-space/publish-queue.json', 'ops.space-publish-queue.v1',
                      items=[dict(state='succeeded', commitSha=self.space_commit, result=self.space_result)])
        paths = iter([set(), {candidate.name}])
        self.assertEqual(self.run_legacy(open_paths=lambda path: next(paths))['removed'], 0)
        self.assertTrue(candidate.exists())

    def test_legacy_batch_is_bounded(self):
        self.setup_legacy()
        for index in range(cleanup_module.BATCH_COUNT + 3):
            self.make_legacy('runtime-space', f'{index:07x}' + '1'*33)
        report = self.run_legacy(dry_run=True)
        self.assertEqual(report['eligible'], cleanup_module.BATCH_COUNT)
        self.assertEqual(report['deferred'], 3)
        self.assertEqual(report['removed'], 0)

    def test_manual_drain_rechecks_and_aggregates_actual_removals(self):
        def row(removed, deferred, blocked=False):
            return dict(removed=removed, eligible=removed, skipped=0, allocatedBytes=removed * 4096,
                        errors=0, blocked=blocked, deferred=deferred, scanned=removed,
                        eligibleAllocatedBytes=removed * 4096)
        pending = iter([row(8, 10), row(8, 2), row(0, 2, blocked=True)])
        checks = []
        def policy(home, *, dry_run):
            self.assertEqual(home, self.home)
            self.assertFalse(dry_run)
            checks.append(True)
            return next(pending)
        report = cleanup_module.drain_legacy_backlog(self.home, policy=policy, pause=lambda sec: None,
                                                    monotonic=lambda: 0)
        self.assertEqual((len(checks), report['batches'], report['removed'], report['allocatedBytes']),
                         (3, 3, 16, 16 * 4096))
        self.assertEqual(report['deferred'], 2)
        self.assertTrue(report['blocked'])
