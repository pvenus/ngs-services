"""Read-only active canonical tree comparison. Not a filesystem snapshot."""
import hashlib
import json
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timezone
import os
import re
import stat
from threading import Lock
from time import monotonic_ns
from uuid import uuid4
from urllib.parse import parse_qs

from .project_folders import DIR_FLAGS,FILE_FLAGS,FolderError,SHA

PREFIX='/v1/folders'
MAX_ENTRIES=32768
MAX_DIRS=8192
MAX_BODY=16*1024*1024
# MCP uses interoperable exact JSON integers, not a product-level size cap.
MAX_TRANSFER_INTEGER=2**53-1
# The legacy HTTP whole-file route materializes its response in memory.
MAX_LEGACY_BUFFERED_FILE=64*1024*1024
MAX_PATH_BYTES=4096
CHUNK=1024*1024
_HASH_CACHE=OrderedDict()
_HASH_LOCK=Lock()
_HASH_MAX=256


def _bad(status=409,code='FOLDER_SOURCE_CHANGED'):
    raise FolderError(status,code)


def _parts(path,*,empty=False):
    if path=='' and empty:return ()
    if type(path) is not str:_bad(400,'FOLDER_PATH_INVALID')
    parts=tuple(path.split('/'))
    try:
        good=(len(path.encode('utf-8'))<=MAX_PATH_BYTES and len(parts)<=64
              and all(p and not p.startswith('.') and len(p.encode('utf-8'))<=255
                      and not re.search(r'[\\\x00-\x1F\x7F]',p) for p in parts))
    except UnicodeError:good=False
    if not good:_bad(400,'FOLDER_PATH_INVALID')
    return parts


def _log_capacity(event,**fields):
    # Only contention events are logged. A broken log stream must not change the read result.
    try:print(json.dumps({'event':event,'at':datetime.now(timezone.utc).isoformat(timespec='milliseconds'),**fields},separators=(',',':')),flush=True)
    except (OSError,ValueError):pass


class _FolderCapacity:
    def __init__(self,name,limit):
        self.name=name
        self.limit=limit
        self.lock=Lock()
        self.active={}

    @contextmanager
    def hold(self,operation,scope):
        # Use a server-generated ID: JSON-RPC IDs are client-local and can repeat.
        # Never log file paths, hashes, file contents, or caller-supplied metadata.
        try:
            _parts(scope,empty=True)
        except FolderError:
            scope='<invalid>'
        if len(scope)>256:scope=scope[:253]+'...'
        request_id=uuid4().hex
        request={'requestId':request_id,'operation':operation,'scope':scope}
        with self.lock:
            now=monotonic_ns()
            blocked=len(self.active)>=self.limit
            if blocked:
                holders=[]
                for holder in self.active.values():
                    holder['contended']=True
                    holders.append({**holder['request'],'heldMs':(now-holder['started'])//1_000_000})
            else:
                self.active[request_id]={'request':request,'started':now,'contended':False}
        if blocked:
            _log_capacity('pds_folder_busy',pool=self.name,limit=self.limit,request=request,holders=holders)
            _bad(503,'FOLDER_BUSY')
        try:yield
        finally:
            with self.lock:
                holder=self.active.pop(request_id)
                held_ms=(monotonic_ns()-holder['started'])//1_000_000
            if holder['contended']:
                _log_capacity('pds_folder_busy_holder_released',pool=self.name,request=request,heldMs=held_ms)


_COMPARE=_FolderCapacity('compare',1)
_FETCH=_FolderCapacity('fetch',2)


@contextmanager
def _under(root,parts):
    fd=os.dup(root)
    try:
        device=os.fstat(root).st_dev
        for p in parts:
            child=os.open(p,DIR_FLAGS,dir_fd=fd)
            os.close(fd);fd=child
            if os.fstat(fd).st_dev!=device:_bad(409,'FOLDER_SOURCE_UNSAFE')
        yield fd
    finally:os.close(fd)


def _stat(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_nlink)


def _cached(pin,sha=None):
    with _HASH_LOCK:
        if sha is None:
            found=_HASH_CACHE.get(pin)
            if found is not None:_HASH_CACHE.move_to_end(pin)
            return found
        _HASH_CACHE[pin]=sha
        _HASH_CACHE.move_to_end(pin)
        if len(_HASH_CACHE)>_HASH_MAX:_HASH_CACHE.popitem(last=False)


def _read(fd,name,initial,*,keep=False,window=None):
    with os.fdopen(os.open(name,FILE_FLAGS,dir_fd=fd),'rb')as stream:
        before=os.fstat(stream.fileno())
        if _stat(before)!=_stat(initial):_bad()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_dev!=os.fstat(fd).st_dev:
            _bad(409,'FOLDER_SOURCE_UNSAFE')
        pin=_stat(before)
        if not 0<=before.st_size<=MAX_TRANSFER_INTEGER:_bad(409,'FOLDER_FILE_LIMIT')
        if keep and window is None and before.st_size>MAX_LEGACY_BUFFERED_FILE:
            _bad(409,'FOLDER_FILE_LIMIT')
        # Reuse only a fully read SHA for the exact inode, device, size,
        # timestamps and link count, with the same pre/post FD and path checks.
        # Large-file chunking does not re-read all previous bytes one MiB at a time.
        use_cache=before.st_size>MAX_LEGACY_BUFFERED_FILE and (not keep or window is not None)
        digest=_cached(pin) if use_cache else None
        raw=bytearray() if keep else None
        if digest is None:
            sha=hashlib.sha256();total=0
            while data:=stream.read(CHUNK):
                start=total
                total+=len(data)
                if total>before.st_size:_bad()
                sha.update(data)
                if raw is not None:
                    if window is None:raw.extend(data)
                    else:
                        lo,hi=max(window[0],start),min(window[1],total)
                        if lo<hi:raw.extend(memoryview(data)[lo-start:hi-start])
            if total!=before.st_size:_bad()
            digest=sha.hexdigest()
        elif raw is not None:
            stream.seek(window[0])
            length=min(window[1],before.st_size)-window[0]
            raw.extend(stream.read(length))
            if len(raw)!=length:_bad()
        if (_stat(os.fstat(stream.fileno()))!=pin
                or _stat(os.stat(name,dir_fd=fd,follow_symlinks=False))!=pin):_bad()
        if use_cache:_cached(pin,digest)
    return digest,raw


def _inventory(fd,*,hash_files,scope_depth):
    meta={};hashes={};path_bytes=files=dirs=0
    def walk(folder,base=()):
        nonlocal path_bytes,files,dirs
        dirs+=1
        if dirs>MAX_DIRS:_bad(409,'FOLDER_TREE_LIMIT')
        parent=os.fstat(folder);meta[base]=_stat(parent)
        for name in sorted(os.listdir(folder)):
            if name.startswith('.'):continue
            path=base+(name,);key='/'.join(path)
            try:_parts(key)
            except FolderError:_bad(409,'FOLDER_SOURCE_UNSAFE')
            path_bytes+=len(key.encode('utf-8'))
            if path_bytes>8*1024*1024 or len(path)+scope_depth>64:
                _bad(409,'FOLDER_TREE_LIMIT')
            item=os.stat(name,dir_fd=folder,follow_symlinks=False)
            if item.st_dev!=parent.st_dev:_bad(409,'FOLDER_SOURCE_UNSAFE')
            if stat.S_ISDIR(item.st_mode):
                with _under(folder,(name,))as child:
                    if _stat(os.fstat(child))!=_stat(item):_bad()
                    walk(child,path)
                if _stat(os.stat(name,dir_fd=folder,follow_symlinks=False))!=_stat(item):_bad()
            elif stat.S_ISREG(item.st_mode) and item.st_nlink==1 and 0<=item.st_size<=MAX_TRANSFER_INTEGER:
                files+=1
                if files>MAX_ENTRIES:_bad(409,'FOLDER_TREE_LIMIT')
                meta[path]=_stat(item)
                if hash_files:hashes[path]=(_read(folder,name,item)[0],item.st_size)
            else:_bad(409,'FOLDER_SOURCE_UNSAFE')
        if _stat(os.fstat(folder))!=_stat(parent):_bad()
    walk(fd)
    return meta,hashes


def _checkpoint(folders,root,paths):
    # Raw mounted-tree readers do not hold a Filex writer lock. Refuse an
    # in-doubt domain move, and detect a catalog incarnation change between
    # the raw operation's two checkpoints. Unrelated scopes are unaffected.
    out=[]
    for scope,journal in getattr(folders,'read_checkpoints',()):
        common=min(len(scope),len(paths))
        if scope[:common]!=paths[:common]:continue
        try:
            with _under(root,scope) as domain:
                try:os.stat(journal,dir_fd=domain,follow_symlinks=False)
                except FileNotFoundError:pass
                else:_bad(409,'FOLDER_DOMAIN_COMMIT_IN_PROGRESS')
                current=os.stat('metadata.json',dir_fd=domain,follow_symlinks=False)
                if not stat.S_ISREG(current.st_mode) or current.st_nlink!=1:_bad(409,'FOLDER_SOURCE_UNSAFE')
                out.append((_stat(os.fstat(domain)),_stat(current)))
        except FileNotFoundError:_bad(409,'FOLDER_DOMAIN_CATALOG_UNAVAILABLE')
    return tuple(out)


def compare(folders,value,*,operation):
    fields={'scope','files'}
    include_deleted=(type(value) is dict and set(value)==fields|{'includeDeleted'}
                     and value['includeDeleted'] is True)
    if (type(value) is not dict or (set(value)!=fields and not include_deleted)
        or type(value['files']) is not list or len(value['files'])>MAX_ENTRIES):
        _bad(400,'FOLDER_COMPARE_INVALID')
    paths=_parts(value['scope'],empty=True);seen={}
    for item in value['files']:
        if (type(item) is not dict or set(item)!={'path','sha256'} or not isinstance(item['sha256'],str)
                or not SHA.fullmatch(item['sha256'])):_bad(400,'FOLDER_COMPARE_INVALID')
        key=_parts(item['path'])
        if key in seen or include_deleted and len(paths)+len(key)>64:_bad(400,'FOLDER_COMPARE_INVALID')
        seen[key]=item['sha256']
    with _COMPARE.hold(operation,value['scope']),folders._root() as root:
        checkpoint=_checkpoint(folders,root,paths)
        opened=False
        try:
            with _under(root,paths)as directory:
                opened=True
                before,actual=_inventory(directory,hash_files=True,scope_depth=len(paths))
                with _under(root,paths)as check:
                    after,_=_inventory(check,hash_files=False,scope_depth=len(paths))
                if before!=after:_bad()
        except FileNotFoundError:
            # A scope lost after we successfully opened it is a race, not proof of absence.
            if paths and (not include_deleted or not opened):
                try:
                    with _under(root,paths):pass
                except FileNotFoundError:
                    if not include_deleted:_bad(404,'FOLDER_SCOPE_NOT_FOUND')
                    actual={}
                else:_bad()
            else:_bad()
        if include_deleted:
            # Refuse deletion evidence from a detached/replaced canonical root.
            with folders._root() as current:
                a,b=os.fstat(root),os.fstat(current)
                if (a.st_dev,a.st_ino)!=(b.st_dev,b.st_ino):_bad()
        if checkpoint!=_checkpoint(folders,root,paths):_bad()
    groups={}
    for key,(sha,size) in actual.items():
        if seen.get(key)!=sha:
            groups.setdefault(key[0] if len(key)>1 else '',[]).append({'path':'/'.join(key),'sha256':sha,'size':size})
    result={'scope':value['scope'],'groups':[{'folder':folder,'files':groups[folder]} for folder in sorted(groups)]}
    if include_deleted:
        removed=sorted(('/'.join(key),sha) for key,sha in seen.items() if key not in actual)
        result['deleted']=[{'path':path,'sha256':sha} for path,sha in removed]
    return result


def _fetch(folders,scope,path,hash_,offset=None):
    paths=_parts(scope,empty=True);file=_parts(path)
    if not isinstance(hash_,str) or not SHA.fullmatch(hash_):_bad(400,'FOLDER_HASH_REQUIRED')
    if len(paths)+len(file)>64:_bad(400,'FOLDER_PATH_INVALID')
    parents=paths+file[:-1];name=file[-1]
    with folders._root() as root:
        checkpoint=_checkpoint(folders,root,parents+(name,))
        try:
            with _under(root,parents)as directory:
                initial=os.stat(name,dir_fd=directory,follow_symlinks=False)
                if not stat.S_ISREG(initial.st_mode) or initial.st_nlink!=1:
                    _bad(409,'FOLDER_SOURCE_UNSAFE')
                if offset is not None:
                    if offset>initial.st_size:_bad(416,'FOLDER_OFFSET_INVALID')
                    sha,raw=_read(directory,name,initial,keep=True,window=(offset,offset+CHUNK))
                else:sha,raw=_read(directory,name,initial,keep=True)
                with _under(root,parents)as check:
                    if _stat(os.fstat(check))!=_stat(os.fstat(directory)):_bad()
                if sha!=hash_ or checkpoint!=_checkpoint(folders,root,parents+(name,)):_bad()
                return raw,initial.st_size
        except FileNotFoundError:_bad()


def fetch(folders,scope,path,hash_):
    return _fetch(folders,scope,path,hash_)[0]


def fetch_chunk(folders,scope,path,hash_,offset):
    """Read one bounded window against the SHA of the exact checked file identity."""
    if type(offset) is not int or not 0<=offset<=MAX_TRANSFER_INTEGER:_bad(400,'FOLDER_OFFSET_INVALID')
    return _fetch(folders,scope,path,hash_,offset)


def route(handler,parsed,folders):
    if (handler.server.server_address[0]!='127.0.0.1' or handler.client_address[0]!='127.0.0.1'
            or handler.headers.get('Host')!='127.0.0.1:'+str(handler.server.server_port)
            or handler.headers.get('Origin') is not None or handler.headers.get('Transfer-Encoding') is not None):
        return handler.respond(403,{'error':'FOLDER_LOOPBACK_REQUIRED'})
    if folders is None:return handler.respond(404,{'error':'not_found'})
    try:
        if parsed.path==PREFIX+'/compare' and handler.command=='POST' and not parsed.query:
            handler.connection.settimeout(15)
            try:payload=handler.read_json(max_body=MAX_BODY)
            except (ValueError,RecursionError):_bad(400,'FOLDER_COMPARE_INVALID')
            return handler.respond(200,compare(folders,payload,operation='http_compare'))
        if parsed.path==PREFIX+'/file' and handler.command=='GET':
            try:q=parse_qs(parsed.query,keep_blank_values=True,strict_parsing=True,encoding='utf-8',errors='strict',max_num_fields=3)
            except (ValueError,UnicodeError):_bad(400,'FOLDER_QUERY_INVALID')
            if set(q)!={'scope','path','sha256'} or any(len(v)!=1 for v in q.values()):
                _bad(400,'FOLDER_QUERY_INVALID')
            handler.connection.settimeout(15)
            with _FETCH.hold('http_whole_file',q['scope'][0]):
                raw=fetch(folders,*(q[k][0] for k in ('scope','path','sha256')))
                try:
                    handler.send_response(200)
                    handler.send_header('Content-Type','application/octet-stream')
                    handler.send_header('Content-Length',str(len(raw)))
                    handler.send_header('X-Content-Type-Options','nosniff')
                    handler.send_header('Cache-Control','no-store')
                    handler.end_headers()
                    view=memoryview(raw)
                    for start in range(0,len(view),CHUNK):handler.wfile.write(view[start:start+CHUNK])
                except OSError:handler.close_connection=True
            return
        return handler.respond(404,{'error':'not_found'})
    except FolderError as exc:return handler.respond(exc.status,{'error':str(exc)})
    except OSError:return handler.respond(503,{'error':'FOLDER_SOURCE_UNAVAILABLE'})
