"""One Filex writer selector and reversible paired writer/PDS cutover."""
from datetime import UTC, datetime
from pathlib import Path
import os
import plistlib
import re
import stat

from filex_writer_apply import IMAGE, PROJECT, PROJECT_PATH
from mac_target_apply import TARGETS, validate_and_rewrite
from prepare_ngs_release import digest, json_bytes, json_load, matches, need, shape

NAME = 'unified-writer'
LABEL = TARGETS['backend-stack'].label
LEGACY = ('npc-writer', 'concept-writer', 'motion-writer', 'media-writer')
NETWORK = 'writer_loopback'
PORT = '8095'
VOLUMES = {
    'npc': 'ngs-npc-writer-secret', 'concept': 'ngs-concept-writer-secret',
    'motion': 'ngs-motion-writer-secret', 'media': 'ngs_filex_media_writer_secret',
}
KEY = 'PDS_FILEX_WRITER_CONFIG'
WRITER_CONFIG = 'ngs.filex-writer-scopes.v1'
PDS_CONFIG = 'pds-filex-writer.v1'
SCOPE = re.compile(r'[a-z][a-z0-9-]{0,39}')
BIND = re.compile(r'[a-z][a-z0-9-]{0,63}')


def registry(raw):
    obj = json_load(raw)
    need(shape(obj, 'schemaVersion scopes') and obj['schemaVersion'] == WRITER_CONFIG
         and type(obj['scopes']) is list and 1 <= len(obj['scopes']) <= 64, 'FILEX_SCOPE_REGISTRY')
    rows = obj['scopes']
    for row in rows:
        need(shape(row, 'id folder credential profile')
             and matches(SCOPE, row['id']) and matches(BIND, row['folder'])
             and row['credential'] in VOLUMES and matches(SCOPE, row['profile']), 'FILEX_SCOPE_REGISTRY')
    for attr in ('id', 'folder'):
        need(len({x[attr] for x in rows}) == len(rows), 'FILEX_SCOPE_REGISTRY')
    return rows


def compose(image, raw):
    need(matches(IMAGE, image), 'FILEX_IMAGE')
    rows = registry(raw)
    credentials = sorted({x['credential'] for x in rows})
    return {'services': {NAME: {
        'image': image, 'user': '501:20', 'platform': 'linux/arm64',
        'restart': 'unless-stopped', 'read_only': True, 'cap_drop': ['ALL'],
        'security_opt': ['no-new-privileges:true'],
        'ports': ['127.0.0.1:' + PORT + ':8093'],
        'networks': [NETWORK], 'volumes': [
            {'type': 'bind', 'source': str(PROJECT_PATH / x['folder']),
             'target': '/canonical/' + x['folder'], 'read_only': False,
             'bind': {'create_host_path': False}} for x in rows
        ] + [
            {'type': 'volume', 'source': 'writer_' + x, 'target': '/run/secrets/' + x,
             'read_only': True} for x in credentials
        ],
    }}, 'volumes': {'writer_' + x: {'external': True, 'name': VOLUMES[x]} for x in credentials}}


def guard_mounts(model, raw):
    """Container must have exactly the generated scoped mounts, identity, loopback and sandbox."""
    rows = registry(raw)
    expected = {'/canonical/' + x['folder']: ('bind', str(PROJECT_PATH / x['folder']), True) for x in rows}
    expected.update({'/run/secrets/' + k: ('volume', VOLUMES[k], False) for k in {x['credential'] for x in rows}})
    host, cfg = model['HostConfig'], model['Config']
    labels = cfg.get('Labels', {})
    need(model.get('Name') == '/' + PROJECT + '-' + NAME + '-1'
         and labels.get('com.docker.compose.project') == PROJECT
         and labels.get('com.docker.compose.service') == NAME
         and matches(IMAGE, model.get('Image'))
         and cfg.get('User') == '501:20' and cfg.get('Entrypoint') in (None, [])
         and cfg.get('Cmd') == ['python', '-B', '/app/server.py'] and cfg.get('WorkingDir') == '/app'
         and not any(x.startswith(('WRITER_SCOPE=', 'WRITER_ROOT=', 'WRITER_TOKEN_FILE=')) for x in cfg['Env'])
         and host.get('ReadonlyRootfs') is True and host.get('Privileged') is False
         and host.get('CapDrop') == ['ALL'] and not host.get('CapAdd')
         and host.get('SecurityOpt') in (['no-new-privileges:true'], ['no-new-privileges=true'])
         and not any(host.get(k) for k in ('Devices', 'DeviceRequests', 'PidMode', 'VolumesFrom'))
         and host.get('PortBindings') == {'8093/tcp': [{'HostIp': '127.0.0.1', 'HostPort': PORT}]}
         and host.get('RestartPolicy', {}).get('Name') == 'unless-stopped'
         and set(model['NetworkSettings']['Networks']) == {PROJECT + '_' + NETWORK}, 'FILEX_RUNTIME_SCOPE')
    mounts = model['Mounts']
    observed = {m['Destination']: (m['Type'], m.get('Name') if m['Type'] == 'volume' else m['Source'], m['RW'])
                for m in mounts}
    need(len(observed) == len(mounts) and observed == expected
         and all(m['Type'] != 'bind' or m.get('Propagation', 'rprivate') == 'rprivate'
                 for m in mounts), 'FILEX_RUNTIME_MOUNTS')


def render_pds(raw, credential_policy):
    """Derive the client scope array from the writer's only folder registry."""
    policy=json_load(credential_policy)
    need(shape(policy,'schemaVersion credentials')
         and policy['schemaVersion']=='ops.filex-writer-credentials.v1'
         and shape(policy['credentials'],' '.join(VOLUMES)), 'FILEX_PDS_TOKEN_SCOPE')
    paths=policy['credentials']
    need(all(isinstance(p,str) and Path(p).is_absolute() and '..' not in Path(p).parts
             for p in paths.values()) and len(set(paths.values()))==len(paths),
         'FILEX_PDS_TOKEN_SCOPE')
    return json_bytes({'schemaVersion':PDS_CONFIG,'projectId':'binari',
                       'endpoint':'http://127.0.0.1:'+PORT,
                       'scopes':[{'id':r['id'],'tokenFile':paths[r['credential']]} for r in registry(raw)]})


def backend_selector(prior, release, home, pds_path, pds_raw, registry_raw):
    """Only pin release and reviewed PDS route config, do not alter existing secret/storage settings."""
    pds = json_load(pds_raw)
    rows = registry(registry_raw)
    need(shape(pds, 'schemaVersion projectId endpoint scopes')
         and pds['schemaVersion'] == PDS_CONFIG and pds['projectId'] == 'binari'
         and pds['endpoint'] == 'http://127.0.0.1:' + PORT and type(pds['scopes']) is list
         and len(pds['scopes']) == len(rows), 'FILEX_PDS_CONFIG')
    scope_to_credential = {x['id']: x['credential'] for x in rows}
    observed = {}
    for x in pds['scopes']:
        need(shape(x, 'id tokenFile') and x['id'] in scope_to_credential and x['id'] not in observed
             and isinstance(x['tokenFile'], str) and Path(x['tokenFile']).is_absolute()
             and '..' not in Path(x['tokenFile']).parts, 'FILEX_PDS_CONFIG')
        observed[x['id']] = x['tokenFile']
    forward, reverse = {}, {}
    for k, path in observed.items():
        credential = scope_to_credential[k]
        need(forward.setdefault(credential, path) == path and reverse.setdefault(path, credential) == credential,
             'FILEX_PDS_TOKEN_SCOPE')
    need(pds_path.is_absolute(), 'FILEX_PDS_CONFIG')
    result = plistlib.loads(validate_and_rewrite(prior, release, TARGETS['backend-stack'], home / 'Workspaces'))
    need(result['ProgramArguments'][2:] == ['--storage-root', str(home / 'Workspaces/PDS/data')],
         'FILEX_PDS_STORAGE')
    env = result.setdefault('EnvironmentVariables', {})
    env[KEY] = str(pds_path)
    return plistlib.dumps(result)


def prove_backend_predecessor(home, control, selector, applied):
    """Allow a separately OPS-deployed, release-only selector advance, not drift.

    The immutable paired receipt stays untouched. For a newer selector, require
    the exact independent OPS deployment record, preserved scope-binding bytes,
    and a lossless round-trip through the two fixed release paths. This is only
    predecessor evidence; capture still proves the loaded selector, actual writer
    identity and mounts, and PDS's live scope fingerprint separately.
    """
    from prepare_ngs_release import DIGEST, SHA, private_directory, private_file
    code='FILEX_PDS_PREDECESSOR_UNPROVED'
    def read_proof(path, limit):
        # Same proof reader on the candidate preparer and the minimal apply
        # runner, which intentionally lacks the NGS preparation module.
        private_file(path,0o600)
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            a=os.fstat(stream.fileno())
            b=path.lstat()
            need(stat.S_ISREG(a.st_mode) and stat.S_IMODE(a.st_mode)==0o600 and
                 a.st_uid==os.getuid() and a.st_nlink==1 and 0<a.st_size<=limit and
                 (a.st_dev,a.st_ino)==(b.st_dev,b.st_ino),code)
            raw=stream.read(limit+1)
            c=os.fstat(stream.fileno())
            need(len(raw)==a.st_size and (a.st_mtime_ns,a.st_ctime_ns)==(c.st_mtime_ns,c.st_ctime_ns),code)
            return raw
    old=applied.get('sha')
    expected=applied.get('backendSelectorSha256')
    need(matches(SHA,old) and matches(DIGEST,expected),code)
    if digest(selector)==expected:
        return
    raw=read_proof(control/'targets'/'backend-stack'/'last-result.json',8192)
    recorded=json_load(raw)
    need(shape(recorded,'schemaVersion target attemptedSha state finishedAt observed')
         and recorded['schemaVersion']=='ops.deploy-target-result.v1'
         and recorded['target']=='backend-stack' and recorded['state']=='deployed'
         and shape(recorded['observed'],'sha deployedAt'),code)
    new=recorded['observed']['sha']
    stamp=r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z'
    need(matches(SHA,new) and new!=old and recorded['attemptedSha']==new
         and all(type(v) is str and re.fullmatch(stamp,v) is not None
                 for v in (recorded['observed']['deployedAt'],recorded['finishedAt']))
         and type(applied.get('deployedAt')) is str,code)
    try:
        paired_at=datetime.fromisoformat(applied['deployedAt'])
        adopted_at=datetime.fromisoformat(recorded['observed']['deployedAt'])
        finish=datetime.fromisoformat(recorded['finishedAt'])
        need(paired_at.tzinfo is not None and paired_at<adopted_at<=finish,code)
    except (ValueError,TypeError):
        need(False,code)
    target=TARGETS['backend-stack']
    base=home/'Workspaces'
    releases=base/target.workspace/'code/releases'
    old_release=releases/(target.release_prefix+old)
    new_release=releases/(target.release_prefix+new)
    private_directory(old_release)
    private_directory(new_release)
    value=plistlib.loads(selector)
    args=value.get('ProgramArguments') if type(value) is dict else None
    env=value.get('EnvironmentVariables') if type(value) is dict else None
    pds=control/'filex-writer-prepared'/(old+'.pds-scopes.json')
    need(type(args) is list and args==['/opt/homebrew/bin/python3.12',str(new_release/target.entry),
         '--storage-root',str(base/'PDS/data')] and type(env) is dict and env.get(KEY)==str(pds)
         and matches(DIGEST,applied.get('pdsSha256'))
         and digest(read_proof(pds,65536))==applied['pdsSha256'],code)
    paired=validate_and_rewrite(selector,old_release,target,base)
    need(digest(paired)==expected and validate_and_rewrite(paired,new_release,target,base)==selector,code)


def loaded_backend_selector(report, original, path):
    """Match loaded args/path and either exact declared CWD or exact absence."""
    lines=report.splitlines()
    try:
        start=lines.index('\targuments = {')+1
        end=lines.index('\t}',start)
    except ValueError:
        return False
    directories=[line.removeprefix('\tworking directory = ') for line in lines if line.startswith('\tworking directory = ')]
    expected=[original['WorkingDirectory']] if 'WorkingDirectory' in original else []
    return (lines.count('\tpath = '+str(path))==1
            and lines[start:end]==['\t\t'+arg for arg in original['ProgramArguments']]
            and directories==expected)


def managed_overlay(image, raw):
    return json_bytes(compose(image, raw))


class CutoverFailure(RuntimeError):
    def __init__(self, phase, rollback):
        super().__init__(f'{phase}:{rollback}')
        self.phase, self.rollback = phase, rollback


def cutover(host, plan):
    """Fixed sequence; host persists signed selection/journal and proves actual state.

    Called under the existing Filex/OPS lock, after exact candidate validation.
    Every restoration phase checks identity instead of deleting containers/images.
    """
    prior = host.capture(plan)
    if 'priorFingerprint' in plan:
        need(live_fingerprint(prior)==plan['priorFingerprint'], 'FILEX_PRIOR_CHANGED')
    host.arm(plan, prior)  # durable before even an asynchronous stop call
    phase = 'pause_backend'
    try:
        host.pause_backend(prior)
        host.prove_backend_held()
        phase = 'hold_writers'
        host.stop_legacy(prior)
        host.prove_writers_held(prior)
        phase = 'select_writer'
        host.select_writer(plan, prior)
        host.start_writer(plan)
        host.prove_writer(plan, prior)
        phase = 'start_backend'
        host.select_backend(plan, prior)
        host.start_backend()
        host.prove_backend(plan, prior)
        host.prove_writer(plan, prior)
        phase = 'commit'
        host.commit(plan, prior, datetime.now(UTC))
    except Exception:
        # Observe/finish ambiguous started/stopped calls; never overlap writers.
        try:
            host.ensure_uncommitted(plan,prior)
            host.pause_backend(prior)
            host.prove_backend_held()
            host.stop_writer(plan, prior)
            host.prove_successor_held(plan, prior)
            host.restore_writer_selector(plan, prior)
            host.restore_legacy(prior)
            host.prove_prior_writer(prior)
            host.restore_backend_selector(plan, prior)
            host.start_backend()
            host.prove_prior_backend(prior)
            host.clear_rolled_back(plan, prior)
        except Exception:
            # Do not clear the pre-start journal when ownership/rollback is unproved.
            raise CutoverFailure(phase, 'failed') from None
        raise CutoverFailure(phase, 'restored') from None


class ManagedHost:
    """Bound Docker/launchd operations for the existing owner-click target.

    The target preparer must supply a verified plan and own the existing locks.
    Does not install helpers, select a Docker daemon, or accept executable paths.
    """
    def __init__(self, home, control, docker, launch):
        self.docker, self.launch = docker, launch
        self.control, self.home = control, home
        self.target = control/'targets'/'filex-writer'
        self.base = home/'Workspaces/Filex/code/compose.yaml'
        self.overlay = self.target/'unified.compose.json'
        self.armed = self.target/'unified-in-progress.json'
        self.applied = self.target/'unified-applied.json'
        self.selector = home/'Library/LaunchAgents'/(LABEL+'.plist')

    def _record(self, name):
        ids = self.docker.call('container','ls','-a','-q','--no-trunc','--filter','name=^/'+name+'$').decode().split()
        need(len(ids)<=1 and all(re.fullmatch('[a-f0-9]{64}',i) for i in ids), 'FILEX_CONTAINER_ID')
        if not ids:
            return None
        rows = json_load(self.docker.call('container','inspect',ids[0]))
        need(type(rows) is list and len(rows)==1 and rows[0].get('Name')=='/'+name
             and rows[0].get('Id')==ids[0], 'FILEX_CONTAINER_ID')
        return rows[0]

    def _name(self, service):
        return PROJECT+'-'+service+'-1'

    def _rows(self, services):
        return {s:self._record(self._name(s)) for s in services}

    @staticmethod
    def _running(row):
        return row is not None and row['State']['Running'] is True

    def _legacy_scope(self, service, row):
        expected = {
            'npc-writer': ('npc-owned','npcs','npc','8094'),
            'concept-writer': ('concept','concept','concept','8095'),
            'motion-writer': ('motion-presets','motion-presets','motion','8096'),
            'media-writer': ('media-artifacts','media-artifacts','media','8097'),
        }
        scope, folder, credential, port = expected[service]
        host, cfg, ms = row['HostConfig'], row['Config'], row['Mounts']
        labels = cfg['Labels']
        want = {'/canonical': ('bind',str(PROJECT_PATH/folder),True),
                '/run/secrets': ('volume',VOLUMES[credential],False)}
        if scope=='concept':
            want['/audio']=('bind',str(PROJECT_PATH/'audio'),True)
        got = {m['Destination']:(m['Type'],m.get('Name') if m['Type']=='volume' else m['Source'],m['RW']) for m in ms}
        need(labels.get('com.docker.compose.project')==PROJECT
             and labels.get('com.docker.compose.service')==service
             and [s for s in cfg['Env'] if s.startswith('WRITER_SCOPE=')]==['WRITER_SCOPE='+scope]
             and len(got)==len(ms) and got==want
             and host.get('PortBindings')=={'8093/tcp':[{'HostIp':'127.0.0.1','HostPort':port}]}
             and host.get('ReadonlyRootfs') is True and host.get('Privileged') is False
             and host.get('CapDrop')==['ALL'] and not host.get('CapAdd')
             and set(row['NetworkSettings']['Networks'])=={PROJECT+'_'+NETWORK},
             'FILEX_PRIOR_SCOPE')

    def capture(self, plan):
        from prepare_ngs_release import private_file, private_directory, tree_sha
        private_directory(self.target)
        private_file(self.selector)
        private_file(self.base)
        need(not os.path.lexists(self.armed)
             and os.path.lexists(self.overlay)==os.path.lexists(self.applied)
             and digest(self.base.read_bytes())==plan['composeSha256'], 'FILEX_RUNTIME_SELECTOR')
        private_directory(plan['release'])
        need(tree_sha(plan['release'])==plan['releaseTreeSha256'], 'FILEX_PDS_RELEASE')
        prior = self.selector.read_bytes()
        need(digest(prior)==plan['priorPlistSha256'], 'FILEX_PDS_SELECTOR')
        pds_path=plan['pdsPath']
        private_file(pds_path,0o600)
        raw=pds_path.read_bytes()
        need(digest(raw)==plan['pdsSha256'], 'FILEX_PDS_CONFIG')
        successor=backend_selector(prior,plan['release'],self.home,pds_path,raw,plan['registry'])
        stage = plan['propStageRoot']
        private_directory(stage)
        need(stage == self.home/'Workspaces/PDS/data/.filex-prop-stage', 'FILEX_PROP_STAGE')
        successor = plistlib.loads(successor)
        successor['EnvironmentVariables'].update(PDS_WORKFLOW_PROP_STATIC='1',
                                                  PDS_WORKFLOW_PROP_STAGE_ROOT=str(stage))
        successor = plistlib.dumps(successor)
        for scope in registry(plan['registry']):
            path=PROJECT_PATH/scope['folder']
            need(path.is_dir() and path.resolve(strict=True)==path
                 and path.stat().st_uid==os.getuid(), 'FILEX_CANONICAL_DIRECTORY')
        for v in json_load(raw)['scopes']:
            private_file(Path(v['tokenFile']),0o600)  # no credential access required here
        rows=self._rows((*LEGACY,NAME,'filex'))
        need(self._running(rows['filex']), 'FILEX_RUNTIME_DRIFT')
        active={}
        if os.path.lexists(self.applied):
            for f in (self.applied,self.overlay):
                private_file(f,0o600)
            previous_raw=self.applied.read_bytes()
            previous=json_load(previous_raw)
            old_registry_path=self.control/'filex-writer-prepared'/(previous['sha']+'.scopes.json')
            private_file(old_registry_path,0o600)
            previous_registry=old_registry_path.read_bytes()
            current=self.overlay.read_bytes()
            prove_backend_predecessor(self.home,self.control,prior,previous)
            need(previous['registrySha256']==digest(previous_registry)
                 and previous['writerOverlaySha256']==digest(current)
                 and current==managed_overlay(previous['imageId'],previous_registry)
                 and self._running(rows[NAME]) and rows[NAME]['Image']==previous['imageId']
                 and all(not self._running(rows[k]) for k in LEGACY), 'FILEX_ACTIVE_WRITER')
            guard_mounts(rows[NAME],previous_registry)
            self._prove_scopes(rows[NAME],previous_registry)
            old_health=json_load(self.launch.get(8030,'/health'))
            need(old_health.get('filexWriterScopesSha256')==previous['pdsSha256'],'FILEX_PDS_WRITER_PROOF')
            active={'beforeOverlay':current,'beforeApplied':previous_raw,'beforeRegistry':previous_registry,
                    'beforeImageId':previous['imageId'],'beforePdsSha':previous['pdsSha256']}
        else:
            need(rows[NAME] is None and all(self._running(rows[k]) for k in LEGACY), 'FILEX_RUNTIME_DRIFT')
            for k in LEGACY:
                self._legacy_scope(k,rows[k])
            self._legacy_proof(rows)
        pid, report=self.launch.launch_report(LABEL)
        original=plistlib.loads(prior)
        need(pid is not None and loaded_backend_selector(report,original,self.selector), 'FILEX_PDS_NOT_LOADED')
        return {'ids':{x:r['Id'] for x,r in rows.items() if r is not None},
                'images':{x:r['Image'] for x,r in rows.items() if r is not None},
                'pid':pid, 'old':prior, 'new':successor,**active}

    def _legacy_proof(self, rows):
        from filex_writer_apply import PROBE
        media_probe = PROBE.split(b'with client.open(req, timeout=2) as reply:')[0]
        media_probe = media_probe.replace(b'/v1/capabilities', b'/v1/media-artifacts/assets/probe')
        media_probe += b"""
try:
    client.open(req, timeout=2).close()
    raise SystemExit(5)
except HTTPError as e:
    if e.code != 400 or json.loads(e.read(512)) != {'error':'INVALID_REQUEST'}:
        raise
print('media-ok')
"""
        for k in LEGACY:
            need(self._running(rows[k]), 'FILEX_LEGACY_HEALTH')
            raw=self.docker.call('exec','-i',rows[k]['Id'],'/usr/local/bin/python','-B','-',
                                 data=media_probe if k=='media-writer' else PROBE,timeout=8,code='FILEX_LEGACY_HEALTH')
            if k=='media-writer':
                need(raw==b'media-ok\n','FILEX_LEGACY_HEALTH')
                continue
            namespace={'npc-writer':'npcs','concept-writer':'concept','motion-writer':'motion-presets','media-writer':'media-artifacts'}[k]
            need(json_load(raw).get('namespace')=='canonical-files/binari/'+namespace+'/', 'FILEX_LEGACY_HEALTH')

    def arm(self, plan, prior):
        from prepare_ngs_release import atomic_write
        need(not os.path.lexists(self.armed), 'FILEX_RUNTIME_SELECTOR')
        backup=self.target/('unified-backend-predecessor-'+plan['sha']+'.plist')
        if os.path.lexists(backup):
            from prepare_ngs_release import private_file
            private_file(backup,0o600)
            need(backup.read_bytes()==prior['old'], 'FILEX_PDS_SELECTOR')
        else:
            atomic_write(backup,prior['old'])
        if prior.get('beforeOverlay') is not None:
            for kind,key in (('compose.json','beforeOverlay'),('applied.json','beforeApplied')):
                p=self.target/('unified-before-'+plan['sha']+'.'+kind)
                if os.path.lexists(p):
                    from prepare_ngs_release import private_file
                    private_file(p,0o600)
                    need(p.read_bytes()==prior[key], 'FILEX_RUNTIME_SELECTOR')
                else:
                    atomic_write(p,prior[key])
        # Exact prior selectors are private owner artifacts, not surfaced to OPS logs.
        # Do not record raw selectors or credential contents merely for observability.
        atomic_write(self.armed,json_bytes({'sha':plan['sha'],'oldPid':prior['pid'],
            'legacyIds':prior['ids'],'legacyImageIds':prior['images'],
            'oldSelectorSha256':digest(prior['old']),'newSelectorSha256':digest(prior['new']),
            'successorOverlaySha256':digest(managed_overlay(plan['imageId'],plan['registry']))}))

    def pause_backend(self, prior):
        need(self.selector.read_bytes() in (prior['old'],prior['new']), 'FILEX_PDS_SELECTOR')
        self.launch.remove(LABEL)

    def prove_backend_held(self):
        import errno
        import socket
        from mac_target_apply import PORTS
        def unbound():
            if self.launch.job(LABEL)[0]:
                return False
            for port in PORTS:
                with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
                    sock.settimeout(.3)
                    code=sock.connect_ex(('127.0.0.1',port))
                if code!=errno.ECONNREFUSED:
                    return False
            return True
        need(self.launch.wait_for(unbound,30), 'FILEX_PDS_DRAIN')

    def stop_legacy(self, prior):
        selected=(NAME,) if prior.get('beforeImageId') else LEGACY
        for svc,row in self._rows(selected).items():
            need(row is not None and row['Id']==prior['ids'][svc], 'FILEX_PRIOR_CHANGED')
            if self._running(row):
                self.docker.call('container','stop','--time','30',row['Id'],timeout=40)

    def prove_writers_held(self, prior):
        rows=self._rows((*LEGACY,NAME,'filex'))
        stopped = LEGACY + ((NAME,) if prior.get('beforeImageId') else ())
        need(all((rows[k] is None and k not in prior['ids']) or
                 (rows[k] is not None and rows[k]['Id']==prior['ids'].get(k)
                  and rows[k]['State']['Running'] is False and not rows[k]['State'].get('Restarting'))
                 for k in stopped)
             and (prior.get('beforeImageId') or rows[NAME] is None) and self._running(rows['filex'])
             and rows['filex']['Id']==prior['ids']['filex'], 'FILEX_WRITERS_NOT_DRAINED')

    def select_writer(self, plan, prior):
        from prepare_ngs_release import atomic_write
        old=self.overlay.read_bytes() if os.path.lexists(self.overlay) else None
        need(old==prior.get('beforeOverlay'), 'FILEX_RUNTIME_SELECTOR')
        atomic_write(self.overlay,managed_overlay(plan['imageId'],plan['registry']))

    def start_writer(self, plan):
        raw=managed_overlay(plan['imageId'],plan['registry'])
        need(self.overlay.read_bytes()==raw, 'FILEX_RUNTIME_SELECTOR')
        self.docker.call('compose','--project-name',PROJECT,'--project-directory',str(self.base.parent),
                         '-f',str(self.base),'-f',str(self.overlay),'up','-d','--no-deps','--no-build',
                         '--pull','never',NAME,timeout=60)

    def _prove_scopes(self, row, raw):
        source = ('SCOPES='+repr(registry(raw))+'\n').encode() + b'''
import json,os,stat
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler,Request,build_opener
client=build_opener(ProxyHandler({}))
def probe(path,t=None):
    req=Request('http://127.0.0.1:8093'+path,headers={'Authorization':'Bearer '+t} if t else {})
    try:
        with client.open(req,timeout=2) as r:
            return r.status,json.loads(r.read(8193))
    except HTTPError as error:
        return error.code,None
def token(credential):
    p=Path('/run/secrets')/credential/'writer_token'
    st=p.lstat()
    assert p.resolve()==p and stat.S_ISREG(st.st_mode) and 32<=st.st_size<=512
    assert st.st_uid==os.getuid() and not st.st_mode&0o077
    return p.read_text().strip()
assert probe('/v1/capabilities')[0]==404
tokens={k:token(k) for k in sorted({r['credential'] for r in SCOPES})}
for r in SCOPES:
    path='/v1/scopes/'+r['id']+'/capabilities'
    assert probe(path)[0]==401
    status,body=probe(path,tokens[r['credential']])
    assert status==200 and body['namespace']=='canonical-files/binari/'+r['folder']+'/'
    wrong=next((v for k,v in tokens.items() if k!=r['credential']),None)
    assert wrong is None or probe(path,wrong)[0]==401
print('scopes-ok')
'''
        result=self.docker.call('exec','-i',row['Id'],'/usr/local/bin/python','-B','-',data=source,
                                timeout=15,code='FILEX_ACTIVE_WRITER')
        need(result==b'scopes-ok\n','FILEX_ACTIVE_WRITER')

    def prove_writer(self, plan, prior):
        rows=self._rows((*LEGACY,NAME,'filex'))
        need(all((rows[k] is None and k not in prior['ids']) or
                 (rows[k] is not None and rows[k]['Id']==prior['ids'].get(k)
                  and rows[k]['State']['Running'] is False) for k in LEGACY)
             and self._running(rows['filex']) and rows['filex']['Id']==prior['ids']['filex']
             and self._running(rows[NAME]) and rows[NAME]['Image']==plan['imageId'],
             'FILEX_ACTIVE_WRITER')
        guard_mounts(rows[NAME],plan['registry'])
        self._prove_scopes(rows[NAME],plan['registry'])
        # Actual PDS scoped HTTP capability proof is checked after backend start.

    def select_backend(self, plan, prior):
        from prepare_ngs_release import atomic_write
        need(self.selector.read_bytes()==prior['old'], 'FILEX_PDS_SELECTOR')
        atomic_write(self.selector,prior['new'])

    def start_backend(self):
        self.launch.start(LABEL,self.selector)

    def prove_backend(self, plan, prior):
        self.launch.wait_healthy(TARGETS['backend-stack'],datetime.now(UTC),prior['pid'])
        health=json_load(self.launch.get(8030,'/health'))
        need(health.get('filexWriterScopesSha256')==plan['pdsSha256'],
             'FILEX_PDS_WRITER_PROOF')
        # Same PDS workflow surfaces relied on by production read/claim.
        for endpoint in ('/v1/npcs/ownership/workflow-preset','/v1/props/ownership/workflow-package'):
            need(json_load(self.launch.get(8030,endpoint)).get('enabled') is True,'FILEX_PDS_WRITER_PROOF')

    def commit(self, plan, prior, now):
        from prepare_ngs_release import atomic_write
        path=self.applied
        old=path.read_bytes() if os.path.lexists(path) else None
        need(old==prior.get('beforeApplied') and self.selector.read_bytes()==prior['new'], 'FILEX_RUNTIME_SELECTOR')
        atomic_write(path,json_bytes({'sha':plan['sha'],'imageId':plan['imageId'],'deployedAt':now.isoformat(),
            'registrySha256':digest(plan['registry']),'pdsSha256':plan['pdsSha256'],
            'writerOverlaySha256':digest(self.overlay.read_bytes()),'backendSelectorSha256':digest(prior['new'])}))
        # No fallible mutation follows publishing commit proof except clearing
        # the journal. If interrupted here, the startup bridge refuses to start
        # until owner reconciliation instead of selecting legacy or mixing modes.
        self.armed.unlink()

    def ensure_uncommitted(self, plan, prior):
        current=self.applied.read_bytes() if os.path.lexists(self.applied) else None
        need(current==prior.get('beforeApplied'), 'FILEX_RUNTIME_SELECTOR')

    def stop_writer(self, plan, prior):
        row=self._record(self._name(NAME))
        if row is not None:
            if row['Id']==prior['ids'].get(NAME) and row['Image']==prior.get('beforeImageId'):
                guard_mounts(row,prior['beforeRegistry'])
            else:
                need(row['Image']==plan['imageId'], 'FILEX_ACTIVE_WRITER')
                guard_mounts(row,plan['registry'])
            if self._running(row):
                self.docker.call('container','stop','--time','30',row['Id'],timeout=40)

    def prove_successor_held(self, plan, prior):
        row=self._record(self._name(NAME))
        need(row is None or row['Image'] in (plan['imageId'],prior.get('beforeImageId')) and row['State']['Running'] is False
             and not row['State'].get('Restarting'), 'FILEX_WRITER_ROLLBACK_DRAIN')

    def restore_writer_selector(self, plan, prior):
        from prepare_ngs_release import atomic_write
        actual=self.overlay.read_bytes() if os.path.lexists(self.overlay) else None
        before=prior.get('beforeOverlay')
        need(actual in (before,managed_overlay(plan['imageId'],plan['registry'])), 'FILEX_RUNTIME_SELECTOR')
        if actual!=before:
            if before is None:
                self.overlay.unlink()
            else:
                atomic_write(self.overlay,before)

    def restore_legacy(self, prior):
        if prior.get('beforeImageId'):
            self.start_writer({'imageId':prior['beforeImageId'],'registry':prior['beforeRegistry']})
            return
        for svc,row in self._rows(LEGACY).items():
            need(row is not None and row['Id']==prior['ids'][svc], 'FILEX_PRIOR_CHANGED')
            if not self._running(row):
                self.docker.call('container','start',row['Id'],timeout=35)

    def prove_prior_writer(self, prior):
        if prior.get('beforeImageId'):
            self.prove_writer({'imageId':prior['beforeImageId'],'registry':prior['beforeRegistry']},prior)
            return
        rows=self._rows((*LEGACY,'filex'))
        need(all(rows[k]['Id']==prior['ids'][k] and rows[k]['Image']==prior['images'][k]
                 and self._running(rows[k]) for k in rows),'FILEX_PRIOR_CHANGED')
        self._legacy_proof(rows)

    def restore_backend_selector(self, plan, prior):
        from prepare_ngs_release import atomic_write
        old=self.selector.read_bytes()
        need(old in (prior['old'],prior['new']), 'FILEX_PDS_SELECTOR')
        if old==prior['new']:
            atomic_write(self.selector,prior['old'])

    def prove_prior_backend(self, prior):
        self.launch.wait_healthy(TARGETS['backend-stack'],datetime.now(UTC),prior['pid'])
        need(self.selector.read_bytes()==prior['old'], 'FILEX_PDS_SELECTOR')
        if prior.get('beforePdsSha'):
            need(json_load(self.launch.get(8030,'/health')).get('filexWriterScopesSha256')==prior['beforePdsSha'],
                 'FILEX_PDS_WRITER_PROOF')

    def clear_rolled_back(self, plan, prior):
        self.ensure_uncommitted(plan,prior)
        if not prior.get('beforeImageId'):
            failed=self._record(self._name(NAME))
            if failed is not None:
                need(failed['Image']==plan['imageId'] and failed['State']['Running'] is False,
                     'FILEX_WRITER_ROLLBACK_DRAIN')
                guard_mounts(failed,plan['registry'])
                # Only remove the fresh, stopped failed candidate container;
                # retain its exact local image and all bind/volume data.
                self.docker.call('container','rm',failed['Id'],timeout=15)
        self.armed.unlink()


def startup_bridge(home, control, *, props_legacy=False):
    """Render the reviewed startup bridge with a selected unified branch.

    Old selectors continue to boot legacy only before adoption or on rollback.
    After adoption, even a reboot never starts an inactive legacy writer.
    """
    from filex_writer_apply import startup_bridge as previous
    old = previous(home,control,props=props_legacy)
    needle = b"    if not os.path.lexists(override):\n"
    prefix = '''
    unified = override.parent / 'unified.compose.json'
    proof = override.parent / 'unified-applied.json'
    pending = override.parent / 'unified-in-progress.json'
    if os.path.lexists(pending):
        raise SystemExit(1)
    if os.path.lexists(unified) or os.path.lexists(proof):
        import hashlib
        def private(path):
            current = path.lstat()
            if (path.resolve() != path or not stat.S_ISREG(current.st_mode)
                    or current.st_uid != os.getuid() or stat.S_IMODE(current.st_mode) != 0o600
                    or current.st_nlink != 1):
                raise SystemExit(1)
            return path.read_bytes()
        record = json.loads(private(proof))
        overlay = private(unified)
        if (hashlib.sha256(overlay).hexdigest() != record['writerOverlaySha256']
                or set(json.loads(overlay)['services']) != {'unified-writer'}
                or json.loads(overlay)['services']['unified-writer']['image'] != record['imageId']
                or not re.fullmatch(r'sha256:[a-f0-9]{64}', record['imageId'])):
            raise SystemExit(1)
        subprocess.run(command + ['up', '-d', '--no-deps', '--no-build', '--pull', 'never', 'filex'],check=True)
        subprocess.run(command + ['-f', str(unified), 'up', '-d', '--no-deps', '--no-build',
                                  '--pull', 'never', 'unified-writer'],check=True)
        raise SystemExit(0)
'''.encode().lstrip(b'\n')
    need(old.count(needle)==1, 'FILEX_STARTUP_SELECTOR')
    return old.replace(needle,prefix+needle)

CANDIDATE = 'ops.filex-unified-apply-candidate.v1'
CANDIDATE_FIELDS = ('schemaVersion target sha artifactSha256 imageId composeSha256 startupSha256 '
                    'releaseTreeSha256 priorPlistSha256 pdsSha256 registrySha256 priorFingerprint')


def plan_inputs(home, control, candidate):
    """Construct fixed native paths only, from separately pinned owner/canonical inputs."""
    from prepare_ngs_release import DIGEST, SHA, private_file
    need(shape(candidate,CANDIDATE_FIELDS) and candidate['schemaVersion']==CANDIDATE
         and candidate['target']=='filex-writer' and matches(SHA,candidate['sha'])
         and matches(IMAGE,candidate['imageId'])
         and all(matches(DIGEST,candidate[k]) for k in CANDIDATE_FIELDS.split() if k.endswith('Sha256') or k=='priorFingerprint'),
         'FILEX_CANDIDATE_SHAPE')
    from filex_writer_apply import filex_code
    startup=filex_code(home)/'start-filex.sh'
    private_file(startup)
    raw = startup.read_bytes()
    need(digest(raw)==candidate['startupSha256'] and raw in (
         startup_bridge(home,control), startup_bridge(home,control,props_legacy=True)), 'FILEX_STARTUP_SELECTOR')
    release=home/'Workspaces/BackendServices/code/releases'/('ngs-services-github-'+candidate['sha'])
    pds_path=control/'filex-writer-prepared'/(candidate['sha']+'.pds-scopes.json')
    witness=control/'filex-writer-prepared'/(candidate['sha']+'.scopes.json')
    private_file(witness,0o600)
    registry_raw=witness.read_bytes()
    need(digest(registry_raw)==candidate['registrySha256'], 'FILEX_SCOPE_REGISTRY')
    return {**candidate,'release':release,'pdsPath':pds_path,'registry':registry_raw,
            'propStageRoot':home/'Workspaces/PDS/data/.filex-prop-stage'}


def live_fingerprint(prior):
    return digest(json_bytes({k:prior[k] for k in ('ids','images')}))


def apply_managed(sha, artifact, home, control):
    """Use the existing Filex OPS run-once runner contract; do not invoke directly."""
    import fcntl
    from filex_writer_apply import DockerHost
    from mac_target_apply import LaunchHost
    from prepare_ngs_release import private_directory, private_file
    control=Path(control)
    home=Path(home)
    target=control/'targets'/'filex-writer'
    private_directory(control)
    private_directory(target)
    fds=[]
    try:
        fd=os.open(control,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        fds.append(fd)
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)  # same lock as Filex startup
        paired=control/'targets'/'backend-stack'
        private_directory(paired)
        lock=paired/'.apply.lock'
        fds.append(os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW,0o600))
        private_file(lock,0o600)
        fcntl.flock(fds[-1],fcntl.LOCK_EX|fcntl.LOCK_NB)
        c=target/'candidate.json'
        bound=control/'filex-writer-prepared'/(sha+'.unified-apply.json')
        for p in (c,bound):
            private_file(p,0o600)
        raw=c.read_bytes()
        value=json_load(raw)
        need(raw==bound.read_bytes() and value.get('sha')==sha
             and value.get('artifactSha256')==artifact,'FILEX_CANDIDATE_MOVED')
        plan=plan_inputs(home,control,value)
        host=ManagedHost(home,control,DockerHost(home,control),LaunchHost(os.getuid()))
        need(host.docker.image(plan['imageId'])==plan['imageId'],'FILEX_IMAGE')
        cutover(host,plan)
        result=json_load((target/'unified-applied.json').read_bytes())
        when=datetime.fromisoformat(result['deployedAt']).strftime('%Y-%m-%dT%H:%M:%SZ')
        return {'schemaVersion':'ops.deploy-target-receipt.v1','target':'filex-writer','sha':sha,
                'deployedAt':when,'state':'deployed','health':'verified'}
    finally:
        for fd in reversed(fds):
            os.close(fd)


def prepare_candidate(github, repo, home, control, head, policy, loaded, runner, proof,
                      *, assert_current, docker, services):
    """Normal OPS candidate preparation: immutable, manual-only, no live changes."""
    from prepare_filex_supervised import config
    from prepare_filex_writer import closure, UNIFIED_CLOSURES, UNIFIED_CAP_DIGEST
    from prepare_ngs_local_once import lock, read_optional, read_private, runner_digest
    from prepare_ngs_release import DIGEST, atomic_write, tree_sha
    from prepare_service_candidates import tracked_targets, verify_source
    from filex_writer_apply import DockerHost
    from mac_target_apply import LaunchHost

    need(type(services) is dict and services['proof']['headSha']==head,
         'FILEX_PAIRED_SERVICE_PROOF_UNAVAILABLE')
    release=home/'Workspaces/BackendServices/code/releases'/('ngs-services-github-'+head)
    backend_tree=verify_source(release,tracked_targets(services,repo))
    need(backend_tree==tree_sha(release), 'FILEX_PDS_RELEASE')
    _,blobs=closure(repo,head)
    need(set(blobs) in UNIFIED_CLOSURES and proof['source']['headSha']==head
         and proof['source']['files']=={k:digest(v) for k,v in blobs.items()}
         and proof.get('capabilitiesSha256')==UNIFIED_CAP_DIGEST, 'FILEX_SOURCE_PROOF')
    archive=proof['provenance']['artifactSha256']
    need(matches(DIGEST,archive) and docker.verify_image(proof['imageId']) is True, 'FILEX_IMAGE')
    target=control/'targets'/'filex-writer'
    prepared=control/'filex-writer-prepared'
    candidate=dict(schemaVersion=CANDIDATE,target='filex-writer',sha=head,artifactSha256=archive,
                   imageId=proof['imageId'],composeSha256=policy['composeSha256'],
                   startupSha256=policy['startupSha256'],releaseTreeSha256=backend_tree,
                   registrySha256=digest(blobs['scopes.json']),priorFingerprint='0'*64)
    host=ManagedHost(home,control,DockerHost(home,control),LaunchHost(os.getuid()))
    pds=prepared/(head+'.pds-scopes.json')
    with lock(control/'cycle.lock',create=True):
        need(not os.path.lexists(control/'active.json'),'PREP_ACTIVE_DEPLOYMENT')
        assert_current(head)
        need(config(control)==loaded and runner_digest(target)==runner,'FILEX_PREPARATION_CHANGED')
        raw=read_private(prepared/(head+'.json'),64*1024,mode=0o600)
        need(json_load(raw)==proof,'FILEX_SOURCE_PROOF')
        active=target/'unified-applied.json'
        if os.path.lexists(active) and json_load(read_private(active,8192,mode=0o600)).get('sha')==head:
            saved=json_load(read_private(active,8192,mode=0o600))
            need(saved.get('imageId')==candidate['imageId']
                 and saved.get('registrySha256')==candidate['registrySha256']
                 and read_private(host.overlay,64*1024,mode=0o600)==managed_overlay(saved['imageId'],blobs['scopes.json'])
                 and digest(read_private(host.selector,64*1024,mode=0o600))==saved['backendSelectorSha256']
                 and not os.path.lexists(host.armed), 'FILEX_UNIFIED_RECONCILIATION_REQUIRED')
            return {'target':'filex-writer','sha':head,'state':'current'}
        credential_policy=read_private(control/'filex-writer-credentials.json',8192,mode=0o600)
        pds_raw=render_pds(blobs['scopes.json'],credential_policy)
        if os.path.lexists(pds):
            need(read_private(pds,64*1024,mode=0o600)==pds_raw,'FILEX_PDS_CONFIG')
        else:
            atomic_write(pds,pds_raw)
        candidate['pdsSha256']=digest(pds_raw)
        candidate['priorPlistSha256']=digest(read_private(host.selector,64*1024,mode=0o600))
        witness=prepared/(head+'.scopes.json')
        if os.path.lexists(witness):
            need(read_private(witness,64*1024,mode=0o600)==blobs['scopes.json'],'FILEX_SOURCE_PROOF')
        else:
            atomic_write(witness,blobs['scopes.json'])
        plan=plan_inputs(home,control,candidate)
        candidate['priorFingerprint']=live_fingerprint(host.capture(plan))
        last=read_optional(target/'last-result.json')
        need(not (last and last.get('attemptedSha')==head and last.get('state')=='failed'),
             'FILEX_RECONCILE_REQUIRED')
        path=prepared/(head+'.unified-apply.json')
        body=json_bytes(candidate)
        if os.path.lexists(path):
            need(read_private(path,8192,mode=0o600)==body,'FILEX_PREPARATION_CHANGED')
        else:
            atomic_write(path,body)
        ready={'schemaVersion':'ops.deploy-target-ready.v1','target':'filex-writer','sha':head,
               'artifactSha256':archive,'runnerSha256':runner,'manualOnly':True,
               'verifiedAt':datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}
        assert_current(head)
        atomic_write(target/'candidate.json',body)
        atomic_write(target/'ready.json',json_bytes(ready))
    return {'target':'filex-writer','sha':head,'state':'ready'}


def verify_selected(home, control, repo, head):
    """Recognize the coupled target for later independent Backend preparation."""
    from prepare_filex_writer import closure, UNIFIED_CLOSURES, UNIFIED_CAP_DIGEST
    from prepare_ngs_local_once import read_private
    from filex_writer_apply import DockerHost
    from mac_target_apply import LaunchHost
    target=control/'targets'/'filex-writer'
    p=target/'unified-applied.json'
    if not os.path.lexists(p):
        return False
    raw=read_private(p,8192,mode=0o600)
    applied=json_load(raw)
    need(shape(applied,'sha imageId deployedAt registrySha256 pdsSha256 writerOverlaySha256 backendSelectorSha256'),
         'FILEX_ACTIVE_PROOF_INVALID')
    from prepare_ngs_release import SHA
    need(matches(SHA,applied['sha']), 'FILEX_ACTIVE_PROOF_INVALID')
    proof=json_load(read_private(control/'filex-writer-prepared'/(applied['sha']+'.json'),65536,mode=0o600))
    terminal=json_load(read_private(target/'last-result.json',8192,mode=0o600))
    need(terminal['state']=='deployed' and terminal['observed']['sha']==applied['sha']
         and proof['source']['headSha']==applied['sha']
         and proof['imageId']==applied['imageId'] and proof.get('capabilitiesSha256')==UNIFIED_CAP_DIGEST,
         'FILEX_ACTIVE_PROOF_INVALID')
    _,required=closure(repo,head)
    need(set(required) in UNIFIED_CLOSURES and proof['source']['files']=={k:digest(v) for k,v in required.items()}
         and digest(required['scopes.json'])==applied['registrySha256'],'FILEX_ACTIVE_PROOF_INVALID')
    host=ManagedHost(home,control,DockerHost(home,control),LaunchHost(os.getuid()))
    row=host._record(host._name(NAME))
    need(not os.path.lexists(host.armed) and host._running(row) and row['Image']==applied['imageId']
         and all(not host._running(v) for v in host._rows(LEGACY).values())
         and read_private(host.overlay,65536,mode=0o600)==managed_overlay(applied['imageId'],required['scopes.json'])
         and digest(read_private(host.overlay,65536,mode=0o600))==applied['writerOverlaySha256'],
         'FILEX_ACTIVE_PROOF_INVALID')
    guard_mounts(row,required['scopes.json'])
    host._prove_scopes(row,required['scopes.json'])
    need(json_load(host.launch.get(8030,'/health')).get('filexWriterScopesSha256')==applied['pdsSha256']
         and read_private(p,8192,mode=0o600)==raw, 'FILEX_ACTIVE_PROOF_INVALID')
    return True
