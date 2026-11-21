"""Narrow, authenticated atomic writer. Never exposes arbitrary filesystem paths."""
import base64
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import tempfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, unquote

PREFIX = 'canonical-files/binari/knowledge/'

class Conflict(Exception): pass

class Store:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
    def path(self, key):
        if not isinstance(key, str) or not key.startswith(PREFIX): raise ValueError('namespace')
        relative = key[len(PREFIX):]
        parts = relative.split('/')
        if not relative or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', p) or p in ('.', '..') for p in parts): raise ValueError('path')
        if relative not in ('overview.json',) and not re.fullmatch(r'categories/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}/(?:metadata\.json|entries/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.json)', relative): raise ValueError('namespace')
        path = self.root
        for p in parts:
            path = path / p
            if path.is_symlink(): raise ValueError('symlink')
        return path
    @contextmanager
    def lock(self):
        fd = os.open(self.root / '.writer.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally: os.close(fd)
    def get(self, key):
        with self.lock(): return self.path(key).read_bytes()
    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        # Persist directory additions as well as the final entry before acknowledgement.
        parent = path.parent
        while parent != self.root.parent:
            fd = os.open(parent, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
            if parent == self.root: break
            parent = parent.parent
        fd, name = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(content); f.flush(); os.fsync(f.fileno())
            os.replace(name, path)
            fd = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally:
            if os.path.exists(name): os.unlink(name)
    def snapshot_unlocked(self):
        documents, hashes = {}, {}
        for path in sorted(self.root.rglob('*.json')):
            relative = path.relative_to(self.root).as_posix()
            if relative.startswith('.'): continue
            path = self.path(PREFIX + relative)
            body = path.read_bytes()
            documents[relative] = json.loads(body)
            hashes[relative] = hashlib.sha256(body).hexdigest()
        digest = hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        return {'sourceVersion':digest,'manifestHash':digest,'documents':documents,'hashes':hashes}
    def snapshot(self):
        with self.lock(): return self.snapshot_unlocked()
    def receipt_path(self, request_id):
        if not isinstance(request_id,str) or not request_id: raise ValueError('requestId')
        path = self.root / '.requests'
        if path.is_symlink(): raise ValueError('symlink')
        path = path / (hashlib.sha256(request_id.encode()).hexdigest()+'.json')
        if path.is_symlink(): raise ValueError('symlink')
        return path
    def recover_unlocked(self, request_id):
        record = json.loads(self.receipt_path(request_id).read_bytes())
        if record['status'] == 'prepared':
            path = self.path(record['key'])
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == record['afterHash']:
                record['status']='committed'
                self.write(self.receipt_path(request_id),json.dumps(record).encode())
        return record
    def receipt(self, request_id):
        with self.lock(): return self.recover_unlocked(request_id)
    def cas(self, key, expected, content, request_id=None, input_hash=None, source_version=None):
        value=json.loads(content)
        if not isinstance(value, dict): raise ValueError('JSON object required')
        json.dumps(value, allow_nan=False)
        with self.lock():
            path = self.path(key)
            after_hash = hashlib.sha256(content).hexdigest()
            if request_id:
                if not input_hash or not source_version: raise ValueError('request metadata')
                rp = self.receipt_path(request_id)
                if rp.exists():
                    previous = self.recover_unlocked(request_id)
                    if previous['inputHash'] != input_hash or previous['key'] != key or previous['afterHash'] != after_hash: raise Conflict('request reused')
                    if previous['status']=='committed': return previous
                if self.snapshot_unlocked()['sourceVersion'] != source_version: raise Conflict('stale source')
            actual = path.read_bytes() if path.exists() else None
            if actual != expected: raise Conflict('stale file')
            record={'requestId':request_id,'inputHash':input_hash,'key':key,'afterHash':after_hash,'status':'prepared'}
            if request_id: self.write(rp,json.dumps(record).encode())
            self.write(path, content)
            record['status']='committed'
            if request_id: self.write(rp,json.dumps(record).encode())
            return record

def handler(store, token):
    if len(token) < 32: raise ValueError('dedicated token must be at least 32 characters')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass # no tokens, paths, or payloads in logs
        def reply(self, status, value, raw=False):
            body = value if raw else json.dumps(value).encode()
            self.send_response(status); self.send_header('Content-Type', 'application/octet-stream' if raw else 'application/json')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def dispatch(self):
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                return self.reply(401, {'error':'UNAUTHORIZED'})
            try:
                url = urlsplit(self.path)
                if self.command == 'GET':
                    if url.path == '/v1/asset' and hasattr(store,'asset'): return self.reply(200,store.asset(parse_qs(url.query)['id'][0]))
                    if url.path == '/v1/snapshot': return self.reply(200, store.snapshot())
                    if url.path.startswith('/v1/requests/'): return self.reply(200, store.receipt(unquote(url.path[len('/v1/requests/'):])))
                    if url.path == '/v1/capabilities': return self.reply(200, {'atomicCAS':not hasattr(store,'batch'), 'lockedBatch':hasattr(store,'batch'), 'namespace':getattr(store,'namespace','canonical-files/binari/npcs/' if hasattr(store,'batch') else PREFIX)})
                    if url.path == '/v1/object': return self.reply(200, store.get(parse_qs(url.query)['key'][0]), True)
                elif self.command == 'POST':
                    if self.headers.get('Transfer-Encoding'): raise ValueError('content length required')
                    length = int(self.headers['Content-Length'])
                    if length < 0: raise ValueError('length')
                    data = json.loads(self.rfile.read(length))
                    decode = lambda value: base64.b64decode(value, validate=True)
                    if url.path == '/v1/asset' and hasattr(store,'put_asset'):
                        return self.reply(200,store.put_asset(data['assetId'],decode(data['base64'])))
                    if url.path == '/v1/delete-character' and hasattr(store,'delete_character'):
                        return self.reply(200,store.delete_character(data['requestId'],data['inputHash'],data['characterId'],data['expectedSourceVersion'],data['metadataAfter']))
                    if url.path == '/v1/batch' and hasattr(store, 'batch'):
                        return self.reply(200, store.batch(data['requestId'], data['inputHash'], data['files'], data['readSet']))
                    if url.path == '/v1/cas':
                        record = store.cas(data['key'], None if data['expectedBase64'] is None else decode(data['expectedBase64']), decode(data['base64']), data['requestId'], data['inputHash'], data['expectedSourceVersion'])
                        return self.reply(200,record)
                    else: return self.reply(404, {'error':'NOT_FOUND'})
                    return self.reply(200, {'ok':True})
                return self.reply(404, {'error':'NOT_FOUND'})
            except Conflict: return self.reply(409, {'error':'CONFLICT'})
            except FileNotFoundError: return self.reply(404, {'error':'NOT_FOUND'})
            except (ValueError, KeyError, TypeError): return self.reply(400, {'error':'INVALID_REQUEST'})
            except OSError: return self.reply(503, {'error':'STORAGE_FAILURE'})
        do_GET = dispatch
        do_POST = dispatch
    return Handler

if __name__ == '__main__':
    token = Path(os.environ['WRITER_TOKEN_FILE']).read_text().strip()
    store_class = Store
    if os.environ.get('WRITER_SCOPE') == 'npc':
        from npc_store import NPCStore
        store_class = NPCStore
    elif os.environ.get('WRITER_SCOPE') == 'concept':
        from concept_store import ConceptStore
        store_class = ConceptStore
    elif os.environ.get('WRITER_SCOPE') == 'motion-presets':
        from motion_store import MotionStore
        store_class = MotionStore
    server = ThreadingHTTPServer((os.environ.get('WRITER_BIND', '127.0.0.1'), int(os.environ.get('WRITER_PORT', '8093'))), handler(store_class(os.environ['WRITER_ROOT']), token))
    server.serve_forever()
