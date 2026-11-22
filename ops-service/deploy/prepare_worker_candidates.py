"""Prepare and register official Worker candidates; never invoke a worker or apply runner."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import plistlib
import stat
import re
import subprocess

from mac_target_apply import TARGETS, validate_and_rewrite
from prepare_ngs_local_once import lock, read_private
from prepare_ngs_release import (Refused, atomic_write, digest, json_bytes, json_load, need,
                                 private_directory, private_file, tree_sha)
from prepare_service_targets import (SPECS, WORKERS, assemble_source, git, prepare_audio_candidate,
                                     tracked_targets, tracked_tree, verify_target_artifact)

TARGET_IDS = tuple(SPECS[WORKERS])
READY = 'ops.deploy-target-ready.v1'
CANDIDATE = 'ops.mac-target-candidate.v1'
TERMINAL = 'ops.deploy-target-result.v1'
MAX_CONTROL = 128 * 1024
RETRY_TARGET = 'worker-image'
RETRY_READY = 'ops.worker-manual-retry-ready.v1'
DECISION = 'ops.worker-source-decision.v1'
SHA = re.compile(r'[a-f0-9]{40}')
STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z')
# Trusted production adapters are read and compared, never imported from the
# live target directories or executed by this module.
SUPPORT = ('prepare_ngs_release.py', 'planner_cutover.py', 'audio_cutover.py')


def _optional(path, limit=4096):
    try:
        return json_load(read_private(path, limit, mode=0o600))
    except FileNotFoundError:
        return None


def _exact_source(release, tracked):
    private_directory(release)
    expected = {}
    dirs = set()
    for files in tracked.values():
        for name, (oid, mode) in files.items():
            relative = Path(name).relative_to('workflow-production')
            expected[relative] = (oid, 0o700 if mode & 0o100 else 0o600)
            dirs.update(p for p in relative.parents if p != Path('.'))
    found, found_dirs = set(), set()
    for path in release.rglob('*'):
        rel = path.relative_to(release)
        attrs = path.lstat()
        if stat.S_ISDIR(attrs.st_mode):
            private_directory(path)
            found_dirs.add(rel)
            continue
        need(rel in expected, 'WORKER_RELEASE_EXTRA_FILE')
        oid, mode = expected[rel]
        data = read_private(path, 8 * 1024 * 1024, mode=mode) if attrs.st_size else _empty(path, mode)
        need(hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest() == oid,
             'WORKER_RELEASE_SOURCE_DRIFT')
        found.add(rel)
    need(found == set(expected) and found_dirs == dirs, 'WORKER_RELEASE_SOURCE_INCOMPLETE')
    return tree_sha(release)


def _empty(path, mode):
    private_file(path, mode)
    need(path.stat().st_size == 0, 'WORKER_RELEASE_SOURCE_DRIFT')
    return b''


def _runner(target, target_dir, trusted_deploy):
    # Imported support modules are pinned to the bytes of the fixed installed
    # trust bundle, not to the new Worker release or GitHub artifact.
    runner = target_dir/'run-once'
    private_file(runner)
    need(stat.S_IMODE(runner.lstat().st_mode) in (0o500, 0o700), 'WORKER_RUNNER_UNVERIFIED')
    actual = read_private(runner, MAX_CONTROL)
    need(actual == read_private(trusted_deploy/'mac_target_apply.py', MAX_CONTROL),
         'WORKER_RUNNER_UNVERIFIED')
    names = SUPPORT if target == 'worker-audio' else SUPPORT[:2]
    support = []
    for name in names:
        expected = read_private(trusted_deploy/name, MAX_CONTROL)
        installed = read_private(target_dir/name, MAX_CONTROL)
        need(installed == expected, 'WORKER_RUNNER_DEPENDENCY_UNVERIFIED')
        support.append(digest(installed))
    return digest(actual), tuple(support)


def _selector(target, home, release):
    catalog = TARGETS[target]
    selector = home/'Library/LaunchAgents'/(catalog.label+'.plist')
    prior = read_private(selector, MAX_CONTROL)
    current = plistlib.loads(prior)
    args = current['ProgramArguments']
    need(type(args) is list and len(args) >= 2 and all(type(a) is str for a in args), 'WORKER_SELECTOR_INVALID')
    old_entry = Path(args[1])
    old = old_entry.parents[len(Path(catalog.entry).parts)-1]
    need(old_entry==old/catalog.entry and current.get('Label')==catalog.label and
         current.get('WorkingDirectory')==str(old_entry.parent),'WORKER_SELECTOR_INVALID')
    active_suffix=old.name.removeprefix('workflows-github-')
    if (old.parent==release.parent and old.name.startswith('workflows-github-')
            and re.fullmatch(r'[a-f0-9]{7,40}',active_suffix)
            and release.name.removeprefix('workflows-github-').startswith(active_suffix)):
        # Historical seven-character names share a literal prefix with the
        # full immutable release, but this is an observation, never a rewrite.
        return prior, current, old, None
    proposed = plistlib.loads(validate_and_rewrite(prior, release, catalog, home/'Workspaces'))
    return prior, current, old, proposed


def _abs(value):
    need(type(value) is str and value.startswith('/'), 'WORKER_RUNTIME_PATH_INVALID')
    return Path(value)


def _accessible(value, *, directory=False, executable=False):
    path = _abs(value)
    stat_ = path.stat()
    need((stat.S_ISDIR(stat_.st_mode) if directory else stat.S_ISREG(stat_.st_mode))
         and (not executable or os.access(path, os.X_OK)), 'WORKER_RUNTIME_DEPENDENCY_UNVERIFIED')
    return path


def verify_worker_runtime(target, proposed):
    """Cold read-only selector dependencies; never run native/provider programs."""
    args = proposed['ProgramArguments']
    env = proposed.get('EnvironmentVariables', {})
    need(type(env) is dict, 'WORKER_SELECTOR_INVALID')
    _accessible(args[0], executable=True)
    need(args.count('--db') == 1 and args.index('--db') + 1 < len(args), 'WORKER_DATABASE_UNVERIFIED')
    db = _accessible(args[args.index('--db')+1])
    private_file(db, 0o600)
    if target == 'worker-image':
        _accessible('/opt/homebrew/bin/python3.12', executable=True)
        config = _abs(env.get('WORKFLOW_IMAGE_HOST_CONFIG'))
        pin = env.get('WORKFLOW_IMAGE_HOST_CONFIG_SHA256')
        need(digest(read_private(config, MAX_CONTROL, mode=0o600)) == pin,
             'WORKER_IMAGE_CONFIG_UNVERIFIED')
    elif target == 'worker-animation':
        _accessible(env.get('WORKFLOW_WAN_RELEASE'), directory=True)
        _accessible(env.get('WORKFLOW_WAN_PYTHON'), executable=True)
        read_private(_abs(env.get('WORKFLOW_WAN_BINDING')), MAX_CONTROL, mode=0o600)
    elif target == 'worker-postprocess':
        for flag, directory in (('--worker-source', True), ('--sam-source', True),
                                ('--config', False), ('--model-pins', False)):
            need(args.count(flag) == 1 and args.index(flag)+1 < len(args), 'WORKER_RUNTIME_PATH_INVALID')
            qualified = _accessible(args[args.index(flag)+1], directory=directory)
            if not directory:
                read_private(qualified, MAX_CONTROL)


def _prior_attempt(target_dir, target, head):
    try:
        raw=read_private(target_dir/'last-result.json',4096,mode=0o600)
    except FileNotFoundError:
        return None
    last=json_load(raw)
    need(type(last) is dict and last.get('schemaVersion') == TERMINAL and last.get('target') == target
         and type(last.get('attemptedSha')) is str and last.get('state') in ('failed','deployed'),
         'WORKER_OUTCOME_UNVERIFIED')
    return (last,digest(raw)) if last['attemptedSha']==head and last['state']=='failed' else None


def _observation(directory,target,old):
    last=_optional(directory/'last-result.json')
    initial=_optional(directory/'observed.json')
    rows=[]
    for item in (initial,last.get('observed') if last else None):
        if item is None:continue
        need(type(item) is dict and set(item)=={'sha','deployedAt'} and type(item['sha']) is str
             and SHA.fullmatch(item['sha']) and type(item['deployedAt']) is str and STAMP.fullmatch(item['deployedAt']),
             'WORKER_ACTIVE_PROOF_UNAVAILABLE')
        datetime.fromisoformat(item['deployedAt'])
        rows.append(item)
    need(bool(rows),'WORKER_ACTIVE_PROOF_UNAVAILABLE')
    observed=max(reversed(rows),key=lambda item:item['deployedAt'])
    hint=old.name.removeprefix('workflows-github-')
    need(re.fullmatch(r'[a-f0-9]{7,40}',hint) is not None and observed['sha'].startswith(hint),
         'WORKER_ACTIVE_PROOF_UNAVAILABLE')
    return observed['sha']


def _history(repo, revision, target):
    declared, entries=SPECS[WORKERS][target]
    root={r:git(repo,'rev-parse',revision+':'+r).decode().strip() for r in declared}
    files,_=tracked_tree(repo,revision,WORKERS,root)
    need(all(x in files for x in entries),'WORKER_ACTIVE_PROOF_UNAVAILABLE')
    return files


def _active_paths(target, selector, old):
    args=selector['ProgramArguments']
    env=selector.get('EnvironmentVariables',{})
    names={p.removeprefix('workflow-production/'):old/p.removeprefix('workflow-production/')
           for p in SPECS[WORKERS][target][0]}
    if target=='worker-animation':
        need(env.get('WORKFLOW_WAN_RELEASE')==str(names['native-wan']),'WORKER_ACTIVE_PROOF_UNAVAILABLE')
    if target=='worker-postprocess':
        need(args.count('--worker-source')==1 and args[args.index('--worker-source')+1]==str(names['native-postprocess']),
             'WORKER_ACTIVE_PROOF_UNAVAILABLE')
    if target=='worker-audio':
        from audio_cutover import NATIVE, _configured_outer, _config, _verify
        _, config, _=_config(_configured_outer(args))
        sidecar=Path(config['release_root'])
        need(sidecar==old.with_name(old.name.replace('workflows-github-','workflow-native-audio-resident-github-',1)),
             'WORKER_ACTIVE_PROOF_UNAVAILABLE')
        _verify(config,sidecar)
        # Compare both the installed source and the actually selected second
        # native copy; dormant source alone cannot establish audio equivalence.
        names[NATIVE]=(names[NATIVE],sidecar)
    return names


def _active_tree(paths, expected):
    content=[]
    for name, anchors in sorted(paths.items()):
        if isinstance(anchors,Path):anchors=(anchors,)
        prefix='workflow-production/'+name+'/'
        expected_root={Path(k.removeprefix(prefix)):v for k,v in expected.items() if k.startswith(prefix)}
        need(bool(expected_root),'WORKER_ACTIVE_PROOF_UNAVAILABLE')
        expected_dirs={p for rel in expected_root for p in rel.parents if p!=Path('.')}
        for anchor in anchors:
            _safe_runtime_dir(anchor)
            found=set();dirs=set()
            for f in anchor.rglob('*'):
                rel=f.relative_to(anchor)
                attrs=f.lstat()
                if stat.S_ISDIR(attrs.st_mode):
                    _safe_runtime_dir(f)
                    dirs.add(rel)
                    continue
                need(rel in expected_root,'WORKER_ACTIVE_TREE_DRIFT')
                oid,mode=expected_root[rel]
                # Earlier approved read-only installs may be 0400/0500; only
                # owner access bits and execute class can differ from 0600/0700.
                flags=stat.S_IMODE(attrs.st_mode)
                need(flags in ((0o400,0o600) if mode==0o644 else (0o500,0o700)),
                     'WORKER_ACTIVE_TREE_DRIFT')
                raw=read_private(f,8*1024*1024,mode=flags) if attrs.st_size else _empty(f,flags)
                need(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==oid,
                     'WORKER_ACTIVE_TREE_DRIFT')
                found.add(rel)
            need(found==set(expected_root) and dirs==expected_dirs,'WORKER_ACTIVE_TREE_DRIFT')
        content.extend((prefix+p.as_posix(),spec) for p,spec in expected_root.items())
    need(len(content)==len(expected),'WORKER_ACTIVE_TREE_DRIFT')
    return digest(json_bytes(sorted(content)))


def _safe_runtime_dir(path):
    attrs=path.lstat()
    need(path.resolve()==path and attrs.st_uid==os.getuid() and stat.S_ISDIR(attrs.st_mode)
         and stat.S_IMODE(attrs.st_mode) in (0o500,0o700),'WORKER_ACTIVE_TREE_DRIFT')


def _comparison(previous_repo, target, selector, old, current, directory, head):
    observed=_observation(directory,target,old)
    previous=current if observed==head else _history(previous_repo(observed),observed,target)
    active_digest=_active_tree(_active_paths(target,selector,old),previous)
    candidate_digest=digest(json_bytes(sorted(current.items())))
    return ('equivalent' if previous==current else 'changed',observed,active_digest,candidate_digest)


def _plan(verified, repo, home, control, release, full_tree, target, trusted_deploy, idle, retry_probe,
          loaded, prior_repo, tracked):
    directory = control/'targets'/target
    private_directory(directory)
    head = verified['proof']['headSha']
    failed=_prior_attempt(directory,target,head)
    need(not failed or target==RETRY_TARGET and retry_probe is not None,'WORKER_ATTEMPT_FAILED')
    prior, selector, old, proposed = _selector(target, home, release)
    prior_hash = digest(prior)
    need(loaded(target,prior_hash) is True,'WORKER_LOADED_UNPROVEN')
    verify_worker_runtime(target,selector)
    try:
        comparison=_comparison(prior_repo,target,selector,old,tracked[target],directory,head)
    except (OSError,ValueError,KeyError,IndexError,TypeError,subprocess.SubprocessError) as error:
        if not failed:
            code='WORKER_ACTIVE_TREE_DRIFT' if getattr(error,'args',())==('WORKER_ACTIVE_TREE_DRIFT',) else 'WORKER_ACTIVE_PROOF_UNAVAILABLE'
            raise Refused(code) from None
        comparison=None # Existing exact Image recovery still requires its separate authority.
    if proposed is None or (comparison and comparison[1]==head):
        need(not failed and comparison and comparison[0]=='equivalent','WORKER_ACTIVE_PROOF_UNAVAILABLE')
        return 'current',None
    if comparison and comparison[0]=='equivalent':
        verification=_runner(target,directory,trusted_deploy)
        return 'verified', ({'target':target,'sha':head,'archiveSha256':verified['artifactSha256'],
                            'expectedPriorPlistSha256':prior_hash},verification,prior,failed,comparison)
    if failed:
        last, _ = failed
        previous=last.get('observed')
        need(type(last.get('failure')) is dict and type(previous) is dict and set(previous)=={'sha','deployedAt'} and
             type(previous['sha']) is str and previous['sha']!=head and
             retry_probe(target,prior_hash,previous['sha'],last.get('finishedAt')) is True,
             'WORKER_RETRY_BASELINE_UNPROVEN')
    need(idle(target, prior_hash) is True, 'WORKER_IDLE_UNPROVEN')
    verification = _runner(target, directory, trusted_deploy)
    verify_worker_runtime(target, proposed)
    candidate = {'schemaVersion': CANDIDATE, 'target': target, 'sha': head,
                 'archiveSha256': verified['artifactSha256'], 'treeSha256': full_tree,
                 'expectedPriorPlistSha256': prior_hash}
    if target == 'worker-audio':
        staged = prepare_audio_candidate(verified, repo, release, home, prior_hash)
        need(all(staged.get(k)==v for k,v in candidate.items()) and set(staged)==set(candidate)|{'audioBindingSha256'},
             'WORKER_AUDIO_PROOF_DRIFT')
        candidate = staged
    return 'verified', (candidate, verification, prior, failed, comparison)


def _publish(directory, candidate, runner, stamp, decision):
    """Ready is the last pointer. Detached candidate proof always binds SHA and ZIP."""
    ready = {'schemaVersion': READY, 'target': candidate['target'], 'sha': candidate['sha'],
             'verifiedAt': stamp, 'artifactSha256': candidate['archiveSha256'], 'runnerSha256': runner,
             'manualOnly': True}
    prior_ready = _optional(directory/'ready.json')
    prior_candidate = _optional(directory/'candidate.json')
    if prior_ready is not None:
        need(type(prior_ready) is dict and
             (set(prior_ready)==set(ready) and prior_ready['manualOnly'] is True
              or set(prior_ready)==set(ready)-{'manualOnly'}) and
             prior_ready['schemaVersion']==READY and prior_ready['target']==candidate['target'],
             'WORKER_REGISTERED_CANDIDATE_DRIFT')
        if prior_ready['sha']==candidate['sha']:
            need(prior_ready['artifactSha256']==candidate['archiveSha256'],'WORKER_REGISTERED_CANDIDATE_DRIFT')
    if prior_candidate is not None:
        keys=set(candidate)
        if candidate['target']=='worker-audio':
            keys.remove('audioBindingSha256') # retain the old ordinary candidate format
        need(type(prior_candidate) is dict and set(prior_candidate) in (keys,set(candidate)) and
             prior_candidate['schemaVersion']==CANDIDATE and prior_candidate['target']==candidate['target'],
             'WORKER_REGISTERED_CANDIDATE_DRIFT')
        if prior_candidate['sha']==candidate['sha']:
            need(prior_candidate['archiveSha256']==candidate['archiveSha256'],'WORKER_REGISTERED_CANDIDATE_DRIFT')
    if prior_ready is not None and all(ready[k] == prior_ready.get(k) for k in ready if k != 'verifiedAt'):
        need(prior_candidate == candidate, 'WORKER_REGISTERED_CANDIDATE_DRIFT')
        _save_decision(directory,decision,candidate)
        return 'already_ready'
    if prior_candidate != candidate:
        atomic_write(directory/'candidate.json', json_bytes(candidate))
    _save_decision(directory,decision,candidate)
    atomic_write(directory/'ready.json', json_bytes(ready))
    return 'ready'



def _write_once(path,value):
    if _optional(path)!=value:
        atomic_write(path,json_bytes(value))


def _save_decision(directory,decision,candidate=None):
    if candidate is not None:
        decision={**decision,'manifestSha256':digest(read_private(directory/'candidate.json',4096,mode=0o600))}
    _write_once(directory/'worker-source-decision.json',decision)


def _decision(candidate,runner,comparison):
    state,revision,old,new=comparison
    return {'schemaVersion':DECISION,'target':candidate['target'],'sha':candidate['sha'],
            'artifactSha256':candidate['archiveSha256'],'runnerSha256':runner,
            'selectorSha256':candidate['expectedPriorPlistSha256'],'observedSha':revision,
            'activeTreeSha256':old,'candidateTreeSha256':new,'state':state}


def _publish_equivalent(directory,candidate,runner,stamp,decision):
    prior=_optional(directory/'ready.json')
    if prior and prior.get('sha')==candidate['sha']:
        need(prior.get('target')==candidate['target'] and prior.get('artifactSha256')==candidate['archiveSha256']
             and prior.get('runnerSha256')==runner,'WORKER_REGISTERED_CANDIDATE_DRIFT')
        stamp=prior['verifiedAt']
    ready={'schemaVersion':READY,'target':candidate['target'],'sha':candidate['sha'],
           'verifiedAt':stamp,'artifactSha256':candidate['archiveSha256'],'runnerSha256':runner,'manualOnly':True}
    equivalent={k:decision[k] for k in ('target','sha','observedSha','artifactSha256','runnerSha256','selectorSha256')}
    equivalent.update(schemaVersion='ops.deploy-target-equivalent.v1',verifiedAt=stamp,
                      runtimeTreeSha256=decision['activeTreeSha256'])
    _write_once(directory/'equivalent.json',equivalent)
    _save_decision(directory,decision)
    _write_once(directory/'ready.json',ready)
    return 'current'


def _publish_retry(directory,candidate,runner,failed):
    # Rebinding only the prior selector is allowed, and only after re-reading
    # the same exact Git/archive tree + installed runner and the repaired prior.
    ready=_optional(directory/'ready.json')
    prior=_optional(directory/'candidate.json')
    keys=('schemaVersion','target','sha','archiveSha256','treeSha256')
    need(type(prior) is dict and set(prior)==set(candidate) and all(prior[k]==candidate[k] for k in keys)
         and type(ready) is dict and set(ready)=={'schemaVersion','target','sha','verifiedAt',
            'artifactSha256','runnerSha256','manualOnly'} and ready['schemaVersion']==READY
         and ready['manualOnly'] is True and ready['target']==candidate['target'] and ready['sha']==candidate['sha']
         and ready['artifactSha256']==candidate['archiveSha256'] and ready['runnerSha256']==runner,
         'WORKER_REGISTERED_CANDIDATE_DRIFT')
    candidate_bytes=json_bytes(candidate)
    offer={'schemaVersion':RETRY_READY,'target':candidate['target'],'sha':candidate['sha'],
           'failureSha256':failed[1],'candidateSha256':digest(candidate_bytes),'runnerSha256':runner}
    if prior!=candidate:
        atomic_write(directory/'candidate.json',candidate_bytes)
    elif digest(read_private(directory/'candidate.json',4096,mode=0o600))!=offer['candidateSha256']:
        # Preserve exact already-published bytes rather than canonically rewriting.
        offer['candidateSha256']=digest(read_private(directory/'candidate.json',4096,mode=0o600))
    if _optional(directory/'retry-ready.json')!=offer:
        atomic_write(directory/'retry-ready.json',json_bytes(offer))
    return 'ready'


def prepare_worker_candidates(evidence, *, repo, home, control, read_main, idle, trusted_deploy=None, retry_probe=None, loaded=None, prior_repo=None):
    """Accept exact authenticated metadata/ZIP; publish only after trusted idle and CAS checks.

    Transport and process supervision are separate. `read_main()` fetches the
    canonical Workers main from the same trusted provenance source; `idle`
    checks the exact, currently loaded worker selector and that worker's task
    boundary, and may deny. No default grants or private config changes.
    """
    control, repo, home = map(Path, (control, repo, home))
    trusted_deploy = Path(trusted_deploy) if trusted_deploy is not None else Path(__file__).resolve().parent
    private_directory(control)
    private_directory(control/'targets')
    need(callable(loaded) and callable(prior_repo),'WORKER_ACTIVE_PROOF_UNAVAILABLE')
    need(type(evidence) is dict and set(evidence) == {'head','workflow','run','artifact','raw'},
         'WORKER_EVIDENCE_INVALID')
    head = evidence['head']
    verified = verify_target_artifact(head, evidence['workflow'], evidence['run'],
                                      evidence['artifact'], evidence['raw'], repo=WORKERS)
    need(read_main() == head, 'WORKER_MAIN_ADVANCED')
    tracked = tracked_targets(verified, repo)
    releases = home/'Workspaces/Workers/code/releases'
    private_directory(releases)
    release = releases/('workflows-github-'+head)
    with lock(control/'prepare-workers.lock', create=True):
        need(not os.path.lexists(control/'active.json'), 'WORKER_DEPLOY_ACTIVE_OR_UNVERIFIED')
        if not os.path.lexists(release):
            need(assemble_source(verified, repo, releases) == release, 'WORKER_RELEASE_PATH_MISMATCH')
        full_tree = _exact_source(release, tracked)
        plans, rows = {}, {}
        for target in TARGET_IDS:
            try:
                state, plan = _plan(verified, repo, home, control, release, full_tree, target, trusted_deploy, idle, retry_probe, loaded, prior_repo, tracked)
                rows[target] = {'state': state}
                if plan:
                    plans[target] = plan
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                rows[target] = {'state': 'blocked', 'code': _safe_reason(exc)}
        if not plans:
            return {'sha': head, 'targets': rows}
        with lock(control/'cycle.lock', create=True):
            need(not os.path.lexists(control/'active.json'), 'WORKER_DEPLOY_ACTIVE_OR_UNVERIFIED')
            need(_exact_source(release, tracked) == full_tree, 'WORKER_RELEASE_SOURCE_DRIFT')
            verified_plans = {}
            for target, (candidate, pins, prior, failed, comparison) in plans.items():
                try:
                    updated_state, updated_plan = _plan(verified, repo, home, control, release, full_tree,
                                                       target, trusted_deploy, idle, retry_probe, loaded, prior_repo, tracked)
                    need(updated_state == 'verified' and updated_plan == (candidate,pins,prior,failed,comparison),
                         'WORKER_ADMISSION_INPUT_DRIFT')
                    verified_plans[target] = (candidate, pins[0], failed, comparison)
                except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                    rows[target] = {'state': 'blocked', 'code': _safe_reason(exc)}
            if verified_plans:
                need(read_main() == head, 'WORKER_MAIN_ADVANCED')
                stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                for target, (candidate, runner, failed, comparison) in verified_plans.items():
                    directory = control/'targets'/target
                    try:
                        prior, _, _, proposed = _selector(target, home, release)
                        pin=candidate['expectedPriorPlistSha256']
                        need(proposed is not None and digest(prior)==pin and loaded(target,pin) is True
                             and _prior_attempt(directory,target,head)==failed,'WORKER_ADMISSION_INPUT_DRIFT')
                        if not comparison or comparison[0]!='equivalent':
                            need(idle(target,pin) is True,'WORKER_IDLE_UNPROVEN')
                        if comparison and comparison[0]=='equivalent':
                            state=_publish_equivalent(directory,candidate,runner,stamp,_decision(candidate,runner,comparison))
                        elif failed:
                            state=_publish_retry(directory,candidate,runner,failed)
                        else:
                            state=_publish(directory,candidate,runner,stamp,_decision(candidate,runner,comparison))
                        rows[target] = {'state':state}
                    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                        rows[target] = {'state': 'blocked', 'code': _safe_reason(exc)}
        return {'sha': head, 'targets': rows}


SAFE_CODES = frozenset(('WORKER_RUNNER_UNVERIFIED WORKER_RUNNER_DEPENDENCY_UNVERIFIED '
    'WORKER_SELECTOR_INVALID WORKER_RUNTIME_PATH_INVALID WORKER_RUNTIME_DEPENDENCY_UNVERIFIED '
    'WORKER_DATABASE_UNVERIFIED WORKER_IMAGE_CONFIG_UNVERIFIED WORKER_OUTCOME_UNVERIFIED '
    'WORKER_ATTEMPT_FAILED WORKER_RETRY_BASELINE_UNPROVEN WORKER_IDLE_UNPROVEN WORKER_AUDIO_PROOF_DRIFT '
    'WORKER_ADMISSION_INPUT_DRIFT WORKER_REGISTERED_CANDIDATE_DRIFT WORKER_ACTIVE_PROOF_UNAVAILABLE '
    'WORKER_ACTIVE_TREE_DRIFT WORKER_LOADED_UNPROVEN '
    'TARGET_SELECTOR_LABEL TARGET_SELECTOR_ARGUMENTS TARGET_SELECTOR_ENTRY TARGET_SELECTOR_WORKDIR '
    'TARGET_UNHANDLED_RELEASE_ARG TARGET_UNHANDLED_RELEASE_REFERENCE TARGET_RELEASE_ENTRY_MISSING '
    'TARGET_AUDIO_ADAPTER TARGET_AUDIO_BINDING_INVALID TARGET_AUDIO_BINDING_RELEASE '
    'TARGET_AUDIO_BINDING_RUNTIME TARGET_AUDIO_BINDING_TREE TARGET_AUDIO_BINDING_PINS '
    'TARGET_AUDIO_BINDING_ALREADY_STAGED TARGET_ANIMATION_WAN_RELEASE TARGET_POSTPROCESS_ADAPTER').split())


def _safe_reason(exc):
    code = exc.args[0] if type(exc) is Refused and len(exc.args) == 1 else None
    return code if type(code) is str and code in SAFE_CODES else 'WORKER_CANDIDATE_UNVERIFIED'
