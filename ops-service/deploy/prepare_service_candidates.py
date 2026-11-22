"""Prepare fixed Mac service candidates; never execute a release or change launchd."""
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
import plistlib
import re
import stat
import subprocess

from mac_target_apply import LaunchHost, TARGETS, validate_and_rewrite
from planner_cutover import REVISION, loaded_selector, planner_identity
from prepare_ngs_local_once import lock, read_optional, read_private, runner_digest
from prepare_ngs_release import DIGEST, SHA, atomic_write, digest, json_bytes, matches, need, private_directory, private_file, tree_sha
from prepare_rs_relay import prepare_rs_relay
from prepare_service_targets import (SERVICES, assemble_source, request_candidate, tracked_targets,
                                     verify_equivalent, equivalent_record)

ORDER = ('request-service', 'workflow-planner', 'backend-stack')
READY = 'ops.deploy-target-ready.v1'
CANDIDATE = 'ops.mac-target-candidate.v1'
NODE = '.nvm/versions/node/v24.21.0/bin/node'
PLAN_ABI = tuple('workflow-planner/'+n for n in ('package.json', 'cli.mjs', 'planner.mjs', 'audio-task-schema.mjs'))
# These routes/transport carry the paired PDS/Filex ABI. A changed required writer
# must be active before exposing the independently clickable Backend Stack action.
FILEX_ABI = tuple('data-service/project_data_service/'+n for n in (
    'workflow_audio.py', 'workflow_audio_delete.py', 'filex_cas_transport.py', 'concept_filex.py'))
DEPENDENCY_NAMES = {'package.json', 'package-lock.json', 'pyproject.toml', 'requirements.txt', 'database.py'}
CODE = re.compile(r'[A-Z][A-Z0-9_]{3,62}')


def outcome(target, sha, state, code=None):
    record = {'target': target, 'sha': sha, 'state': state}
    if code is not None:
        record['code'] = code
    return record


def blocked(target, head, exc):
    code = str(exc)
    if not (isinstance(exc, ValueError) and CODE.fullmatch(code)):
        code = 'SERVICE_PREP_REFUSED'
    return outcome(target, head, 'pending' if code in (
        'PREP_LOCK_BUSY', 'PREP_ACTIVE_DEPLOYMENT', 'MAIN_ADVANCED', 'FILEX_COMPANION_NOT_ACTIVE',
        'SERVICE_LOCAL_ARTIFACT_UNAVAILABLE') else 'blocked', code)


def read_selector(home, target, host):
    cat = TARGETS[target]
    path = home / 'Library/LaunchAgents' / (cat.label + '.plist')
    raw = read_private(path, 128 * 1024)
    v = plistlib.loads(raw)
    args, wd = v.get('ProgramArguments'), v.get('WorkingDirectory')
    need(v.get('Label') == cat.label and type(args) is list and len(args) >= 2
         and all(type(a) is str for a in args) and (wd is None or type(wd) is str),
         'SERVICE_SELECTOR_INVALID')
    correct_python = target == 'backend-stack' and args[0] == '/opt/homebrew/bin/python3.12'
    correct_node = target != 'backend-stack' and args[0] == str(home / NODE)
    need(correct_python or correct_node, 'SERVICE_RUNTIME_NOT_PINNED')
    pid, report = host.launch_report(cat.label)
    if wd is not None:
        live = loaded_selector(report, args, wd)
    else:
        bits = report.splitlines()
        start = bits.index('\targuments = {') + 1 if '\targuments = {' in bits else 0
        end = bits.index('\t}', start) if '\t}' in bits[start:] else 0
        live = not any(x.startswith('\tworking directory = ') for x in bits) and start > 0 and end > start and bits[start:end] == ['\t\t'+a for a in args]
    need(bool(pid) and live, 'SERVICE_PRIOR_SELECTOR_NOT_LOADED')
    return path, raw, v


def runtime_matches(prior, candidate, files):
    for name in files:
        a, b = prior / name, candidate / name
        private_file(a)
        private_file(b)
        if a.read_bytes() != b.read_bytes():
            return False
    return True


def prior_root(base, prior, cat):
    path = Path(prior['ProgramArguments'][1])
    parent = base / cat.workspace / 'code/releases'
    tail = path.relative_to(parent).parts if path.is_relative_to(parent) else ()
    need(len(tail) >= 2 and '/'.join(tail[1:]) == cat.entry
         and re.fullmatch(re.escape(cat.release_prefix)+r'[a-f0-9]{7,40}', tail[0]), 'SERVICE_SELECTOR_ENTRY')
    root = parent / tail[0]
    private_directory(root)
    return root


def verify_source(release, scopes):
    private_directory(release)
    expected = {name: spec for scope in scopes.values() for name, spec in scope.items()}
    seen = set()
    for f in release.rglob('*'):
        st = f.lstat()
        if stat.S_ISDIR(st.st_mode):
            private_directory(f)
            continue
        mode = stat.S_IMODE(st.st_mode)
        n = f.relative_to(release).as_posix()
        need(n in expected and mode == (0o700 if expected[n][1] & 0o100 else 0o600),
             'SERVICE_RELEASE_SOURCE_SCOPE')
        raw = read_private(f, 8 * 1024 * 1024, mode=mode) if st.st_size else b''
        private_file(f, mode)
        oid = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        need(oid == expected[n][0], 'SERVICE_RELEASE_SOURCE_DRIFT')
        seen.add(n)
    need(seen == expected.keys(), 'SERVICE_RELEASE_SOURCE_MISSING')
    return tree_sha(release)


def gate(target, value, replacement, home, old, release, scope, head, filex_ready):
    if target == 'workflow-planner':
        # Capture existing database identity by stat only. Do not open SQLite
        # or execute a candidate schema/startup module during preparation.
        need(runtime_matches(old, release, PLAN_ABI), 'PLANNER_STARTUP_OR_STORAGE_ABI_CHANGED')
        proposed = plistlib.loads(replacement)
        proposed.setdefault('EnvironmentVariables', {})[REVISION] = head
        need(planner_identity(value, home)[2] == planner_identity(proposed, home)[2],
             'TARGET_PLANNER_DATABASE_IDENTITY')
    elif target == 'backend-stack':
        storage = home / 'Workspaces/PDS/data'
        need(value['ProgramArguments'][2:] == ['--storage-root', str(storage)]
             and storage.resolve(strict=True) == storage and storage.is_dir(), 'BACKEND_STORAGE_UNVERIFIED')
        dependency_paths = [n for n in scope if Path(n).name in DEPENDENCY_NAMES or n.endswith('.sql')]
        need(runtime_matches(old, release, ('runtime/stack.py', *dependency_paths)), 'BACKEND_STARTUP_OR_STORAGE_ABI_CHANGED')
        if not runtime_matches(old, release, FILEX_ABI):
            need(filex_ready(head) is True, 'FILEX_COMPANION_NOT_ACTIVE')


def equivalence(verified, repo, target, folder, old, selector, expected):
    # Only real prior observations can be marked equivalent. An unobserved,
    # short release path is not expanded into a guessed deployment revision.
    initial = read_optional(folder / 'observed.json')
    terminal = read_optional(folder / 'last-result.json')
    actual = terminal and terminal.get('observed')
    rows = [r for r in (initial, actual) if type(r) is dict and set(r) == {'sha','deployedAt'}
            and matches(SHA, r['sha']) and isinstance(r['deployedAt'],str)]
    newest = max(rows, key=lambda row:row['deployedAt'], default=None)
    if not newest or newest['sha'] == verified['proof']['headSha']:
        return None
    suffix = old.name.removeprefix(TARGETS[target].release_prefix)
    if not newest['sha'].startswith(suffix):
        return None
    try:
        runtime = verify_equivalent(verified, repo, target, old)
    except (OSError, ValueError):
        return None  # Unknown or changed source remains separately actionable.
    return equivalent_record(verified, target, newest['sha'], folder/'run-once', selector, expected, runtime)


def register(target, head, candidate, home, control, release, prior, runner, host, assert_current,
             readiness_guard=lambda: None, equivalent=None):
    f = control / 'targets' / target
    with lock(control / 'cycle.lock', create=True):
        need(not os.path.lexists(control / 'active.json'), 'PREP_ACTIVE_DEPLOYMENT')
        assert_current(head)
        need(read_selector(home, target, host)[1] == prior, 'SERVICE_SELECTOR_CHANGED')
        need(runner_digest(f) == runner, 'SERVICE_RUNNER_CHANGED')
        need(tree_sha(release) == candidate['treeSha256'], 'SERVICE_RELEASE_SOURCE_DRIFT')
        readiness_guard()
        ready = dict(schemaVersion=READY, target=target, sha=head,
                     artifactSha256=candidate['archiveSha256'], runnerSha256=runner, manualOnly=True)
        old_ready = read_optional(f / 'ready.json')
        state = read_optional(f / 'last-result.json')
        if (state and state.get('attemptedSha') == head and state.get('state') == 'failed'):
            return outcome(target, head, 'blocked', 'SERVICE_RECONCILE_REQUIRED')
        if old_ready and all(old_ready.get(k) == v for k, v in ready.items()):
            need(read_optional(f / 'candidate.json') == candidate, 'SERVICE_RECONCILE_REQUIRED')
            if equivalent is not None:
                observed = read_optional(f / 'equivalent.json')
                # No same-SHA rewrite just to refresh verification timestamps.
                if not observed or any(observed.get(k) != v for k,v in equivalent.items() if k != 'verifiedAt'):
                    atomic_write(f / 'equivalent.json', json_bytes(equivalent))
                return outcome(target, head, 'current')
            return outcome(target, head, 'ready')
        ready['verifiedAt'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        # Button and automatic coordinator serialize on cycle.lock. Verify no
        # race before making the candidate visible to the button.
        atomic_write(f / 'candidate.json', json_bytes(candidate))
        assert_current(head)
        need(read_selector(home, target, host)[1] == prior and runner_digest(f) == runner,
             'SERVICE_PREPARATION_CHANGED')
        readiness_guard()
        if equivalent is not None:
            atomic_write(f / 'equivalent.json', json_bytes(equivalent))
        atomic_write(f / 'ready.json', json_bytes(ready))
        return outcome(target, head, 'current' if equivalent is not None else 'registered')


def prepare_service_candidates(verified, repo, home, control, *, assert_current,
                               filex_ready=lambda head: False, host=None):
    """Stage from the verified canonical CI evidence returned by verify_target_artifact.

    Dependency callback must attest required Filex ABI is active, not only staged.
    This function does not handle auth or invoke the apply adapter.
    """
    p = verified['proof']
    h = p['headSha']
    need(p['repository'] == SERVICES and matches(SHA, h) and matches(DIGEST, verified['artifactSha256']),
         'SERVICE_CANDIDATE_INVALID')
    for f in (control, control/'targets'):
        private_directory(f)
    host = LaunchHost(os.getuid()) if host is None else host
    base = home / 'Workspaces'
    parent = base / 'BackendServices/code/releases'
    release = parent / (TARGETS['backend-stack'].release_prefix + h)
    outcomes = {}
    with lock(control / 'prepare-services.lock', create=True):
        assert_current(h)
        scopes = tracked_targets(verified, repo)
        if not os.path.lexists(release):
            need(assemble_source(verified, repo, parent, public_source_parent=True) == release, 'SERVICE_RELEASE_PATH')
        tree = verify_source(release, scopes)
        for t in ORDER:
            try:
                f = control / 'targets' / t
                private_directory(f)
                runner = runner_digest(f)
                cat = TARGETS[t]
                sel, raw, value = read_selector(home, t, host)
                old = prior_root(base, value, cat)
                if old == release:
                    outcomes[t] = outcome(t, h, 'current')
                    continue
                proposed = validate_and_rewrite(raw, release, cat, base)
                guard = lambda: gate(t, value, proposed, home, old, release, scopes[t], h, filex_ready)
                guard()
                if t == 'request-service':
                    c = request_candidate(verified, repo, release, home, digest(raw))
                    need(c['treeSha256'] == tree, 'SERVICE_RELEASE_SOURCE_DRIFT')
                else:
                    c = dict(schemaVersion=CANDIDATE, target=t, sha=h, archiveSha256=verified['artifactSha256'],
                             treeSha256=tree, expectedPriorPlistSha256=digest(raw))
                equiv = equivalence(verified, repo, t, f, old, sel, digest(raw))
                outcomes[t] = register(t, h, c, home, control, release, raw, runner, host, assert_current, guard, equiv)
            except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as e:
                outcomes[t] = blocked(t, h, e)
    return outcomes


def prepare_rs_mcp_candidate(control, home, sha, *, assert_current, host=None):
    """Use only the existing Local NGS verified/prepared artifact receipt."""
    need(matches(SHA, sha), 'SERVICE_CANDIDATE_INVALID')
    target = 'rs-mcp'
    for f in (control, control/'targets', control/'targets'/target):
        private_directory(f)
    host = LaunchHost(os.getuid()) if host is None else host
    try:
        with lock(control / 'cycle.lock', create=True):
            need(not os.path.lexists(control / 'active.json'), 'PREP_ACTIVE_DEPLOYMENT')
            assert_current(sha)
            _, raw, value = read_selector(home, target, host)
            old = prior_root(home/'Workspaces', value, TARGETS[target])
            if old.name == TARGETS[target].release_prefix + sha:
                return outcome(target, sha, 'current')
            f = control/'targets'/target
            runner = runner_digest(f)
            receipt = read_optional(f/'last-result.json')
            if receipt and receipt.get('attemptedSha') == sha and receipt.get('state') == 'failed':
                return outcome(target, sha, 'blocked', 'SERVICE_RECONCILE_REQUIRED')
            if not os.path.lexists(control/'mac-releases'/sha/'manifest.json'):
                return outcome(target, sha, 'pending', 'SERVICE_LOCAL_ARTIFACT_UNAVAILABLE')
            _, equivalent = prepare_rs_relay(
                control, home, sha=sha, expected_prior_sha256=digest(raw), expected_runner_sha256=runner, manual_only=True, report_equivalence=True,
                recheck=lambda: (assert_current(sha), need(read_selector(home, target, host)[1] == raw,
                                                        'SERVICE_SELECTOR_CHANGED'))
            )
            return outcome(target, sha, 'current' if equivalent else 'registered')
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as e:
        return blocked(target, sha, e)
