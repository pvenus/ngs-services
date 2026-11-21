"""Prepare RS-MCP only from an already verified, installed Local NGS release."""
import stat
from datetime import datetime, timezone

from mac_target_apply import TARGETS, validate_and_rewrite
from prepare_ngs_release import (
    DIGEST, REF, REPO, SHA, TAR, LIMIT, NUMBER, atomic_write, digest, json_bytes,
    json_load, matches, need, private_directory, private_file, shape, source_members,
    tree_sha,
)


def prepare_rs_relay(control, home, *, sha, expected_prior_sha256):
    need(matches(SHA, sha) and matches(DIGEST, expected_prior_sha256), 'RS_RELAY_INPUT_INVALID')
    package = control / 'mac-releases' / sha
    folder = control / 'targets' / 'rs-mcp'
    for path in (control, package.parent, package, folder.parent, folder):
        private_directory(path)
    manifest = package / 'manifest.json'
    tar = package / TAR
    runner = folder / 'run-once'
    private_file(manifest, 0o600)
    private_file(tar, 0o600)
    private_file(runner)
    attrs = runner.stat()
    need(stat.S_IMODE(attrs.st_mode) in (0o500, 0o700) and 0 < attrs.st_size <= 1024 * 1024,
         'RS_RELAY_RUNNER_UNTRUSTED')
    manifest_bytes = manifest.read_bytes()
    m = json_load(manifest_bytes)
    need(shape(m, 'schemaVersion sha repo ref workflow archiveSha256 treeSha256 expectedPriorPlistSha256')
         and m['schemaVersion'] == 'ops.ngs-mac-release.v1' and m['sha'] == sha
         and m['repo'] == REPO and m['ref'] == REF and matches(DIGEST, m['archiveSha256'])
         and matches(DIGEST, m['treeSha256']) and matches(DIGEST, m['expectedPriorPlistSha256']),
         'RS_RELAY_LOCAL_MANIFEST_INVALID')
    workflow = m['workflow']
    need(shape(workflow, 'headSha headBranch conclusion runId runAttempt artifactId')
         and workflow['headSha'] == sha and workflow['headBranch'] == 'main'
         and workflow['conclusion'] == 'success'
         and all(matches(NUMBER, workflow[k]) for k in ('runId', 'runAttempt', 'artifactId')),
         'RS_RELAY_LOCAL_PROVENANCE_INVALID')
    need(0 < tar.stat().st_size <= LIMIT, 'RS_RELAY_LOCAL_ARCHIVE_INVALID')
    raw = tar.read_bytes()
    need(digest(raw) == m['archiveSha256'], 'RS_RELAY_LOCAL_ARCHIVE_CHANGED')
    target = TARGETS['rs-mcp']
    files = source_members(raw)
    need(target.entry in files, 'RS_RELAY_ENTRY_NOT_IN_ARCHIVE')
    release = home / 'Workspaces/NGS/code/releases' / (target.release_prefix + sha)
    private_directory(release)
    need(tree_sha(release) == m['treeSha256'] and
         (release / target.entry).read_bytes() == files[target.entry], 'RS_RELAY_LOCAL_TREE_CHANGED')
    selector = home / 'Library/LaunchAgents' / (target.label + '.plist')
    private_file(selector)
    prior = selector.read_bytes()
    need(digest(prior) == expected_prior_sha256, 'RS_RELAY_PRIOR_CHANGED')
    validate_and_rewrite(prior, release, target, home / 'Workspaces')
    runner_sha = digest(runner.read_bytes())
    candidate = dict(schemaVersion='ops.mac-target-candidate.v1', target='rs-mcp', sha=sha,
                     archiveSha256=m['archiveSha256'], treeSha256=m['treeSha256'],
                     expectedPriorPlistSha256=expected_prior_sha256)
    ready = dict(schemaVersion='ops.deploy-target-ready.v1', target='rs-mcp', sha=sha,
                 verifiedAt=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                 artifactSha256=m['archiveSha256'], runnerSha256=runner_sha)

    def recheck():
        for path, mode in ((selector, None), (manifest, 0o600), (runner, None)):
            private_file(path, mode)
        need(selector.read_bytes() == prior and manifest.read_bytes() == manifest_bytes
             and digest(runner.read_bytes()) == runner_sha, 'RS_RELAY_PREPARATION_CHANGED')

    recheck()
    atomic_write(folder / 'candidate.json', json_bytes(candidate))
    recheck()
    atomic_write(folder / 'ready.json', json_bytes(ready))
    return ready
