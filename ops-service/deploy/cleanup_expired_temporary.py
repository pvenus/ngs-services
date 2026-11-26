"""Fixed-path OPS housekeeping. Schedule this program externally with launchd."""

from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import plistlib
import re
import shutil
import stat
import subprocess
import time
from urllib.request import build_opener, ProxyHandler
import uuid


RETENTION_DAYS = 10
NANOSECONDS_PER_DAY = 86_400 * 1_000_000_000
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@dataclass(frozen=True)
class Rule:
    name: str
    directory: str  # Relative to ~/Workspaces, never a glob.
    suffix: str
    kind: str = 'file'  # 'empty-directory' is supported for narrowly named leaf folders.
    lock: str = 'none'  # 'huggingface' guards the producer's existing download lock.

    def __post_init__(self):
        parts = self.directory.split('/')
        if (any(p in ('', '.', '..') for p in parts)
                or not self.suffix.startswith('.') or len(self.suffix) < 2
                or '/' in self.suffix or any(c in self.suffix for c in '*?[]')
                or self.kind not in ('file', 'empty-directory')
                or self.lock not in ('none', 'huggingface')):
            raise ValueError('invalid cleanup rule')


RULES = (
    Rule('wan-part', 'Workers/code/experiments/comfyui-wan-animate2-20261111/models/diffusion_models', '.part'),
    Rule('ace-incomplete', 'Workers/runtime/acestep/source/checkpoints/acestep-v15-xl-sft/.cache/huggingface/download',
         '.incomplete', lock='huggingface'),
)


@contextmanager
def open_directory(path):
    # Resolve each path component under a no-follow descriptor, not Path.resolve().
    if not path.is_absolute():
        raise ValueError('workspace root must be absolute')
    fd = os.open('/', DIRECTORY_FLAGS)
    try:
        for name in path.parts[1:]:
            if name in ('.', '..'):
                raise ValueError('unsafe cleanup path')
            child = os.open(name, DIRECTORY_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def identity(info):
    return info.st_dev, info.st_ino


def unchanged(info):
    return (*identity(info), info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def no_open_descriptors(path):
    # A true no-match from macOS lsof is status 1 with no output. Errors, warnings
    # and timeouts are not proof the file is unused. Keep argument separate.
    try:
        result = subprocess.run(['/usr/sbin/lsof', '-nP', '-Fn', str(path)],
                                stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=8, check=False, env={'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'})
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 1 and not result.stdout and not result.stderr


@contextmanager
def download_guard(directory_fd, name, rule, owner):
    if rule.lock == 'none':
        yield True
        return
    # Hugging Face local-dir child partials are filename.ETAG.incomplete.
    # Skip unknown names instead of guessing at their producer lock.
    parts = name.rsplit('.', 2)
    if len(parts) != 3 or not all(parts):
        yield False
        return
    lockname = parts[0] + '.lock'
    try:
        fd = os.open(lockname, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    except FileNotFoundError:
        # No pre-existing lock. A newly appeared lock is checked separately.
        yield None
        return
    except OSError:
        yield False
        return
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != owner or info.st_nlink != 1:
            yield False
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield identity(info)
    finally:
        os.close(fd)


def same_producer_lock(directory_fd, name, rule, guard):
    if rule.lock == 'none':
        return True
    lockname = name.rsplit('.', 2)[0] + '.lock'
    try:
        info = os.stat(lockname, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return guard is None
    return bool(guard and guard == identity(info) and stat.S_ISREG(info.st_mode))


def cleanup(workspace, *, rules=RULES, now_ns=None, dry_run=False, open_check=no_open_descriptors):
    cutoff = (time.time_ns() if now_ns is None else now_ns) - RETENTION_DAYS * NANOSECONDS_PER_DAY
    result = {}
    owner = os.geteuid()
    for rule in rules:
        counts = dict(removed=0, eligible=0, skipped=0, allocatedBytes=0, errors=0)
        result[rule.name] = counts
        path = workspace.joinpath(*PurePosixPath(rule.directory).parts)
        try:
            with open_directory(path) as fd:
                directory = os.fstat(fd)
                if directory.st_uid != owner:
                    counts['errors'] += 1
                    continue
                for name in os.listdir(fd):
                    if not name.endswith(rule.suffix):
                        continue
                    try:
                        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                        is_file = rule.kind == 'file'
                        good_type = stat.S_ISREG(info.st_mode) and info.st_nlink == 1 if is_file else stat.S_ISDIR(info.st_mode)
                        if (not good_type or info.st_uid != owner or info.st_dev != directory.st_dev
                                or info.st_mtime_ns > cutoff):
                            counts['skipped'] += 1
                            continue
                        with download_guard(fd, name, rule, owner) as protected:
                            if protected is False or not open_check(path / name):
                                counts['skipped'] += 1
                                continue
                            # Do not remove an entry changed or swapped during the open-file probe.
                            with open_directory(path) as current:
                                if (identity(os.fstat(current)) != identity(directory)
                                        or not same_producer_lock(fd, name, rule, protected)
                                        or unchanged(os.stat(name, dir_fd=fd, follow_symlinks=False)) != unchanged(info)):
                                    counts['skipped'] += 1
                                    continue
                                if not is_file:
                                    nested = os.open(name, DIRECTORY_FLAGS, dir_fd=fd)
                                    try:
                                        if unchanged(os.fstat(nested)) != unchanged(info) or os.listdir(nested):
                                            counts['skipped'] += 1
                                            continue
                                    finally:
                                        os.close(nested)
                                counts['eligible'] += 1
                                if not dry_run:
                                    if is_file:
                                        os.unlink(name, dir_fd=fd)
                                    else:
                                        # A concurrent child appearing makes rmdir fail safely.
                                        os.rmdir(name, dir_fd=fd)
                                    counts['removed'] += 1
                                    counts['allocatedBytes'] += info.st_blocks * 512
                    except OSError as exc:
                        key = 'skipped' if exc.errno in (errno.ENOENT, errno.ENOTEMPTY, errno.EBUSY) else 'errors'
                        counts[key] += 1
        except FileNotFoundError:
            # Older machines may not have a given model/cache installed.
            pass
        except OSError:
            counts['errors'] += 1
    return result


RELEASES = 'Workspaces/NGS/code/releases'
CONTROL = 'Workspaces/OPS/runtime/deploy-control'
RELEASE_PREFIX = 'project-page-github-'
SHA = re.compile(r'[a-f0-9]{40}')
DIGEST = re.compile(r'[a-f0-9]{64}')
NGS_LOCAL = 'com.vinus.ngs.local-mcp'
LOCAL_ENTRY = '/sites/npcgen/build/local-miniflare-app.mjs'


def require(condition):
    if not condition:
        raise ValueError('unverified NGS release state')


def private_read(path, limit=262144, *, external=False):
    with open_directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid in (
                {0, os.geteuid()} if external else {os.geteuid()})
                and info.st_nlink == 1 and not info.st_mode & 0o022 and 0 < info.st_size <= limit)
            raw = stream.read(limit + 1)
            require(len(raw) == info.st_size and unchanged(os.fstat(stream.fileno())) == unchanged(info)
                    and unchanged(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) == unchanged(info))
            return raw


def record(path, limit=262144):
    obj = json.loads(private_read(path, limit))
    require(isinstance(obj, dict))
    return obj


@contextmanager
def deployment_locks(control):
    # Same producer order: preparation locks, cycle.lock, then apply's directory lock.
    lock_names = ('prepare-ngs-local.lock', 'prepare-ops-candidates.lock',
                  'prepare-services.lock', 'cycle.lock')
    with open_directory(control) as parent:
        info = os.fstat(parent)
        require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700)
        with ExitStack() as stack:
            for name in lock_names:
                fd = os.open(name, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
                stack.callback(os.close, fd)
                info = os.fstat(fd)
                require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                        and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
                        and identity(info) == identity(os.stat(name, dir_fd=parent, follow_symlinks=False)))
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require(not os.path.lexists(control / 'active.json'))
            yield


def references(value, releases):
    prefix = str(releases) + '/'
    if isinstance(value, str):
        # Loaded launchd text may have multiple entries/lines. A candidate-name
        # reference followed by a slash, whitespace or delimiters is preserved.
        items = re.findall(re.escape(prefix) + r"([a-zA-Z0-9._-]+)(?=[/\s,;]|$)", value)
        require(value.count(prefix) == len(items))
        return set(items)
    if isinstance(value, list):
        return set().union(*(references(item, releases) for item in value))
    if isinstance(value, dict):
        return set().union(*(references(item, releases) for item in value.values()))
    return set()


def local_name(plist, releases):
    require(plist.get('Label') == NGS_LOCAL)
    args = plist.get('ProgramArguments')
    require(isinstance(args, list) and all(isinstance(arg, str) for arg in args))
    paths = [arg for arg in args if arg.endswith(LOCAL_ENTRY)]
    require(len(paths) == 1)
    names = references(paths[0], releases)
    require(len(names) == 1)
    name = names.pop()
    require(name.startswith(RELEASE_PREFIX) and SHA.fullmatch(name[len(RELEASE_PREFIX):]))
    require(paths[0] == str(releases / name) + LOCAL_ENTRY)
    return name


def launch_info(domain):
    result = subprocess.run(['/bin/launchctl', 'print', domain], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=6, check=False)
    require(result.returncode == 0 and not result.stderr and 0 < len(result.stdout) <= 4 * 1024 * 1024)
    return result.stdout


def live_ngs(pid):
    try:
        result = subprocess.run(['/usr/sbin/lsof', '-nP', '-Fp', '-a', '-iTCP:5274', '-sTCP:LISTEN'],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5, check=False)
        if result.returncode or result.stderr or [line for line in result.stdout.splitlines() if line.startswith('p')] != ['p' + pid]:
            return False
        opener = build_opener(ProxyHandler({}))
        with opener.open('http://127.0.0.1:5274/', timeout=3) as response:
            return response.status == 200 and response.geturl() == 'http://127.0.0.1:5274/'
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def active_releases(home, releases, *, launch=launch_info, health=live_ngs):
    installed = home / 'Library/LaunchAgents'
    local = plistlib.loads(private_read(installed / (NGS_LOCAL + '.plist')))
    current = local_name(local, releases)
    protected = references(local, releases)
    domain = 'gui/' + str(os.geteuid())
    services = launch(domain)
    require('\n\tservices = {\n' in services)
    rows = services.split('\n\tservices = {\n', 1)[1].split('\n\t}', 1)[0]
    labels = set(re.findall(r'^\s*\d+\s+\S+\s+(com\.(?:vinus|projectbs)\.[\w.-]+)\s*$', rows, re.M))
    require(NGS_LOCAL in labels)
    for folder in (installed, Path('/Library/LaunchAgents'), Path('/Library/LaunchDaemons')):
        try:
            with open_directory(folder) as fd:
                names = [name for name in os.listdir(fd) if name.endswith('.plist')]
        except FileNotFoundError:
            continue
        for name in names:
            data = plistlib.loads(private_read(folder / name, external=(folder != installed)))
            refs = references(data, releases)
            protected.update(refs)
            if refs and folder == installed:
                label = data.get('Label')
                require(isinstance(label, str) and re.fullmatch(r'[\w.-]+', label))
                # Installed config alone protects inactive services. For a live
                # matching row, inspect the loaded config too, in case it differs.
                if re.search(r'^\s*\d+\s+\S+\s+' + re.escape(label) + r'\s*$', rows, re.M):
                    labels.add(label)
    for label in sorted(labels):
        description = launch(domain + '/' + label)
        protected.update(references(description, releases))
        if label == NGS_LOCAL:
            pid = re.search(r'^	pid = ([1-9][0-9]*)$', description, re.M)
            require(pid and '\n\tstate = running\n' in description)
            require(str(releases / current) + LOCAL_ENTRY in description and health(pid.group(1)))
    return current, protected


def journal_manifest(control, name):
    sha = name[len(RELEASE_PREFIX):]
    journal = control / 'mac-releases' / sha
    manifest = record(journal / 'manifest.json')
    require(manifest.get('schemaVersion') == 'ops.ngs-mac-release.v1' and manifest.get('sha') == sha
            and manifest.get('repo') == 'pvenus/ngs-platform' and manifest.get('ref') == 'refs/heads/main'
            and all(isinstance(manifest.get(key), str) and DIGEST.fullmatch(manifest[key])
                    for key in ('archiveSha256', 'treeSha256', 'expectedPriorPlistSha256')))
    return journal, manifest


def successful_receipts(journal, sha):
    paths = [journal]
    try:
        with open_directory(journal / 'attempts') as fd:
            paths.extend(journal / 'attempts' / n for n in os.listdir(fd) if re.fullmatch(r'[\w-]{1,128}', n))
    except FileNotFoundError:
        pass
    for path in paths:
        try:
            result = record(path / 'result.json')
        except FileNotFoundError:
            continue
        if (result.get('state') == 'deployed' and result.get('health') == 'verified'
                and result.get('target') == 'mac' and result.get('sha') == sha
                and isinstance(result.get('deployedAt'), str)):
            yield path, result


def rollback_name(control, releases, current, result):
    current_sha = current[len(RELEASE_PREFIX):]
    observed = result.get('observed')
    require(isinstance(observed, dict) and observed.get('sha') == current_sha)
    journal, manifest = journal_manifest(control, current)
    matching = [path for path, receipt in successful_receipts(journal, current_sha)
                if receipt['deployedAt'] == observed.get('deployedAt')]
    require(len(matching) == 1)
    prior = private_read(matching[0] / 'prior.plist')
    require(hashlib.sha256(prior).hexdigest() == manifest['expectedPriorPlistSha256'])
    prior_plist = plistlib.loads(prior)
    name = local_name(prior_plist, releases)
    require(name != current)
    prior_journal, _ = journal_manifest(control, name)
    require(any(successful_receipts(prior_journal, name[len(RELEASE_PREFIX):])))
    expected_execution = releases.parent.parent / 'runtime/local-miniflare-execution' / name[len(RELEASE_PREFIX):]
    require(prior_plist.get('WorkingDirectory') == str(expected_execution))
    with open_directory(expected_execution), open_directory(releases / name):
        private_read(releases / name / LOCAL_ENTRY.lstrip('/'), limit=8 * 1024 * 1024)
    return name, references(prior_plist, releases)


def deployment_references(control, releases, current):
    files = (
        ('prepared.json', 'ops.ngs-prepared-deploy.v1', None),
        ('ngs-local-preparation-status.json', 'ops.ngs-local-preparation-status.v1', 'ngs-local'),
        ('targets/ngs-local/ready.json', 'ops.deploy-target-ready.v1', 'ngs-local'),
        ('targets/ngs-local/last-result.json', 'ops.deploy-target-result.v1', 'ngs-local'),
        ('targets/rs-mcp/candidate.json', 'ops.mac-target-candidate.v1', 'rs-mcp'),
        ('targets/rs-mcp/ready.json', 'ops.deploy-target-ready.v1', 'rs-mcp'),
        ('targets/rs-mcp/last-result.json', 'ops.deploy-target-result.v1', 'rs-mcp'),
    )
    protected = set()
    local_result = None
    for route, schema, target in files:
        data = record(control / route)
        require(data.get('schemaVersion') == schema and (target is None or data.get('target') == target))
        keys = ('sha',) if not route.endswith('/last-result.json') else ('attemptedSha', 'observed')
        for key in keys:
            value = data.get(key)
            if key == 'observed':
                require(isinstance(value, dict))
                value = value.get('sha')
            if route == 'ngs-local-preparation-status.json' and value is None and data.get('state') != 'ready':
                continue
            require(isinstance(value, str) and SHA.fullmatch(value))
            protected.add(RELEASE_PREFIX + value)
        protected.update(references(data, releases))
        if route == 'targets/ngs-local/last-result.json':
            local_result = data
    prior, prior_refs = rollback_name(control, releases, current, local_result)
    return protected | prior_refs | {current, prior}


def tree_snapshot(fd, base_device, cutoff, *, deadline=None):
    # Snapshot only regular files and real directories on one filesystem.
    # Existing symlinks, hardlinks, recent entries and non-model filesystem
    # types make the whole release ineligible. Never follow inside the tree.
    fingerprint = hashlib.sha256()
    count = 0
    size = 0

    def visit(parent, depth):
        nonlocal count, size
        require(depth <= 64)
        for name in sorted(os.listdir(parent)):
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('release scan deadline')
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            require(info.st_uid == os.geteuid() and info.st_dev == base_device and info.st_mtime_ns <= cutoff)
            count += 1
            require(count <= 100_000)
            fingerprint.update(repr((depth, name, unchanged(info))).encode(errors='surrogateescape'))
            size += info.st_blocks * 512
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, DIRECTORY_FLAGS, dir_fd=parent)
                try:
                    require(unchanged(os.fstat(child)) == unchanged(info))
                    visit(child, depth + 1)
                    require(unchanged(os.fstat(child)) == unchanged(info))
                finally:
                    os.close(child)
            else:
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    visit(fd, 0)
    return fingerprint.digest(), size


def no_open_release(path):
    try:
        result = subprocess.run(['/usr/sbin/lsof', '-nP', '-Fn', '+D', str(path)],
                                stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=8, check=False, env={'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'})
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 1 and not result.stdout and not result.stderr


LEGACY_RELEASES = (
    ('runtime-space', 'runtime'),
    ('runtime-git', 'runtime'),
    ('source-git', 'source'),
)
LEGACY_FIELDS = frozenset(('baseCommit', 'candidateOnly', 'changes', 'commit',
                           'dependenciesCopiedFrom', 'files', 'integratedHotfix',
                           'localHotfix', 'preservedGeneratedRuntimeFiles',
                           'privateRuntimeOverrides', 'selected'))
LEGACY_MATCH = re.compile(r'(runtime-space|runtime-git|source-git)-([a-f0-9]{7})')
SPACE_BUSY = frozenset(('pending', 'claimed', 'uncertain'))
PROFILE_FILES = (
    '.profile', '.zprofile', '.zshenv', '.zshrc', '.bash_profile', '.bashrc',
    '.config/fish/config.fish', '.codex/config.toml', '.codex/.mcp.json',
    'Workspaces/NGS/connection-bridge/code/start-local.sh',
    'Workspaces/NGS/connection-bridge/code/start-bridge.sh',
)
BATCH_COUNT = 8
LOCK_SCAN_SECONDS = 16


@contextmanager
def space_queue_lock(control):
    with open_directory(control / 'targets/ngs-space') as parent:
        owner = os.fstat(parent)
        require(owner.st_uid == os.geteuid() and stat.S_IMODE(owner.st_mode) == 0o700)
        name = 'publish-queue.lock'
        fd = os.open(name, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                    and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require(identity(info) == identity(os.stat(name, dir_fd=parent, follow_symlinks=False)))
            yield
        finally:
            os.close(fd)


def space_pins(control):
    # Publication uses commit and provider SHAs. Capture current and previous
    # healthy references and fail closed for in-flight/uncertain handoffs.
    target = control / 'targets/ngs-space'
    publication = record(target / 'publication.json')
    queue = record(target / 'publish-queue.json')
    require(publication.get('schemaVersion') == 'ops.ngs-space-publication.v1'
            and publication.get('provider') == 'chatgpt-sites'
            and isinstance(publication.get('projectId'), str)
            and set(queue) == {'schemaVersion', 'items'}
            and queue['schemaVersion'] == 'ops.space-publish-queue.v1'
            and isinstance(queue['items'], list) and len(queue['items']) <= 100)
    pins = set()
    success = 0
    for item in queue['items']:
        require(isinstance(item, dict) and isinstance(item.get('commitSha'), str)
                and SHA.fullmatch(item['commitSha']) and item.get('state') in
                {'pending', 'claimed', 'uncertain', 'succeeded', 'failed'})
        require(item['state'] not in SPACE_BUSY)
        if item['state'] != 'succeeded':
            continue
        result = item.get('result')
        require(isinstance(result, dict) and result.get('state') == 'succeeded'
                and result.get('commitSha') == item['commitSha']
                and isinstance(result.get('providerSha'), str) and SHA.fullmatch(result['providerSha'])
                and isinstance(result.get('versionId'), str)
                and result['versionId'].startswith(publication['projectId'] + '~')
                and isinstance(result.get('deployedAt'), str))
        success += 1
        if success <= 2:
            pins.update((item['commitSha'], result['providerSha']))
    require(success >= 1)
    return pins


def profile_pins(home, releases):
    protected = set()
    for relative in PROFILE_FILES:
        path = home / relative
        try:
            # Unknown symlinked config can point to additional startup wiring;
            # do not clean legacy releases on that machine until it is reviewed.
            raw = private_read(path, limit=1 << 20)
        except FileNotFoundError:
            continue
        content = raw.decode('utf8')
        # Include short legacy names even when a config builds paths from HOME.
        protected.update(re.findall(r'(?:runtime-space|runtime-git|source-git)-[a-f0-9]{7}\b', content))
        protected.update(references(content.replace('"', ' ').replace("'", ' '), releases))
    return protected


def load_legacy_manifest(releases, name):
    match = LEGACY_MATCH.fullmatch(name)
    require(match is not None)
    kind, suffix = match.groups()
    child = dict(LEGACY_RELEASES)[kind]
    path = releases / name
    meta = record(path / 'git-release.json', limit=4 * 1024 * 1024)
    require(set(meta) <= LEGACY_FIELDS and isinstance(meta.get('commit'), str)
            and SHA.fullmatch(meta['commit']) and meta['commit'].startswith(suffix))
    if kind == 'runtime-space':
        require(isinstance(meta.get('baseCommit'), str)
                and re.fullmatch(r'(?:[a-f0-9]{7}|[a-f0-9]{40})', meta['baseCommit']))
    files = meta.get('files')
    require(isinstance(files, dict) and 1 <= len(files) <= 25_000
            and all('/'.join(PurePosixPath(key).parts) == key and not key.startswith('/')
                    and '..' not in PurePosixPath(key).parts and bool(key) and '\x00' not in key
                    and isinstance(digest, str) and DIGEST.fullmatch(digest)
                    for key, digest in files.items()))
    with open_directory(path) as fd:
        require(set(os.listdir(fd)) == {'git-release.json', child})
        status = os.fstat(fd)
    with open_directory(path / child):
        pass
    # The manifests for this format are often incremental instead of complete;
    # a real listed file hash is an additional witness tying the tree to its
    # manifest. Bound verification to small files, and never read through links.
    for index, (relative, digest) in enumerate(files.items()):
        if index >= 24:
            break
        try:
            raw = private_read(path / child / relative, limit=2 * 1024 * 1024)
        except (OSError, ValueError):
            continue
        if hashlib.sha256(raw).hexdigest() == digest:
            return kind, meta['commit'], status
    raise ValueError('unverified legacy expansion content')


def process_release_pins(releases):
    # One complete process-name snapshot rather than lsof +D per tree. NUL
    # fields preserve spaces and newlines in user filenames.
    result = subprocess.run(['/usr/sbin/lsof', '-nP', '-F0n'], stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=6, check=False,
                            env={'LC_ALL': 'C', 'PATH': '/usr/bin:/bin'})
    require(result.returncode == 0 and not result.stderr and 0 < len(result.stdout) <= 32 * 1024 * 1024)
    prefix = os.fsencode(releases) + b'/'
    protected = set()
    for field in result.stdout.split(b'\x00'):
        field = field.lstrip(b'\n')
        if not field.startswith(b'n'):
            continue
        path = field[1:]
        if path.startswith(b'/System/Volumes/Data' + prefix):
            path = path[len(b'/System/Volumes/Data'):]
        if path.startswith(prefix):
            child = path[len(prefix):].split(b'/', 1)[0]
            # A file that was already unlinked may be reported with this suffix.
            child = child.removesuffix(b' (deleted)')
            require(re.fullmatch(rb'[a-zA-Z0-9._-]+', child))
            protected.add(os.fsdecode(child))
    return protected


def cleanup_legacy_ngs(home, *, now_ns=None, dry_run=False, active=active_releases,
                       open_paths=process_release_pins, monotonic=time.monotonic):
    cutoff = (time.time_ns() if now_ns is None else now_ns) - RETENTION_DAYS * NANOSECONDS_PER_DAY
    counts = dict(removed=0, eligible=0, skipped=0, allocatedBytes=0, errors=0, blocked=False,
                  deferred=0, scanned=0, eligibleAllocatedBytes=0)
    control, releases = home / CONTROL, home / RELEASES
    staged = []
    try:
        with open_directory(releases) as root:
            with deployment_locks(control), space_queue_lock(control):
                current, protected = active(home, releases)
                protected |= deployment_references(control, releases, current)
                protected |= profile_pins(home, releases)
                protected |= open_paths(releases)
                pins = space_pins(control)
                require(shutil.rmtree.avoids_symlink_attacks)
                deadline = monotonic() + LOCK_SCAN_SECONDS
                device = os.fstat(root).st_dev
                # Report candidates directly from the directory descriptor;
                # root identity must still agree with the path seen by reads.
                all_rows = []
                for name in os.listdir(root):
                    match = LEGACY_MATCH.fullmatch(name)
                    if match:
                        status = os.stat(name, dir_fd=root, follow_symlinks=False)
                        all_rows.append((name, match[1], status))
                newest = {}
                # Preserve one known-good expanded backup per family even when
                # operations are now published from GitHub without a local tree.
                for name, kind, _ in sorted(all_rows, key=lambda row: row[2].st_mtime_ns, reverse=True):
                    if kind not in newest:
                        require(monotonic() < deadline)
                        try:
                            load_legacy_manifest(releases, name)
                        except (OSError, ValueError):
                            continue
                        newest[kind] = name
                require('runtime-space' in newest)
                protected.update(newest.values())
                candidates = []
                for name, kind, info in all_rows:
                    if (name in protected or info.st_dev != device or info.st_uid != os.geteuid()
                            or not stat.S_ISDIR(info.st_mode) or info.st_mtime_ns > cutoff
                            or any(commit.startswith(name[-7:]) for commit in pins)):
                        counts['skipped'] += 1
                    else:
                        candidates.append((name, kind, info))
                for index, (name, kind, info) in enumerate(sorted(candidates, key=lambda row: row[2].st_mtime_ns)):
                    if monotonic() >= deadline or counts['eligible'] >= BATCH_COUNT:
                        counts['deferred'] += len(candidates) - index
                        break
                    counts['scanned'] += 1
                    try:
                        proven_kind, commit, observed = load_legacy_manifest(releases, name)
                        require(proven_kind == kind and commit not in pins and unchanged(observed) == unchanged(info))
                        child = os.open(name, DIRECTORY_FLAGS, dir_fd=root)
                        try:
                            require(unchanged(os.fstat(child)) == unchanged(info))
                            bound = min(deadline, monotonic() + 4)
                            before, size = tree_snapshot(child, device, cutoff, deadline=bound)
                            # Refresh the open-path set immediately before the
                            # destructive move; this is independent of tree size.
                            after, _ = tree_snapshot(child, device, cutoff, deadline=bound)
                            if not dry_run:
                                require(name not in open_paths(releases))
                            require(before == after and unchanged(os.fstat(child)) == unchanged(info)
                                    and unchanged(os.stat(name, dir_fd=root, follow_symlinks=False)) == unchanged(info))
                            candidate_bytes = size + info.st_blocks * 512
                            counts['eligible'] += 1
                            counts['eligibleAllocatedBytes'] += candidate_bytes
                            if not dry_run:
                                hidden = '.ops-cleanup-' + uuid.uuid4().hex
                                os.rename(name, hidden, src_dir_fd=root, dst_dir_fd=root)
                                require(identity(os.stat(hidden, dir_fd=root, follow_symlinks=False)) == identity(info))
                                staged.append((hidden, candidate_bytes))
                        finally:
                            os.close(child)
                    except (ValueError, FileNotFoundError):
                        counts['skipped'] += 1
                    except TimeoutError:
                        counts['deferred'] += 1
                    except OSError:
                        counts['errors'] += 1
            # Other processes never see the private quarantine as a deploy
            # candidate. Do not hold producer locks for recursive unlinks.
            for hidden, size in staged:
                try:
                    shutil.rmtree(hidden, dir_fd=root)
                except OSError:
                    counts['errors'] += 1
                    continue
                counts['removed'] += 1
                counts['allocatedBytes'] += size
    except (OSError, ValueError, TypeError, AttributeError, plistlib.InvalidFileException, subprocess.SubprocessError):
        counts['blocked'] = True
    return counts


def cleanup_ngs(home, *, now_ns=None, dry_run=False, active=active_releases, open_check=no_open_release):
    cutoff = (time.time_ns() if now_ns is None else now_ns) - RETENTION_DAYS * NANOSECONDS_PER_DAY
    counts = dict(removed=0, eligible=0, skipped=0, allocatedBytes=0, errors=0, blocked=False)
    control, releases = home / CONTROL, home / RELEASES
    try:
        with deployment_locks(control), open_directory(releases) as root:
            current, protected = active(home, releases)
            protected |= deployment_references(control, releases, current)
            require(shutil.rmtree.avoids_symlink_attacks)
            device = os.fstat(root).st_dev
            for name in sorted(os.listdir(root)):
                if not (name.startswith(RELEASE_PREFIX) and SHA.fullmatch(name[len(RELEASE_PREFIX):])):
                    continue
                if name in protected:
                    counts['skipped'] += 1
                    continue
                try:
                    journal_manifest(control, name)
                    info = os.stat(name, dir_fd=root, follow_symlinks=False)
                    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
                            and info.st_dev == device and info.st_mtime_ns <= cutoff)
                    child = os.open(name, DIRECTORY_FLAGS, dir_fd=root)
                    try:
                        require(unchanged(os.fstat(child)) == unchanged(info))
                        before, size = tree_snapshot(child, device, cutoff)
                        require(open_check(releases / name))
                        after, _ = tree_snapshot(child, device, cutoff)
                        require(before == after and unchanged(os.fstat(child)) == unchanged(info)
                                and unchanged(os.stat(name, dir_fd=root, follow_symlinks=False)) == unchanged(info))
                        counts['eligible'] += 1
                        if not dry_run:
                            hidden = '.ops-cleanup-' + uuid.uuid4().hex
                            os.rename(name, hidden, src_dir_fd=root, dst_dir_fd=root)
                            require(identity(os.stat(hidden, dir_fd=root, follow_symlinks=False)) == identity(info))
                            shutil.rmtree(hidden, dir_fd=root)
                            counts['removed'] += 1
                            counts['allocatedBytes'] += size + info.st_blocks * 512
                    finally:
                        os.close(child)
                except (FileNotFoundError, ValueError):
                    counts['skipped'] += 1
                except OSError:
                    counts['errors'] += 1
    except (OSError, ValueError, TypeError, AttributeError, plistlib.InvalidFileException, subprocess.SubprocessError):
        counts['blocked'] = True
    return counts


# Each deployment root must supply its own provenance, live selector, rollback
# and shared producer-lock policy. Register only reviewed roots here; never add
# deployed trees to the simpler temporary-file rules above.
DEPLOYED_RETENTION_POLICIES = (
    ('ngs-expanded-releases', cleanup_ngs),
    ('ngs-legacy-expanded-releases', cleanup_legacy_ngs),
)


def drain_legacy_backlog(home, *, policy=cleanup_legacy_ngs, pause=time.sleep, monotonic=time.monotonic):
    # Manual first maintenance only. Each batch refreshes current/candidate/
    # open-file proof and releases locks before the next batch. Limit total
    # work per invocation as well so this is not a background polling loop.
    deadline = monotonic() + 8 * 60
    totals = dict(removed=0, eligible=0, allocatedBytes=0, errors=0,
                  scanned=0, eligibleAllocatedBytes=0, batches=0)
    last = {}
    for _ in range(32):
        if monotonic() >= deadline:
            break
        last = policy(home, dry_run=False)
        totals['batches'] += 1
        for key in totals:
            if key != 'batches':
                totals[key] += last[key]
        if last['blocked'] or last['errors'] or not last['deferred'] or not last['removed']:
            break
        pause(0.25)
    return {**last, **totals}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--drain-legacy-backlog', action='store_true',
                      help='manual bounded legacy NGS maintenance; never used by launchd')
    args = parser.parse_args()
    home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
    report = cleanup(home / 'Workspaces', dry_run=args.dry_run)
    for name, policy in DEPLOYED_RETENTION_POLICIES:
        report[name] = drain_legacy_backlog(home) if (
            args.drain_legacy_backlog and policy is cleanup_legacy_ngs
        ) else policy(home, dry_run=args.dry_run)
    print(json.dumps({'dryRun': args.dry_run, 'drainLegacyBacklog': args.drain_legacy_backlog,
                      'retentionDays': RETENTION_DAYS, 'rules': report}, sort_keys=True))
    return int(any(rule['errors'] or rule.get('blocked') for rule in report.values()))


if __name__ == '__main__':
    raise SystemExit(main())
