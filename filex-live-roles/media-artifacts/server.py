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
                if hasattr(store,'register_portrait_asset') and url.path.startswith('/v1/portrait-assets'):
                    if url.query or self.headers.get('Origin') is not None:raise ValueError('portrait route')
                    if self.command=='POST' and url.path=='/v1/portrait-assets/register':
                        if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type')!='image/png':raise ValueError('portrait body')
                        header=self.headers.get('X-Portrait-Metadata','')
                        if not 0<len(header)<=24576:raise ValueError('portrait metadata size')
                        meta=json.loads(base64.b64decode(header,validate=True))
                        if set(meta)!={'metadata','guard'}:raise ValueError('portrait envelope')
                        n=self.headers.get('Content-Length','')
                        if not n.isdecimal() or not 0<int(n)<=32*1024*1024:raise ValueError('portrait size')
                        self.connection.settimeout(30);data=self.rfile.read(int(n))
                        if len(data)!=int(n):raise ValueError('portrait truncated')
                        return self.reply(200,store.register_portrait_asset(meta['metadata'],data,meta['guard']))
                    if self.command=='GET':
                        aid=url.path.removeprefix('/v1/portrait-assets/')
                        return self.reply(200,store.portrait_asset(aid))
                    return self.reply(404,{'error':'NOT_FOUND'})
                if hasattr(store,'media_publish'):
                    if url.query: raise ValueError('media query')
                    parts=url.path.strip('/').split('/')
                    if self.command=='GET' and len(parts)==5 and parts[:3]==['v1','media-artifacts','receipts']:
                        return self.reply(200,store.media_receipt(parts[3],parts[4]))
                    if self.command=='GET' and len(parts)==4 and parts[:3]==['v1','media-artifacts','assets']:
                        return self.reply(200,store.media_asset(parts[3]))
                    if self.command=='POST' and url.path=='/v1/media-artifacts/publish':
                        if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('media body')
                        length=self.headers.get('Content-Length','')
                        if not length.isdecimal() or not 0<int(length)<=46*1024*1024:raise ValueError('media body size')
                        raw=self.rfile.read(int(length))
                        if len(raw)!=int(length):raise ValueError('media body incomplete')
                        return self.reply(200,store.media_publish(json.loads(raw)))
                    return self.reply(404,{'error':'NOT_FOUND'})
                if self.command == 'GET':
                    if url.path == '/v1/migration/status' and hasattr(store,'migration_status'): return self.reply(200,store.migration_status(parse_qs(url.query)['requestId'][0]))
                    if url.path == '/v1/asset' and hasattr(store,'asset'): return self.reply(200,store.asset(parse_qs(url.query)['id'][0]))
                    if url.path == '/v1/concept/removed-scope' and hasattr(store,'removed_scope'):
                        with store.lock():return self.reply(200,store.removed_scope(parse_qs(url.query)['backupId'][0]))
                    if url.path == '/v1/backup-preview' and hasattr(store,'backup_preview'): return self.reply(200,store.backup_preview(parse_qs(url.query)['id'][0]))
                    if url.path == '/v1/generation' and hasattr(store,'generation'): return self.reply(200,store.generation(parse_qs(url.query)['npcId'][0]))
                    if url.path == '/v1/generation/staged-delete-fence' and hasattr(store,'staged_delete_fence'):
                        q=parse_qs(url.query,keep_blank_values=True)
                        if set(q)!={'npcId','generationId','requestId'} or any(len(v)!=1 or not v[0] for v in q.values()):raise ValueError('staged fence query')
                        return self.reply(200,store.staged_delete_fence(q['npcId'][0],q['generationId'][0],q['requestId'][0]))
                    if url.path == '/v1/generation/equivalent' and hasattr(store,'current_equivalent'):
                        q=parse_qs(url.query);return self.reply(200,store.current_equivalent(q['npcId'][0],q['generationId'][0],q['parentRequestId'][0],q['sha256'][0]))
                    if url.path == '/v1/generation/publications' and hasattr(store,'publication_receipts'):
                        q=parse_qs(url.query);return self.reply(200,store.publication_receipts(q['npcId'][0],q['generationId'][0],q['parentRequestId'][0]))
                    if url.path == '/v1/snapshot': return self.reply(200, store.snapshot())
                    if url.path.startswith('/v1/requests/'): return self.reply(200, store.receipt(unquote(url.path[len('/v1/requests/'):])))
                    if url.path == '/v1/capabilities': return self.reply(200, {'conceptRecoverableLifecycleV1':hasattr(store,'lifecycle_commit'),'releasedFrameCleanupLeaseV1':hasattr(store,'begin_frame_maintenance'),'npcOwnedSlots':hasattr(store,'generation'),'generationFence':hasattr(store,'generation'),'atomicCAS':not hasattr(store,'batch'), 'lockedBatch':hasattr(store,'batch'), 'namespace':getattr(store,'namespace','canonical-files/binari/npcs/' if hasattr(store,'batch') else PREFIX)})
                    if url.path == '/v1/object': return self.reply(200, store.get(parse_qs(url.query)['key'][0]), True)
                elif self.command == 'POST':
                    if self.headers.get('Transfer-Encoding'): raise ValueError('content length required')
                    length = int(self.headers['Content-Length'])
                    if length < 0: raise ValueError('length')
                    data = json.loads(self.rfile.read(length))
                    decode = lambda value: base64.b64decode(value, validate=True)
                    if hasattr(store,'migration_prepare'):
                        if url.path == '/v1/migration/prepare': return self.reply(200,store.migration_prepare(data))
                        if url.path == '/v1/migration/asset': return self.reply(200,store.migration_asset(data['requestId'],data['token'],data['assetId'],decode(data['base64'])))
                        if url.path == '/v1/migration/activate': return self.reply(200,store.migration_activate(data['requestId'],data['expectedSourceVersion'],data['planHash'],data['backupManifestHash']))
                        if url.path == '/v1/migration/commit': return self.reply(200,store.migration_commit(data['requestId'],data['token']))
                    if hasattr(store,'begin_frame_maintenance') and url.path.startswith('/v1/generation/frame-maintenance/'):
                        action=url.path.rsplit('/',1)[-1]
                        if action=='begin':return self.reply(200,store.begin_frame_maintenance(data))
                        if action=='reserve' and set(data)=={'guard','sha256','reservationId'}:return self.reply(200,store.reserve_frame_maintenance(data['guard'],data['sha256'],data['reservationId']))
                        if action=='release' and set(data)=={'guard','reservationId','reservationToken'}:return self.reply(200,store.release_frame_maintenance(data['guard'],data['reservationId'],data['reservationToken']))
                        if action=='finish' and set(data)=={'guard'}:return self.reply(200,store.finish_frame_maintenance(data['guard']))
                        raise ValueError('maintenance route or schema')
                    if hasattr(store,'generation'):
                        if url.path == '/v1/generation/reserve-equivalent': return self.reply(200,store.reserve_equivalent(data['npcId'],data['generationId'],data['parentRequestId'],data['sha256'],data['reservationId']))
                        if url.path == '/v1/generation/release-equivalent': return self.reply(200,store.release_equivalent(data['npcId'],data['generationId'],data['parentRequestId'],data['reservationId'],data['reservationToken']))
                        if url.path == '/v1/generation/prepare-create': return self.reply(200,store.prepare_create(data['npcId'],data['requestId'],data['expectedSourceVersion']))
                        if url.path == '/v1/generation/prepare-restore': return self.reply(200,store.prepare_restore(data['npcId'],data['requestId'],data['backupId'],data['expectedSourceVersion']))
                        if url.path == '/v1/create-character': return self.reply(200,store.create_character(data['requestId'],data['inputHash'],data['files'],data['readSet'],data['expectedSourceVersion'],data['guard']))
                        if url.path == '/v1/restore-character': return self.reply(200,store.restore_character(data['requestId'],data['inputHash'],data['backupId'],data['expectedSourceVersion'],data['metadataAfter'],data['guard']))
                        if url.path == '/v1/generation/bootstrap': return self.reply(200,store.bootstrap(data['npcId'],data['expectedSourceVersion']))
                        if url.path == '/v1/generation/admit': return self.reply(200,store.admit(data['npcId'],data['generationId'],data['requestId']))
                        if url.path == '/v1/generation/finish': return self.reply(200,store.finish(data['guard']))
                        if url.path == '/v1/generation/stage-delete': return self.reply(200,store.stage_delete(data['npcId'],data['generationId'],data['requestId'],data['expectedSourceVersion']))
                        if url.path == '/v1/generation/prepare-delete': return self.reply(200,store.prepare_delete(data['npcId'],data['generationId'],data['requestId'],data['externalOwnerManifestHash']))
                        if url.path == '/v1/asset': return self.reply(200,store.put_asset(data['assetId'],decode(data['base64']),data['guard']))
                        if url.path == '/v1/batch': return self.reply(200,store.batch(data['requestId'],data['inputHash'],data['files'],data['readSet'],data['guard']))
                        if url.path == '/v1/delete-character': return self.reply(200,store.delete_character(data['requestId'],data['inputHash'],data['characterId'],data['expectedSourceVersion'],data['metadataAfter'],data['guard']))
                    if url.path == '/v1/asset' and hasattr(store,'put_asset'):
                        return self.reply(200,store.put_asset(data['assetId'],decode(data['base64'])))
                    if url.path == '/v1/delete-character' and hasattr(store,'delete_character'):
                        return self.reply(200,store.delete_character(data['requestId'],data['inputHash'],data['characterId'],data['expectedSourceVersion'],data['metadataAfter']))
                    if url.path == '/v1/concept/restore-scope':raise ValueError('MASTER_RECOVERY_FEATURE_REMOVED')
                    if url.path == '/v1/concept/remove-scope' and hasattr(store,'lifecycle_commit'):
                        if set(data)!={'requestId','inputHash','scope','expectedSourceVersion','files','readSet','restoreInfo','backupId'} or (data['backupId']is None)!=(url.path=='/v1/concept/remove-scope'):raise ValueError('concept lifecycle endpoint contract')
                        return self.reply(200,store.lifecycle_commit(data['requestId'],data['inputHash'],data['scope'],data['expectedSourceVersion'],data['files'],data['readSet'],restore_info=data.get('restoreInfo'),backup_id=data.get('backupId')))
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
    # Store modules import server. Preserve one Conflict class in script mode.
    import sys
    sys.modules['server'] = sys.modules[__name__]
    token = Path(os.environ['WRITER_TOKEN_FILE']).read_text().strip()
    store_class = Store
    if os.environ.get('WRITER_SCOPE') == 'media-artifacts':
        from media_store import MediaStore
        store_class = MediaStore
    elif os.environ.get('WRITER_SCOPE') == 'npc-owned':
        from fenced_store import FencedOwnedNPCStore
        store_class = FencedOwnedNPCStore
    elif os.environ.get('WRITER_SCOPE') == 'npc':
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
