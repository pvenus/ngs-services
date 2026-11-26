# Insert this fragment before DEPLOYED_RETENTION_POLICIES. It uses the shared
# runner's audited filesystem primitives, directory snapshot and open probe.
from datetime import datetime


def sre_worker(name, entry):
    return ('worker-' + name, 'com.vinus.workflow.' + name + '-worker',
            'workflows-github-', entry)


SRE_WORKERS = (
    sre_worker('document', 'document/cli.mjs'),
    sre_worker('file', 'file/cli.mjs'),
    sre_worker('image', 'image/image-cli.mjs'),
    sre_worker('animation', 'animation/animation-cli.mjs'),
    sre_worker('postprocess', 'postprocess/postprocess-cli.mjs'),
    sre_worker('audio', 'candidate-audio-resident/audio-cli.mjs'),
    ('worker-document-2', 'com.vinus.workflow.document-worker-2',
     'workflows-document2-github-', 'document/cli.mjs'),
    *(('worker-image-' + str(slot), 'com.vinus.workflow.image-worker-' + str(slot),
       'workflows-image' + str(slot) + '-github-', 'image/image-cli.mjs') for slot in (2, 3, 4)),
)
SRE_BACKENDS = tuple(
    (target, label, 'ngs-services-github-', entry) for target, label, entry in (
        ('backend-stack', 'com.projectbs.backend-stack', 'runtime/stack.py'),
        ('request-service', 'com.vinus.ngs.request-service', 'request-service/request-service.mjs'),
        ('workflow-planner', 'com.vinus.workflow.planner', 'workflow-planner/cli.mjs'),
    )
)
SRE_OPS = (('ops', 'com.vinus.npcgen.ops', 'ops-only-github-', 'ops-service/server.py'),)
SRE_RECORDS = {
    'observed.json': None,
    'ready.json': 'ops.deploy-target-ready.v1',
    'candidate.json': 'ops.mac-target-candidate.v1',
    'last-result.json': 'ops.deploy-target-result.v1',
    'equivalent.json': 'ops.deploy-target-equivalent.v1',
    'worker-source-decision.json': 'ops.worker-source-decision.v1',
    'retry-ready.json': 'ops.worker-manual-retry-ready.v1',
}


@contextmanager
def sre_locks(paths, control):
    with ExitStack() as stack:
        for path in paths:
            parent = stack.enter_context(open_directory(path.parent))
            parent_state = os.fstat(parent)
            require(parent_state.st_uid == os.geteuid() and stat.S_IMODE(parent_state.st_mode) == 0o700)
            fd = os.open(path.name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                    and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
                    and identity(info) == identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)))
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd = stack.enter_context(open_directory(control))
        require(os.fstat(fd).st_uid == os.geteuid())
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not os.path.lexists(control / 'active.json'))
        yield


def sre_refs(value, root):
    # Colon-delimited environment paths are also valid literal references.
    if isinstance(value, str):
        prefix = str(root) + '/'
        names = re.findall(re.escape(prefix) + r'([a-zA-Z0-9._-]+)(?=[/:\s,;"\']|$)', value)
        require(len(names) == value.count(prefix))
        return set(names)
    if isinstance(value, (tuple, list)):
        return set().union(*(sre_refs(item, root) for item in value))
    if isinstance(value, dict):
        return set().union(*(sre_refs(item, root) for item in value.values()))
    return set()


def sre_name(path, root, prefix, entry):
    require(isinstance(path, str))
    rel = Path(path).relative_to(root)
    require(len(rel.parts) >= 2 and re.fullmatch(re.escape(prefix) + r'[a-f0-9]{7,40}', rel.parts[0])
            and '/'.join(rel.parts[1:]) == entry and path == str(root / rel))
    return rel.parts[0]


def sre_healthy(specs):
    lookup = {'backend-stack': 'pds', 'request-service': 'rs'}
    names = {lookup.get(rec[0], rec[0].removeprefix('worker-') + (
        '-worker' if rec[0].startswith('worker-') else '')) for rec in specs}
    # Worker slot labels place the slot suffix after "worker".
    names = {re.sub(r'^(.+)-([234])-worker$', r'\1-worker-\2', name) for name in names}
    if names == {'ops'}:
        names = set()
    url = 'http://127.0.0.1:8090/api/service-status'
    with build_opener(ProxyHandler({})).open(url, timeout=4) as response:
        require(response.status == 200 and response.geturl() == url)
        data = json.loads(response.read(1024 * 1024))
    rows = data.get('services')
    require(isinstance(rows, list) and rows)
    values = {row.get('serverName'): row.get('state') for row in rows}
    require(len(values) == len(rows) and all(values.get(name) == 'connected' for name in names))


def sre_live(home, root, specs, *, launch=launch_info, health=sre_healthy):
    agents = home / 'Library/LaunchAgents'
    domain = 'gui/' + str(os.geteuid())
    description = launch(domain)
    require('\n\tservices = {\n' in description)
    rows = description.split('\n\tservices = {\n', 1)[1].split('\n\t}', 1)[0]
    labels = set(re.findall(r'^\s*\d+\s+\S+\s+(com\.(?:vinus|projectbs)\.[\w.-]+)\s*$', rows, re.M))
    protected = set()
    installed = {}
    for folder in (agents, Path('/Library/LaunchAgents'), Path('/Library/LaunchDaemons')):
        try:
            with open_directory(folder) as fd:
                names = [n for n in os.listdir(fd) if n.endswith('.plist')]
        except FileNotFoundError:
            continue
        for name in names:
            plist = plistlib.loads(private_read(folder / name, external=(folder != agents)))
            refs = sre_refs(plist, root)
            protected.update(refs)
            if folder == agents:
                installed[name] = plist
    required = {label: rec for rec in specs for label in (rec[1],)}
    require(set(required) <= labels)
    current = {}
    for label in sorted(labels):
        report = launch(domain + '/' + label)
        protected.update(sre_refs(report, root))
        if label not in required:
            continue
        target, _, prefix, entry = required[label]
        plist = installed[label + '.plist']
        args = plist.get('ProgramArguments')
        require(plist.get('Label') == label and isinstance(args, list) and len(args) >= 2)
        entries = [a for a in args if isinstance(a, str) and a.endswith('/' + entry)]
        require(len(entries) == 1)
        name = sre_name(entries[0], root, prefix, entry)
        require(re.search(r'^\s*pid = [1-9][0-9]*\s*$', report, re.M) and
                '\n\tstate = running\n' in report and entries[0] in report)
        current[target] = name
        protected.add(name)
    health(specs)
    return current, protected


def sre_records(control, root, specs, current):
    protected = set()
    good = {}
    for target, _, prefix, entry in specs:
        path = control / 'targets' / target
        observed = record(path / 'observed.json')
        require(isinstance(observed.get('sha'), str) and SHA.fullmatch(observed['sha']))
        for name, schema in SRE_RECORDS.items():
            try:
                data = observed if name == 'observed.json' else record(path / name)
            except FileNotFoundError:
                continue
            require(schema is None or (data.get('schemaVersion') == schema and data.get('target') == target))
            protected.update(sre_refs(data, root))
            marks = [data]
            if 'observed' in data:
                require(isinstance(data['observed'], dict))
                marks.append(data['observed'])
            for mark in marks:
                for key in ('sha', 'observedSha', 'attemptedSha'):
                    if key in mark:
                        require(isinstance(mark[key], str) and SHA.fullmatch(mark[key]))
                        protected.add(prefix + mark[key])
            # observed.json is an operator/deployer observation, not a candidate
            # guess. Keep all such references regardless of order, and require
            # an actual on-disk verified prior before pruning that prefix.
            previous = []
            if name == 'observed.json':
                previous.append(data)
            if name == 'last-result.json':
                previous.append(data.get('observed', {}))
            for item in previous:
                sha, stamp = item.get('sha'), item.get('deployedAt')
                if not (isinstance(sha, str) and SHA.fullmatch(sha) and isinstance(stamp, str)):
                    continue
                datetime.fromisoformat(stamp[:-1] + '+00:00' if stamp.endswith('Z') else stamp)
                rel = prefix + sha
                if rel != current[target] and (root / rel).is_dir() and not (root / rel).is_symlink():
                    try:
                        private_read(root / rel / entry, limit=8 * 1024 * 1024)
                    except FileNotFoundError:
                        continue
                    good.setdefault(prefix, set()).add(rel)
    return protected, good


def sre_filex(control, prefix):
    status = control / 'targets' / 'filex-writer'
    with open_directory(status) as fd:
        backups = [x for x in os.listdir(fd) if re.fullmatch(r'unified-before-[a-f0-9]{40}\.applied\.json', x)]
    protected = set()
    for name in ['unified-applied.json', *backups]:
        data = record(status / name)
        require(isinstance(data.get('sha'), str) and SHA.fullmatch(data['sha']))
        protected.add(prefix + data['sha'])
    return protected


def sre_ops_pins(updater, root, current, *, read=record):
    installed = read(updater / 'installed.json')
    transaction = read(updater / 'transaction.json')
    protected = set()
    prior = set()
    for role, data in [('installed', installed), ('old', transaction['old']), ('new', transaction['new'])]:
        sha, directory = data.get('sha'), data.get('directory')
        require(isinstance(sha, str) and SHA.fullmatch(sha) and isinstance(directory, str))
        name = 'ops-only-github-' + sha
        require(directory == str(root / name / 'ops-service'))
        if role in ('installed', 'new'):
            require(name == current)
        else:
            require(name != current and (root / name).is_dir() and not (root / name).is_symlink())
            private_read(root / name / 'ops-service/server.py', limit=8 * 1024 * 1024)
            prior.add(name)
        protected.add(name)
    for n in ('initial-ops.plist', 'prior.plist', 'candidate.plist'):
        try:
            private = private_read(updater / n)
        except FileNotFoundError:
            continue
        protected.update(sre_refs(plistlib.loads(private), root))
    return protected, {'ops-only-github-': prior}


def sre_scan(root, specs, protected, good, cutoff, counts, *, dry_run, open_check):
    require(shutil.rmtree.avoids_symlink_attacks)
    patterns = {t[2] for t in specs}
    with open_directory(root) as fd:
        parent = os.fstat(fd)
        require(parent.st_uid == os.geteuid() and not parent.st_mode & 0o022)
        for name in sorted(os.listdir(fd)):
            prefix = next((p for p in patterns if name.startswith(p) and SHA.fullmatch(name[len(p):])), None)
            if prefix is None:
                continue
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if (name in protected or not stat.S_ISDIR(info.st_mode)
                        or info.st_uid != os.geteuid() or info.st_dev != parent.st_dev
                        or info.st_mtime_ns > cutoff):
                    counts['skipped'] += 1
                    continue
                if not good.get(prefix):
                    counts['heldNoRollback'] += 1
                    continue
                child = os.open(name, DIRECTORY_FLAGS, dir_fd=fd)
                try:
                    require(unchanged(os.fstat(child)) == unchanged(info))
                    # Prepared runtime entry paths are fixed by the producer.
                    # Incomplete/foreign trees with matching name are not releases.
                    for _, _, p, entry in specs:
                        if p == prefix:
                            private_read(root / name / entry, limit=8 * 1024 * 1024)
                    before, size = tree_snapshot(child, parent.st_dev, cutoff)
                    require(open_check(root / name))
                    after, _ = tree_snapshot(child, parent.st_dev, cutoff)
                    require(before == after and unchanged(os.fstat(child)) == unchanged(info)
                            and unchanged(os.stat(name, dir_fd=fd, follow_symlinks=False)) == unchanged(info))
                    counts['eligible'] += 1
                    if not dry_run:
                        hidden = '.ops-cleanup-' + uuid.uuid4().hex
                        os.rename(name, hidden, src_dir_fd=fd, dst_dir_fd=fd)
                        require(identity(os.stat(hidden, dir_fd=fd, follow_symlinks=False)) == identity(info))
                        shutil.rmtree(hidden, dir_fd=fd)
                        counts['removed'] += 1
                        counts['allocatedBytes'] += size + info.st_blocks * 512
                finally:
                    os.close(child)
            except (FileNotFoundError, ValueError):
                counts['skipped'] += 1
            except OSError:
                counts['errors'] += 1


def sre_cleanup(home, kind, *, now_ns=None, dry_run=False, live=sre_live, open_check=no_open_release):
    counts = dict(removed=0, eligible=0, skipped=0, allocatedBytes=0, errors=0, blocked=False, heldNoRollback=0)
    cutoff = (time.time_ns() if now_ns is None else now_ns) - RETENTION_DAYS * NANOSECONDS_PER_DAY
    control = home / CONTROL
    updater = home / 'Workspaces/OPS/runtime/ops-self-update'
    workspace, specs, prep = {
        'workers': ('Workers', SRE_WORKERS, 'prepare-workers.lock'),
        'backend': ('BackendServices', SRE_BACKENDS, 'prepare-services.lock'),
        'ops': ('BackendServices', SRE_OPS, None),
    }[kind]
    root = home / f'Workspaces/{workspace}/code/releases'
    locks = ([control / 'prepare-ops-candidates.lock', control / prep] if prep else
             [updater / 'cycle.lock']) + [control / 'cycle.lock']
    try:
        with sre_locks(locks, control):
            current, protected = live(home, root, specs)
            pins, good = (sre_ops_pins(updater, root, current['ops']) if kind == 'ops' else
                          sre_records(control, root, specs, current))
            protected |= pins
            if kind == 'backend':
                protected |= sre_filex(control, 'ngs-services-github-')
            sre_scan(root, specs, protected, good, cutoff, counts, dry_run=dry_run, open_check=open_check)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, plistlib.InvalidFileException, subprocess.SubprocessError):
        counts['blocked'] = True
    return counts


def cleanup_worker_releases(home, **opts):
    return sre_cleanup(home, 'workers', **opts)


def cleanup_backend_releases(home, **opts):
    return sre_cleanup(home, 'backend', **opts)


def cleanup_ops_releases(home, **opts):
    return sre_cleanup(home, 'ops', **opts)
