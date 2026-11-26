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
from time import monotonic, sleep
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, unquote
from uuid import uuid4

PREFIX = 'canonical-files/binari/knowledge/'
OWNERSHIP_WAIT_SECONDS = 2.0
OWNERSHIP_POLL_SECONDS = 0.025

class Conflict(Exception): pass
class PresetPrewriteConflict(Conflict): pass
class OwnershipBusy(Exception): pass


def _ownership_event(state, wait_id, started):
    # No request payload, filesystem paths, task names, or credentials.
    try:
        print(json.dumps({'event':'filex_writer_ownership','state':state,'waitId':wait_id,
                          'waitMs':round((monotonic()-started)*1000)}), flush=True)
    except (OSError, ValueError):
        pass

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
            # Wait only to acquire ownership. The body has not run, and is not
            # timed out or replayed once it begins. This same on-disk lock still
            # coordinates other Writer processes and existing recovery paths.
            started = monotonic()
            deadline = started + OWNERSHIP_WAIT_SECONDS
            wait_id = None
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if wait_id is None:
                        wait_id = uuid4().hex
                        _ownership_event('waiting', wait_id, started)
                    remaining = deadline - monotonic()
                    if remaining > 0:
                        sleep(min(OWNERSHIP_POLL_SECONDS, remaining))
                    if monotonic() >= deadline:
                        _ownership_event('timeout', wait_id, started)
                        raise OwnershipBusy() from None
            if wait_id is not None:
                _ownership_event('acquired', wait_id, started)
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

def handler(store, token, *, workflow_delete=False, workflow_preset=False, workflow_representative=False, workflow_portrait=False, workflow_idle_plan=False, workflow_idle_portrait=False, workflow_direction_portrait=False, workflow_native_media=False, workflow_shared_plan=False,single_target_delete=None,workflow_npc_folder_delete=False,workflow_npc_planning_update=False,workflow_npc_planning_markdown=False,workflow_npc_planning_add=False,workflow_audio=None,workflow_audio_delete=False,workflow_props=None):
    workflow_combat_pose=(workflow_shared_plan is True and workflow_native_media is True
                          and workflow_portrait is True and workflow_idle_portrait is True
                          and workflow_direction_portrait is True)
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
                if url.path in ('/v1/workflow/props','/v1/workflow/props/read','/v1/workflow/props/asset','/v1/workflow/props/head'):
                    from prop_package_store import PropPackageStore,WRITER,MAX_BODY,keys
                    if not isinstance(workflow_props,PropPackageStore):
                        return self.reply(404,{'error':'NOT_FOUND'})
                    n=self.headers.get('Content-Length','')
                    if (self.command!='POST' or url.query or self.headers.get('Origin') is not None or
                       self.headers.get('Transfer-Encoding') is not None or self.headers.get('Content-Type')!='application/json' or
                       not n.isdecimal() or len(n)>10 or not 0<int(n)<=(MAX_BODY if url.path==WRITER else 4096)):
                        raise ValueError('workflow props transport')
                    self.connection.settimeout(20)
                    raw=self.rfile.read(int(n))
                    if len(raw)!=int(n):raise ValueError('incomplete prop body')
                    def distinct(pairs):
                        result=dict(pairs)
                        if len(result)!=len(pairs):raise ValueError('duplicate prop key')
                        return result
                    body=json.loads(raw.decode('utf-8'),object_pairs_hook=distinct)
                    if url.path==WRITER:return self.reply(200,workflow_props.publish(body))
                    if url.path==WRITER+'/head':
                        if not keys(body,('propId',)):raise ValueError('prop head input')
                        return self.reply(200,{'pointer':workflow_props.head(body['propId'])})
                    if url.path==WRITER+'/read':return self.reply(200,workflow_props.read(body))
                    if not keys(body,('pin','assetId')):raise ValueError('prop asset input')
                    return self.reply(200,workflow_props.read(body['pin'],body['assetId']))
                if url.path in ('/v1/workflow/audio','/v1/workflow/audio/read',
                                '/v1/workflow/audio-separate','/v1/workflow/audio-separate/read',
                                '/v1/workflow/audio-separate/asset'):
                    from workflow_audio_store import AudioResult,Rejected,MAX_BODY,SEPARATE_PATH
                    if not isinstance(workflow_audio,AudioResult) or workflow_audio.store is not store:
                        return self.reply(404,{'error':'NOT_FOUND'})
                    if (self.command!='POST' or url.query or self.headers.get('Origin') is not None or
                        self.headers.get('Transfer-Encoding') is not None or self.headers.get('Content-Type')!='application/json'):
                        raise ValueError('workflow audio transport')
                    n=self.headers.get('Content-Length','')
                    reading=url.path.endswith('/read');asset=url.path.endswith('/asset')
                    if not n.isdecimal() or not 0<int(n)<=(4096 if reading or asset else MAX_BODY):
                        raise ValueError('workflow audio body')
                    self.connection.settimeout(15)
                    raw=self.rfile.read(int(n))
                    if len(raw)!=int(n):raise ValueError('workflow audio incomplete')
                    try:
                        value=json.loads(raw)
                        if url.path.startswith(SEPARATE_PATH):
                            result=(workflow_audio.separation_read(value,asset=asset) if reading or asset
                                    else workflow_audio.separation_publish(value))
                        else:result=(workflow_audio.read if reading else workflow_audio.publish)(value)
                    except Rejected:
                        return self.reply(422,{'error':'WORKFLOW_AUDIO_REJECTED_BEFORE_WRITE'})
                    if asset:
                        self.send_response(200);self.send_header('Content-Type','audio/wav')
                        self.send_header('Content-Length',str(len(result)));self.send_header('Cache-Control','no-store')
                        self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(result)
                        return
                    return self.reply(200,result)
                if url.path in ('/v1/storage/delete-target','/v1/storage/delete-target/cinematic-inventory'):
                    from single_target_delete import PinnedDeleteRejected,SingleTargetDelete
                    ordinary=isinstance(single_target_delete,SingleTargetDelete) and single_target_delete.store is store
                    audio=workflow_audio_delete is True and workflow_audio is not None and workflow_audio.store is store
                    from media_store import CinematicProjectStore
                    cinematic=isinstance(store,CinematicProjectStore)
                    background=hasattr(store,'remove_background_file')
                    if not (ordinary or audio or workflow_props is store or cinematic):
                        return self.reply(404,{'error':'NOT_FOUND'})
                    inventory=url.path.endswith('/cinematic-inventory')
                    if inventory and not cinematic:return self.reply(404,{'error':'NOT_FOUND'})
                    headers=self.headers
                    if (self.command!='POST' or url.query or headers.get('Origin') is not None or
                        headers.get('Transfer-Encoding') is not None or headers.get('Content-Type')!='application/json'):
                        raise ValueError('storage delete writer transport')
                    n=headers.get('Content-Length','')
                    if not n.isdecimal() or not 0<int(n)<=4096:raise ValueError('storage delete writer body')
                    self.connection.settimeout(5)
                    body=self.rfile.read(int(n))
                    if len(body)!=int(n):raise ValueError('storage delete writer incomplete')
                    command=json.loads(body)
                    target=command.get('target',{}) if type(command) is dict else {}
                    if audio and type(target) is dict and target.get('root')=='canonical-files/binari/audio':
                        return self.reply(200,workflow_audio.remove_result(command))
                    if cinematic and (inventory or (type(target) is dict and target.get('root')=='canonical-files/binari/cinematics')):
                        from media_store import CinematicTargetDelete
                        try:
                            deleter=CinematicTargetDelete(store)
                            if inventory:
                                if type(command)is not dict or set(command)!={'sourceSha256'}:raise ValueError('cinematic inventory request')
                                return self.reply(200,deleter.inventory(command['sourceSha256']))
                            return self.reply(200,deleter.remove(command))
                        except PinnedDeleteRejected:return self.reply(409,{'error':'PINNED_DELETE_REJECTED_BEFORE_MOVE'})
                    if workflow_props is store:
                        try:return self.reply(200,workflow_props.remove_package(command))
                        except PinnedDeleteRejected:return self.reply(409,{'error':'PINNED_DELETE_REJECTED_BEFORE_MOVE'})
                    if background and type(target) is dict and target.get('root')=='canonical-files/binari/cinematics/backgrounds':
                        try:return self.reply(200,store.remove_background_file(command))
                        except PinnedDeleteRejected:return self.reply(409,{'error':'PINNED_DELETE_REJECTED_BEFORE_MOVE'})
                    if not ordinary:return self.reply(404,{'error':'NOT_FOUND'})
                    return self.reply(200,single_target_delete.remove(command))
                if url.path in ('/v1/workflow/animation','/v1/workflow/animation/read',
                                '/v1/workflow/frames','/v1/workflow/frames/read','/v1/workflow/frames/asset',
                                '/v1/workflow/portrait-pixel','/v1/workflow/portrait-pixel/read'):
                    if workflow_native_media is not True or not hasattr(store,'workflow_media_publish'):
                        return self.reply(404,{'error':'NOT_FOUND'})
                    from workflow_native_media import MAX_BODY
                    kind=url.path.split('/')[3];reading=url.path.endswith('/read');asset=url.path.endswith('/asset')
                    if (self.command!='POST' or url.query or self.headers.get('Origin') is not None
                        or self.headers.get('Transfer-Encoding') is not None
                        or self.headers.get('Content-Type')!='application/json'):
                        raise ValueError('workflow media transport')
                    n=self.headers.get('Content-Length','')
                    if not n.isdecimal() or not 0<int(n)<=(32768 if (reading or asset) else MAX_BODY):
                        raise ValueError('workflow media body size')
                    self.connection.settimeout(30)
                    raw=self.rfile.read(int(n))
                    if len(raw)!=int(n):raise ValueError('workflow media incomplete')
                    data=json.loads(raw)
                    if type(data) is not dict or set(data)!=({'binding'} if reading else {'binding','artifact'}):
                        raise ValueError('workflow media envelope')
                    binding=data['binding']
                    if type(binding) is dict and ('portraitPose' in binding or
                            kind=='portrait-pixel' and binding.get('posePreset')=='combat') and not workflow_combat_pose:
                        return self.reply(404,{'error':'NOT_FOUND'})
                    if asset:
                        if not hasattr(store,'workflow_media_asset'):return self.reply(404,{'error':'NOT_FOUND'})
                        return self.reply(200,store.workflow_media_asset(kind,data['binding'],data['artifact']))
                    return self.reply(200,store.workflow_media_read(kind,data['binding']) if reading else
                                      store.workflow_media_publish(kind,data['binding'],data['artifact']))
                portrait_routes={
                    '/v1/workflow/portrait-representative':('representative',workflow_portrait),
                    '/v1/workflow/portrait-idle-reference':('idle',workflow_idle_portrait),
                    '/v1/workflow/portrait-direction':('direction',workflow_direction_portrait),
                    '/v1/workflow/portrait-combat-reference':('combat',workflow_combat_pose),
                    '/v1/workflow/portrait-combat-direction':('combat_direction',workflow_combat_pose),
                }
                if url.path in portrait_routes:
                    mode,enabled_portrait=portrait_routes[url.path]
                    if enabled_portrait is not True or not hasattr(store,'workflow_portrait_representative'):
                        return self.reply(404,{'error':'NOT_FOUND'})
                    if (self.command!='POST' or url.query or self.headers.get('Origin') is not None
                        or self.headers.get('Transfer-Encoding') is not None
                        or self.headers.get('Content-Type')!='application/json'):
                        raise ValueError('workflow portrait transport')
                    from base64 import b64decode
                    max_b64=4*((8*1024*1024+2)//3)
                    n=self.headers.get('Content-Length','')
                    if not n.isdecimal() or not 0<int(n)<=max_b64+4096:raise ValueError('workflow portrait size')
                    self.connection.settimeout(10)
                    raw=self.rfile.read(int(n))
                    if len(raw)!=int(n):raise ValueError('workflow portrait incomplete')
                    data=json.loads(raw)
                    if (type(data) is not dict or set(data)!={'binding','base64'}
                        or type(data['base64']) is not str or not 0<len(data['base64'])<=max_b64):
                        raise ValueError('workflow portrait envelope')
                    return self.reply(200,store.workflow_portrait_representative(data['binding'],
                                                                              b64decode(data['base64'],validate=True),mode=mode))

                if url.path in ('/v1/workflow/npc-planning-update','/v1/workflow/npc-planning-update/read',
                                 '/v1/workflow/npc-planning-add','/v1/workflow/npc-planning-add/read'):
                    is_add=url.path in ('/v1/workflow/npc-planning-add','/v1/workflow/npc-planning-add/read')
                    opt_in=workflow_npc_planning_add if is_add else workflow_npc_planning_update
                    method='workflow_npc_planning_add' if is_add else 'workflow_npc_planning_update'
                    if opt_in is not True or not hasattr(store,method):
                        return self.reply(404,{'error':'NOT_FOUND'})
                    if (self.command!='POST' or url.query or self.headers.get('Origin') is not None or
                        self.headers.get('Transfer-Encoding') is not None or self.headers.get('Content-Type')!='application/json'):
                        raise ValueError('npc planning update transport')
                    n=self.headers.get('Content-Length','')
                    if not n.isdecimal() or not 0<int(n)<=512*1024:raise ValueError('npc planning update body size')
                    self.connection.settimeout(10)
                    raw=self.rfile.read(int(n))
                    if len(raw)!=int(n):raise ValueError('npc planning update incomplete')
                    data=json.loads(raw);reading=url.path.endswith('/read')
                    if type(data)is not dict or set(data)!=({'binding'} if reading else {'binding','artifact'}):
                        raise ValueError('npc planning update envelope')
                    return self.reply(200,getattr(store,method+'_read')(data['binding']) if reading else
                                      getattr(store,method)(data['binding'],data['artifact']))

                if url.path in ('/v1/workflow/plan-representative','/v1/workflow/plan-idle','/v1/workflow/plan-current'):
                    shared=url.path=='/v1/workflow/plan-current'
                    is_idle=url.path=='/v1/workflow/plan-idle'
                    plan_enabled=workflow_shared_plan if shared else workflow_idle_plan if is_idle else workflow_representative
                    plan_write=getattr(store,'workflow_plan_current' if shared else 'workflow_plan_idle' if is_idle else 'workflow_plan_representative',None)
                    if plan_enabled is not True or not callable(plan_write):
                        return self.reply(404,{'error':'NOT_FOUND'})
                    if (self.command!='POST' or url.query or self.headers.get('Origin') is not None
                        or self.headers.get('Transfer-Encoding') is not None
                        or self.headers.get('Content-Type')!='application/json'):
                        raise ValueError('workflow representative transport')
                    n=self.headers.get('Content-Length','')
                    if not n.isdecimal() or not 0<int(n)<=131072:raise ValueError('workflow representative size')
                    self.connection.settimeout(10)
                    raw=self.rfile.read(int(n))
                    if len(raw)!=int(n):raise ValueError('workflow representative incomplete')
                    data=json.loads(raw)
                    fields={'binding','content'} if is_idle else {'binding','content','metadataAfter'}
                    if shared:
                        if (type(data) is not dict or set(data)!={'binding','content','stage'}
                            or data['stage'] not in ('overview','portrait-art','representative','idle','combat')):
                            raise ValueError('workflow shared planning envelope')
                        if data['stage']=='combat' and workflow_combat_pose is not True:
                            return self.reply(404,{'error':'NOT_FOUND'})
                        return self.reply(200,plan_write(data['stage'],data['binding'],data['content'],markdown=workflow_npc_planning_markdown))
                    if type(data) is not dict or set(data)!=fields:
                        raise ValueError('workflow plan envelope')
                    return self.reply(200,plan_write(data['binding'],data['content']) if is_idle else
                                      plan_write(data['binding'],data['content'],data['metadataAfter']))

                if url.path == '/v1/workflow/preset':
                    if workflow_preset is not True or not hasattr(store,'workflow_preset'):
                        return self.reply(404,{'error':'NOT_FOUND'})
                    if (self.command!='POST' or url.query or self.headers.get('Origin') is not None
                        or self.headers.get('Transfer-Encoding') is not None
                        or self.headers.get('Content-Type')!='application/json'):
                        raise ValueError('workflow preset transport')
                    n=self.headers.get('Content-Length','')
                    if not n.isdecimal() or not 0<int(n)<=65536:raise ValueError('workflow preset size')
                    self.connection.settimeout(10)
                    raw=self.rfile.read(int(n))
                    if len(raw)!=int(n):raise ValueError('workflow preset incomplete')
                    data=json.loads(raw)
                    if type(data) is not dict or set(data)!={'binding','files'}:
                        raise ValueError('workflow preset envelope')
                    try:
                        return self.reply(200,store.workflow_preset(data['binding'],data['files']))
                    except Conflict as error:
                        binding = data['binding']
                        task = binding.get('taskId') if isinstance(binding, dict) else None
                        if not isinstance(task, str) or not re.fullmatch(r'workflow\.task\.[a-f0-9]{64}', task):
                            task = '-'
                        reason = {
                            'workflow preset stale snapshot': 'PRESET_SNAPSHOT_CHANGED',
                            'workflow preset target exists': 'PRESET_TARGET_EXISTS',
                            'workflow preset replay binding': 'PRESET_REPLAY_CONFLICT',
                            'workflow preset previous generation not retired': 'PRESET_GENERATION_ACTIVE',
                        }.get(str(error), 'PRESET_CONFLICT_UNCLASSIFIED')
                        print(f'filex_workflow_preset task={task} status=409 reason={reason}', flush=True)
                        if isinstance(error, PresetPrewriteConflict):
                            return self.reply(409, {'error':'WORKFLOW_PRESET_REJECTED_BEFORE_WRITE'})
                        raise

                if url.path == '/v1/workflow/delete-character':
                    if workflow_delete is not True or not hasattr(store,'workflow_delete_character'):
                        return self.reply(404,{'error':'NOT_FOUND'})
                    if (self.command!='POST' or url.query or self.headers.get('Origin') is not None
                         or self.headers.get('Transfer-Encoding') is not None
                         or self.headers.get('Content-Type')!='application/json'):
                        raise ValueError('workflow delete transport')
                    n=self.headers.get('Content-Length','')
                    if not n.isdecimal() or not 0<int(n)<=65536:raise ValueError('workflow delete size')
                    self.connection.settimeout(10)
                    raw=self.rfile.read(int(n))
                    if len(raw)!=int(n):raise ValueError('workflow delete incomplete')
                    data=json.loads(raw)
                    if type(data) is not dict or set(data) not in ({'binding','metadataAfter'},{'binding','metadataAfter','target'}):
                        raise ValueError('workflow delete envelope')
                    if 'target' in data:
                        if not workflow_npc_folder_delete or type(data['target']) is not dict:
                            return self.reply(404,{'error':'NOT_FOUND'})
                        return self.reply(200,store.workflow_delete_character(data['binding'],data['metadataAfter'],target=data['target']))
                    return self.reply(200,store.workflow_delete_character(data['binding'],data['metadataAfter']))

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
                if hasattr(store,'cinematic_publish'):
                    from media_store import CINEMATIC_ROUTE,CINEMATIC_BODY,SEQUENCE_ROUTE,PLAYBACK_ROUTE,SOURCE_ROUTE,BACKGROUND_ROUTE,ARCHIVE_ROUTE
                    routes={CINEMATIC_ROUTE:store.cinematic_publish,
                            CINEMATIC_ROUTE+'/asset':store.cinematic_source,
                            CINEMATIC_ROUTE+'/source-status':lambda v:store.cinematic_source(v,status=True),
                            CINEMATIC_ROUTE+'/result':store.cinematic_result}
                    if hasattr(store,'cinematic_sequence_publish'):
                        routes.update({SEQUENCE_ROUTE:store.cinematic_sequence_publish,
                                       SEQUENCE_ROUTE+'/frame':store.cinematic_sequence_frame,
                                       SEQUENCE_ROUTE+'/result':store.cinematic_sequence_result})
                    if hasattr(store,'cinematic_playback_update'):
                        routes.update({PLAYBACK_ROUTE:store.cinematic_playback_update,
                                       PLAYBACK_ROUTE+'/target-status':store.cinematic_playback_target,
                                       PLAYBACK_ROUTE+'/result':store.cinematic_playback_result})
                    if hasattr(store,'cinematic_source_generate'):
                        routes.update({SOURCE_ROUTE:store.cinematic_source_generate,SOURCE_ROUTE+'/result':store.cinematic_source_result,
                                       SOURCE_ROUTE+'/reconcile':store.cinematic_source_reconcile,
                                       BACKGROUND_ROUTE:lambda p:store.cinematic_source_generate(p,background=True),
                                       BACKGROUND_ROUTE+'/result':lambda p:store.cinematic_source_result(p,background=True),
                                       BACKGROUND_ROUTE+'/identity':lambda p:store.background_pixel_source(p,status=True,identity=True),
                                       BACKGROUND_ROUTE+'/source-status':lambda p:store.background_pixel_source(p,status=True),
                                       BACKGROUND_ROUTE+'/asset':store.background_pixel_source,
                                       BACKGROUND_ROUTE+'/reconcile':lambda p:store.cinematic_source_reconcile(p,background=True),
                                       ARCHIVE_ROUTE:store.cinematic_sequences_archive,ARCHIVE_ROUTE+'/result':store.cinematic_sequences_archive_result})
                    from map_store import MAP_ROUTE,map_context,map_revision
                    if hasattr(store,'cinematic_source_generate'):
                        routes.update({MAP_ROUTE:lambda p:map_revision(store,p,True),
                          MAP_ROUTE+'/context':lambda p:map_context(store,p),
                          MAP_ROUTE+'/revision':lambda p:map_revision(store,p)})
                    action=routes.get(url.path)
                    if self.command!='POST' or action is None:return self.reply(404,{'error':'NOT_FOUND'})
                    n=self.headers.get('Content-Length','')
                    if (url.query or self.headers.get('Origin') is not None or self.headers.get('Transfer-Encoding') is not None or
                        self.headers.get('Content-Type')!='application/json' or not n.isascii() or not n.isdecimal() or
                        len(n)>9 or not 0<int(n)<=(CINEMATIC_BODY if url.path in (CINEMATIC_ROUTE,SOURCE_ROUTE,BACKGROUND_ROUTE) else 6*1024*1024 if url.path==SEQUENCE_ROUTE+'/frame' else 64*1024 if url.path in (SEQUENCE_ROUTE,PLAYBACK_ROUTE,PLAYBACK_ROUTE+'/result',MAP_ROUTE) else 4096)):
                        raise ValueError('cinematic transport')
                    def distinct(values):
                        out=dict(values)
                        if len(out)!=len(values):raise ValueError('duplicate cinematic key')
                        return out
                    self.connection.settimeout(20)
                    body=self.rfile.read(int(n))
                    if len(body)!=int(n):raise ValueError('cinematic body incomplete')
                    return self.reply(200,action(json.loads(body.decode('utf-8'),object_pairs_hook=distinct)))
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
                    if url.path == '/v1/capabilities': return self.reply(200, {**({'workflowPropPackageV1':True} if workflow_props is not None else {}),**({'workflowAudioDeleteV1':True} if workflow_audio_delete is True and workflow_audio is not None and workflow_audio.store is store else {}),**({'workflowAudioResultV1':True,'workflowAudioSeparateV1':True} if workflow_audio is not None and workflow_audio.store is store else {}),**({'singleTargetRecoverableDeleteV1':True} if single_target_delete is not None else {}),**({'workflowExactNpcFolderDeleteV1':True} if workflow_delete is True and workflow_npc_folder_delete is True and hasattr(store,'verify_workflow_folder_recovery') else {}),'conceptRecoverableLifecycleV1':hasattr(store,'lifecycle_commit'),'conceptCategoryMarkdownV1':hasattr(store,'markdown_commit'),'conceptCategoryOverwriteV1':hasattr(store,'markdown_current'),'conceptSingleTargetDeleteV1':hasattr(store,'scope_delete_command'),'releasedFrameCleanupLeaseV1':hasattr(store,'begin_frame_maintenance'),'npcOwnedSlots':hasattr(store,'generation'),'generationFence':hasattr(store,'generation'), **({'workflowExactNpcDeleteV1':True} if workflow_delete is True and hasattr(store,'workflow_delete_character') else {}),**({'workflowAtomicNpcPresetV1':True} if workflow_preset is True and hasattr(store,'workflow_preset') else {}),**({'workflowRepresentativePlanWriteV1':True} if workflow_representative is True and hasattr(store,'workflow_plan_representative') else {}),**({'workflowStaticPortraitPixelV1':True} if workflow_native_media is True and hasattr(store,'workflow_static_pixel_publish') else {}),**({'workflowRegenerateMotionV1':True} if workflow_native_media is True and hasattr(store,'workflow_media_asset') else {}),**({'workflowNativePNGMediaV1':True,'workflowSelectedMotionV1':True} if workflow_native_media is True and hasattr(store,'workflow_media_publish') else {}),**({'workflowIdlePlanWriteV1':True} if workflow_idle_plan is True and hasattr(store,'workflow_plan_idle') else {}),**({'workflowRepresentativePortraitCurrentFilesV1':True} if workflow_portrait is True and hasattr(store,'workflow_portrait_representative') else {}),**({'workflowIdleReferencePortraitWriteV1':True} if workflow_idle_portrait is True and hasattr(store,'workflow_portrait_representative') else {}),**({'workflowCombatPortraitWriteV1':True} if workflow_combat_pose is True and hasattr(store,'workflow_portrait_representative') else {}),**({'workflowDirectionPortraitWriteV1':True} if workflow_direction_portrait is True and hasattr(store,'workflow_portrait_representative') else {}),**({'workflowNpcPlanningAddMarkdownV1':True} if workflow_npc_planning_add is True and hasattr(store,'workflow_npc_planning_add') else {}),**({'workflowNpcPlanningUpdateMarkdownV1':True} if workflow_npc_planning_update is True and hasattr(store,'workflow_npc_planning_update') else {}),**({'workflowCurrentDocumentChainV1':True} if workflow_shared_plan is True and hasattr(store,'workflow_plan_current') else {}),'atomicCAS':not hasattr(store,'batch'), 'lockedBatch':hasattr(store,'batch'), 'namespace':getattr(store,'namespace','canonical-files/binari/npcs/' if hasattr(store,'batch') else PREFIX)})
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
                        if url.path == '/v1/generation/abandon-creation':
                            if set(data)!={'guard'}:raise ValueError('creation abandonment schema')
                            return self.reply(200,store.abandon_creation(data['guard']))
                        if url.path == '/v1/generation/prepare-create': return self.reply(200,store.prepare_create(data['npcId'],data['requestId'],data['expectedSourceVersion']))
                        if url.path == '/v1/generation/prepare-restore': return self.reply(200,store.prepare_restore(data['npcId'],data['requestId'],data['backupId'],data['expectedSourceVersion']))
                        if url.path == '/v1/create-character': return self.reply(200,store.create_character(data['requestId'],data['inputHash'],data['files'],data['readSet'],data['expectedSourceVersion'],data['guard']))
                        if url.path == '/v1/restore-character': return self.reply(200,store.restore_character(data['requestId'],data['inputHash'],data['backupId'],data['expectedSourceVersion'],data['metadataAfter'],data['guard']))
                        if url.path == '/v1/generation/bootstrap': return self.reply(200,store.bootstrap(data['npcId'],data['expectedSourceVersion']))
                        if url.path == '/v1/generation/admit': return self.reply(200,store.admit(data['npcId'],data['generationId'],data['requestId']))
                        if url.path == '/v1/generation/finish': return self.reply(200,store.finish(data['guard']))
                        if url.path == '/v1/generation/stage-delete':
                            base={'npcId','generationId','requestId','expectedSourceVersion'}
                            if set(data) not in (base,base|{'replacementEvidence'}) or ('replacementEvidence' in data and not isinstance(data['replacementEvidence'],dict)):raise ValueError('staged deletion binding shape')
                            return self.reply(200,store.stage_delete(data['npcId'],data['generationId'],data['requestId'],data['expectedSourceVersion'],**({'replacement_evidence':data['replacementEvidence']} if 'replacementEvidence' in data else {})))
                        if url.path == '/v1/generation/prepare-delete':
                            base={'npcId','generationId','requestId'}
                            if set(data)==base|{'deletionScope'}:
                                return self.reply(200,store.prepare_delete(data['npcId'],data['generationId'],data['requestId'],deletion_scope=data['deletionScope']))
                            if set(data)!=base|{'externalOwnerManifestHash'}:raise ValueError('deletion binding shape')
                            return self.reply(200,store.prepare_delete(data['npcId'],data['generationId'],data['requestId'],data['externalOwnerManifestHash']))
                        if url.path == '/v1/asset': return self.reply(200,store.put_asset(data['assetId'],decode(data['base64']),data['guard']))
                        if url.path == '/v1/batch': return self.reply(200,store.batch(data['requestId'],data['inputHash'],data['files'],data['readSet'],data['guard']))
                        if url.path == '/v1/delete-character': return self.reply(200,store.delete_character(data['requestId'],data['inputHash'],data['characterId'],data['expectedSourceVersion'],data['metadataAfter'],data['guard']))
                    if url.path == '/v1/asset' and hasattr(store,'put_asset'):
                        return self.reply(200,store.put_asset(data['assetId'],decode(data['base64'])))
                    if url.path == '/v1/delete-character' and hasattr(store,'delete_character'):
                        return self.reply(200,store.delete_character(data['requestId'],data['inputHash'],data['characterId'],data['expectedSourceVersion'],data['metadataAfter']))
                    if url.path == '/v1/concept/category-markdown' and hasattr(store,'markdown_commit'):
                        if self.command!='POST' or url.query or self.headers.get('Origin') is not None:
                            raise ValueError('concept markdown transport')
                        if set(data)=={'previousRequestId'}:return self.reply(200,store.markdown_previous(data['previousRequestId']))
                        if set(data)=={'readCategoryId'}:return self.reply(200,store.markdown_current(data['readCategoryId']))
                        return self.reply(200,store.markdown_commit(data))
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
            except OwnershipBusy: return self.reply(503, {'error':'FILEX_WRITE_BUSY'})
            except Conflict: return self.reply(409, {'error':'CONFLICT'})
            except FileNotFoundError: return self.reply(404, {'error':'NOT_FOUND'})
            except (ValueError, KeyError, TypeError): return self.reply(400, {'error':'INVALID_REQUEST'})
            except OSError: return self.reply(503, {'error':'STORAGE_FAILURE'})
        do_GET = dispatch
        do_POST = dispatch
    return Handler

if __name__ == '__main__':
    # Domain modules import server; preserve one Store / Conflict type.
    import sys
    sys.modules['server']=sys.modules[__name__]
    from scope_registry import bind_scopes,load_registry,unified_handler,writable_mounts
    # No former one-role entry point or arbitrary root/token selector exists.
    scopes=bind_scopes(load_registry(),Path('/canonical'),Path('/run/secrets'),verify_mounts=writable_mounts())
    ThreadingHTTPServer(('0.0.0.0',8093),unified_handler(scopes)).serve_forever()
