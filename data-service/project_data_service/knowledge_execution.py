"""Request-owned knowledge worker execution and verified, recoverable scratch retirement.

The worker receives only a JSON request/snapshot, never Filex paths or credentials.
PDS retains one compressed recovery copy before removing a completed workspace.
Legacy knowledge-authoring caches are outside this namespace and never swept.
"""
import contextlib, fcntl, hashlib, io, json, os, shutil, subprocess, sys, tarfile, tempfile, re
from pathlib import Path
from .knowledge_requests import canonical, digest

SCHEMA = 'ngs.knowledge-execution.v1'

def build_output(request, snapshot, authorer=None):
    from .knowledge_worker import build_files
    from .knowledge_category_removal import current
    trace = None
    worker_request = request
    if 'authoring' in request:
        from .knowledge_authoring import authored_request
        if authorer is None:
            raise ValueError('KNOWLEDGE_EXECUTION_AUTHOR_REQUIRED')
        worker_request, trace = authored_request(request, current(snapshot['documents'], request), authorer)
    files = build_files(worker_request, snapshot)
    return {'schemaVersion': SCHEMA, 'inputHash': digest(request),
            'sourceVersion': snapshot['sourceVersion'], 'snapshotHash':digest(snapshot), 'files': files,
            'outputHash': digest(files), 'authoringTrace': trace}

class KnowledgeExecution:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink(): raise ValueError('KNOWLEDGE_SCRATCH_SYMLINK')
        for name in ('requests', 'locks', 'receipts'):
            (self.root / name).mkdir(exist_ok=True, mode=0o700)
            if (self.root / name).is_symlink(): raise ValueError('KNOWLEDGE_SCRATCH_SYMLINK')

    @contextlib.contextmanager
    def locked(self, request, namespace=''):
        ident = digest(request)
        path = self.root / 'locks' / (namespace + ident + '.lock')
        if path.is_symlink(): raise ValueError('KNOWLEDGE_SCRATCH_SYMLINK')
        fd=os.open(path,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'a') as stream:
            try: fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise ValueError('KNOWLEDGE_EXECUTION_BUSY') from None
            try: yield ident, self.root / 'requests' / ident
            finally: fcntl.flock(stream, fcntl.LOCK_UN)

    def execute(self, request, snapshot, authorer=None):
        with self.locked(request) as (ident, folder):
            if (self.root / 'receipts' / (ident + '.json')).exists():
                raise ValueError('KNOWLEDGE_EXECUTION_RETIRED_NO_REPLAY')
            if folder.is_symlink(): raise ValueError('KNOWLEDGE_SCRATCH_SYMLINK')
            folder.mkdir(exist_ok=True, mode=0o700)
            self.read_workspace(folder)  # reject unknown paths/links before any write
            output = folder / 'output.json'
            if output.is_symlink(): raise ValueError('KNOWLEDGE_SCRATCH_SYMLINK')
            if output.exists():
                value = self.validate(json.loads(output.read_text()), request, snapshot)
                terminal = folder/'terminal.json'
                if terminal.is_symlink(): raise ValueError('KNOWLEDGE_SCRATCH_SYMLINK')
                # A validated atomically committed output is preparation evidence even
                # if recording terminal status previously failed. No model is replayed.
                self.write_atomic(terminal,canonical({'status':'prepared','outputHash':value['outputHash']}))
                return value
            if (folder / 'started.json').exists():
                raise ValueError('KNOWLEDGE_EXECUTION_OUTCOME_UNKNOWN_NO_REEXECUTION')
            if any(folder.iterdir()): raise ValueError('KNOWLEDGE_SCRATCH_UNEXPECTED_CONTENT')
            envelope = {'schemaVersion': SCHEMA, 'request': request, 'snapshot': snapshot}
            self.write_new(folder / 'input-manifest.json',canonical({'inputHash':ident,'snapshotHash':digest(snapshot),'sourceVersion':snapshot['sourceVersion']}))
            with self.open_new(folder / 'started.json') as stream:
                stream.write(canonical({'inputHash': ident, 'sourceVersion': snapshot['sourceVersion']}))
            try:
                # Explicit test injection only; production always uses the process boundary.
                if authorer is not None:
                    value = build_output(request, snapshot, authorer)
                else:
                    package_root = str(Path(__file__).resolve().parent.parent)
                    env = {k: v for k, v in os.environ.items() if k in ('HOME', 'USER', 'LOGNAME', 'TMPDIR', 'CODEX_HOME')}
                    env.update(PYTHONPATH=package_root, PYTHONDONTWRITEBYTECODE='1', PATH='/usr/bin:/bin:/opt/homebrew/bin')
                    with self.open_new(folder / 'stderr.log') as err:
                        result = subprocess.run([sys.executable, '-B', '-m', 'project_data_service.knowledge_execution_worker'],
                            input=canonical(envelope), text=True, cwd=folder, env=env,
                            stdout=subprocess.PIPE, stderr=err, timeout=330)
                    if result.returncode:
                        raise ValueError('KNOWLEDGE_EXECUTION_FAILED: ' + result.stdout[-2000:])
                    value = json.loads(result.stdout)
                self.validate(value, request, snapshot)
                temporary = folder / 'output.pending'
                self.write_new(temporary,canonical(value)); os.replace(temporary, output)
                self.write_atomic(folder / 'terminal.json',canonical({'status': 'prepared', 'outputHash': value['outputHash']}))
                return value
            except Exception as error:
                self.write_atomic(folder / 'terminal.json',canonical({'status': 'failed', 'error': str(error)[:2000]}))
                raise

    @staticmethod
    def validate(value, request, snapshot):
        if (not isinstance(value, dict) or value.get('schemaVersion') != SCHEMA or
            value.get('inputHash') != digest(request) or value.get('sourceVersion') != snapshot['sourceVersion'] or value.get('snapshotHash') != digest(snapshot) or
            not isinstance(value.get('files'), list) or not value['files'] or
            value.get('outputHash') != digest(value['files'])):
            raise ValueError('KNOWLEDGE_EXECUTION_OUTPUT_INVALID')
        return value

    def finalize(self, request, receipt, store):
        if (receipt.get('verified') is not True or receipt.get('requestId') != request['requestId'] or
                receipt.get('inputHash') != digest(request) or not receipt.get('sourceVersion')):
            raise ValueError('KNOWLEDGE_CLEANUP_VERIFIED_RECEIPT_REQUIRED')
        with self.locked(request) as (ident, folder):
            marker = self.root / 'receipts' / (ident + '.json')
            if not folder.exists():
                return {'state': 'cleaned' if marker.exists() else 'not-created', 'recoverableArchive': marker.exists()}
            if folder.is_symlink(): raise ValueError('KNOWLEDGE_SCRATCH_SYMLINK')
            with store.lock, store.connection:
                store.connection.execute('CREATE TABLE IF NOT EXISTS knowledge_execution_archives(id TEXT PRIMARY KEY, archive_hash TEXT NOT NULL, archive BLOB NOT NULL, receipt_json TEXT NOT NULL)')
            with store.lock:
                old = store.connection.execute('SELECT archive_hash,archive FROM knowledge_execution_archives WHERE id=?', (ident,)).fetchone()
            files = self.read_workspace(folder)
            if old:
                # A prior cleanup may have removed a subset before interruption.
                blob = bytes(old[1]); archive_hash = old[0]
                original = self.verify_archive(blob, archive_hash)
                if any(name not in original or original[name] != data for name,data in files.items()):
                    raise ValueError('KNOWLEDGE_ARCHIVE_EQUIVALENCE_FAILED')
            else:
                original = files
            expected = json.loads(original['started.json'])
            if expected.get('inputHash') != ident: raise ValueError('KNOWLEDGE_SCRATCH_BINDING_INVALID')
            terminal = json.loads(original['terminal.json'])
            if terminal.get('status') != 'prepared': raise ValueError('KNOWLEDGE_SCRATCH_NOT_PREPARED')
            output = json.loads(original['output.json'])
            if output.get('inputHash') != ident or output.get('outputHash') != digest(output.get('files')):
                raise ValueError('KNOWLEDGE_SCRATCH_OUTPUT_INVALID')
            if not any(digest(item.get('body')) == receipt.get('fileHash') for item in output['files']):
                raise ValueError('KNOWLEDGE_CLEANUP_OUTPUT_RECEIPT_MISMATCH')
            if not old:
                buffer = io.BytesIO()
                with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
                    for name, data in original.items():
                        info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o600
                        archive.addfile(info, io.BytesIO(data))
                payload = buffer.getvalue(); archive_hash = hashlib.sha256(payload).hexdigest()
                with store.lock, store.connection:
                    store.connection.execute('INSERT INTO knowledge_execution_archives VALUES(?,?,?,?)',
                        (ident, archive_hash, payload, canonical({k:v for k,v in receipt.items() if k!='entry'})))
            # Read the durable equivalent back before touching any local byte.
            with store.lock:
                row = store.connection.execute('SELECT archive_hash,archive FROM knowledge_execution_archives WHERE id=?', (ident,)).fetchone()
            blob = bytes(row[1]); restored = self.verify_archive(blob, row[0])
            if restored != original: raise ValueError('KNOWLEDGE_ARCHIVE_EQUIVALENCE_FAILED')
            if not marker.exists():
                with self.open_new(marker) as stream: stream.write(canonical({'inputHash':ident,'archiveHash':row[0],'publicationVerified':True}))
            elif json.loads(marker.read_text()).get('archiveHash') != row[0]:
                raise ValueError('KNOWLEDGE_CLEANUP_TOMBSTONE_CONFLICT')
            # Never delete an unarchived or modified file, including on crash recovery.
            if self.read_workspace(folder) != files: raise ValueError('KNOWLEDGE_SCRATCH_CHANGED')
            shutil.rmtree(folder)
            return {'state':'cleaned','recoverableArchive':True,'archiveHash':row[0], 'archiveBytes':len(blob),'filesRemoved':len(files)}

    @staticmethod
    def open_new(path):
        flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0)
        return os.fdopen(os.open(path,flags,0o600),'w')

    @classmethod
    def write_new(cls,path,text):
        with cls.open_new(path) as stream: stream.write(text)

    @staticmethod
    def write_atomic(path,text):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError('KNOWLEDGE_SCRATCH_SYMLINK')
        fd,name=tempfile.mkstemp(prefix='.status-',dir=path.parent)
        try:
            with os.fdopen(fd,'w') as stream: stream.write(text)
            os.replace(name,path)  # replaces link itself, never follows it
        finally:
            if os.path.exists(name): os.unlink(name)

    @staticmethod
    def read_workspace(folder):
        files = {}; total = 0
        for path in sorted(folder.rglob('*')):
            if path.is_symlink(): raise ValueError('KNOWLEDGE_SCRATCH_SYMLINK')
            name=path.relative_to(folder).as_posix()
            top_files={'input-manifest.json','started.json','output.json','terminal.json','stderr.log','output.pending'}
            author_files={'prompt.txt','schema.json','started.json','terminal.json','response.json','events.jsonl','stderr.log'}
            parts=Path(name).parts
            allowed=(len(parts)==1 and (parts[0] in top_files or parts[0]=='authoring')) or (len(parts) in (2,3) and parts[0]=='authoring' and re.fullmatch('[0-9a-f]{64}',parts[1]) and (len(parts)==2 or parts[2] in author_files|{'workspace'}))
            if not allowed: raise ValueError('KNOWLEDGE_SCRATCH_UNEXPECTED_CONTENT')
            if path.is_dir():
                if name in top_files or (len(parts)==3 and parts[2] in author_files): raise ValueError('KNOWLEDGE_SCRATCH_UNEXPECTED_DIRECTORY')
                continue
            if not path.is_file(): raise ValueError('KNOWLEDGE_SCRATCH_SPECIAL_FILE')
            data = path.read_bytes(); total += len(data)
            if total > 64 * 1024 * 1024 or len(files) >= 256: raise ValueError('KNOWLEDGE_ARCHIVE_LIMIT')
            files[path.relative_to(folder).as_posix()] = data
        return files

    @staticmethod
    def verify_archive(blob, archive_hash):
        if hashlib.sha256(blob).hexdigest() != archive_hash: raise ValueError('KNOWLEDGE_ARCHIVE_HASH_MISMATCH')
        with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as archive:
            members = archive.getmembers()
            if (len(members)>256 or sum(m.size for m in members)>64*1024*1024 or
                any(not m.isfile() or m.name.startswith('/') or '..' in Path(m.name).parts for m in members) or
                len({m.name for m in members})!=len(members)):
                raise ValueError('KNOWLEDGE_ARCHIVE_INVALID')
            return {m.name: archive.extractfile(m).read() for m in members}
