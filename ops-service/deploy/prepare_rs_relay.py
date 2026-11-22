"""Prepare RS-MCP only from an already verified, installed Local NGS release."""
import stat
import plistlib
from pathlib import Path
from datetime import datetime, timezone

from mac_target_apply import TARGETS, validate_and_rewrite
from prepare_ngs_release import (
    DIGEST, REF, REPO, SHA, TAR, LIMIT, NUMBER, atomic_write, digest, json_bytes,
    json_load, matches, need, private_directory, private_file, shape, source_members,
    tree_sha,
)


RELAY_CLOSURE = tuple('sites/npcgen/build/' + n for n in (
    'rs-mcp.mjs', 'rs-ops-ngs-health.mjs', 'local-ops-health-report.mjs',
    'folder-mcp-loopback.mjs', 'space-machine-mcp.mjs', 'file-sync-raw-client.mjs'))


def _equivalent(files, prior, folder, sha, ready, prior_digest):
    # Compare the fixed entry (and all of its current relative imports) byte
    # for byte. A future changed importer fails this no-op check, not execution.
    v = plistlib.loads(prior)
    old = Path(v['WorkingDirectory'])
    suffix = old.name.removeprefix(TARGETS['rs-mcp'].release_prefix)
    rows = [_read_existing(folder/'observed.json'), (_read_existing(folder/'last-result.json') or {}).get('observed')]
    rows = [row for row in rows if isinstance(row,dict) and set(row)=={'sha','deployedAt'}
            and matches(SHA,row['sha']) and isinstance(row['deployedAt'],str)]
    observed = max(rows,key=lambda r:r['deployedAt'],default=None)
    if observed is None or observed['sha'] == sha or not observed['sha'].startswith(suffix):
        return None
    try:
        for n in RELAY_CLOSURE:
            if n not in files:
                return None
            private_file(old/n)
            if (old/n).read_bytes() != files[n]:
                return None
    except OSError:
        return None
    return {'schemaVersion':'ops.deploy-target-equivalent.v1','target':'rs-mcp','sha':sha,
            'observedSha':observed['sha'],'artifactSha256':ready['artifactSha256'],
            'runnerSha256':ready['runnerSha256'],'selectorSha256':prior_digest,
            'runtimeTreeSha256':digest(json_bytes({n:digest(files[n]) for n in RELAY_CLOSURE})),
            'verifiedAt':ready['verifiedAt']}


def _read_existing(path):
    try:
        private_file(path, 0o600)
    except FileNotFoundError:
        return None
    need(0 < path.stat().st_size <= 4096, 'RS_RELAY_PREPARATION_CHANGED')
    return json_load(path.read_bytes())


def prepare_rs_relay(control, home, *, sha, expected_prior_sha256, expected_runner_sha256=None, recheck=None, manual_only=False, report_equivalence=False):
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
    if expected_runner_sha256 is not None:
        need(runner_sha == expected_runner_sha256, 'RS_RELAY_RUNNER_CHANGED')
    freshness = recheck
    candidate = dict(schemaVersion='ops.mac-target-candidate.v1', target='rs-mcp', sha=sha,
                     archiveSha256=m['archiveSha256'], treeSha256=m['treeSha256'],
                     expectedPriorPlistSha256=expected_prior_sha256)
    ready = dict(schemaVersion='ops.deploy-target-ready.v1', target='rs-mcp', sha=sha,
                 verifiedAt=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                 artifactSha256=m['archiveSha256'], runnerSha256=runner_sha)
    if manual_only is True:
        ready['manualOnly'] = True

    def verify_current():
        if freshness is not None:
            freshness()
        for path, mode in ((selector, None), (manifest, 0o600), (runner, None)):
            private_file(path, mode)
        need(selector.read_bytes() == prior and manifest.read_bytes() == manifest_bytes
             and digest(runner.read_bytes()) == runner_sha, 'RS_RELAY_PREPARATION_CHANGED')

    verify_current()
    equivalent = _equivalent(files, prior, folder, sha, ready, expected_prior_sha256) if report_equivalence else None
    if (old := _read_existing(folder / 'ready.json')) is not None and all(
        old.get(k) == v for k, v in ready.items() if k != 'verifiedAt'
    ):
        need(_read_existing(folder / 'candidate.json') == candidate, 'RS_RELAY_PREPARATION_CHANGED')
        if equivalent is not None:
            old_eq = _read_existing(folder/'equivalent.json')
            if not old_eq or any(old_eq.get(k) != v for k,v in equivalent.items() if k != 'verifiedAt'):
                atomic_write(folder/'equivalent.json',json_bytes(equivalent))
        return (old, equivalent is not None) if report_equivalence else old
    atomic_write(folder / 'candidate.json', json_bytes(candidate))
    verify_current()
    if equivalent is not None:
        atomic_write(folder/'equivalent.json',json_bytes(equivalent))
    atomic_write(folder / 'ready.json', json_bytes(ready))
    return (ready, equivalent is not None) if report_equivalence else ready
