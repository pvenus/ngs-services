"""Opt-in local owner import. No generated request, planner row, or model."""
import hashlib
import hmac
import json
import os
import re
import stat
from pathlib import Path

from .filex_cas_transport import AudioPrewriteRejected
from .filex_read import digest
from .workflow_audio import MAX_BODY

PATH='/v1/audio/manual-import'
WRITER='/v1/workflow/audio-import'
SCHEMA='ngs.audio-import.v1'
TOKEN=re.compile(r'[A-Za-z0-9_-]{43,128}')
SHA=re.compile(r'[a-f0-9]{64}')
MARKER='.manual-audio-import-token'
TOKEN_ENV='PDS_MANUAL_AUDIO_IMPORT_TOKEN_FILE'


def resolve_token_file(storage_root,override):
    if override:return Path(override)
    root=Path(storage_root)
    candidate=root/MARKER
    if not os.path.lexists(candidate):return None
    if not root.is_absolute() or root.resolve(strict=True)!=root:
        raise ValueError('MANUAL_AUDIO_IMPORT_STORAGE_PATH')
    st=root.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:
        raise ValueError('MANUAL_AUDIO_IMPORT_STORAGE_PRIVATE')
    return candidate


def owner_token(token_file):
    path=Path(token_file)
    if not path.is_absolute() or path.resolve(strict=True)!=path:
        raise ValueError('MANUAL_AUDIO_IMPORT_TOKEN_PATH')
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
        st=os.fstat(stream.fileno())
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1
            or stat.S_IMODE(st.st_mode)!=0o600 or not 43<=st.st_size<=129):
            raise ValueError('MANUAL_AUDIO_IMPORT_TOKEN_PRIVATE')
        raw=stream.read(130)
    if len(raw)!=st.st_size:raise ValueError('MANUAL_AUDIO_IMPORT_TOKEN_INVALID')
    token=raw.decode('ascii').removesuffix('\n')
    if not TOKEN.fullmatch(token):raise ValueError('MANUAL_AUDIO_IMPORT_OWNER_TOKEN_INVALID')
    return token


def configured(server,host,token_file,writer_factory):
    if not token_file:return
    if host!='127.0.0.1':raise ValueError('MANUAL_AUDIO_IMPORT_LOCAL_ONLY')
    token=owner_token(token_file)
    server.manual_audio_import=(hashlib.sha256(token.encode()).digest(),writer_factory)

def _json(handler):
    lengths=handler.headers.get_all('Content-Length')
    if not lengths or len(lengths)!=1 or not re.fullmatch(r'[0-9]{1,9}',lengths[0]) or not 0<int(lengths[0])<=MAX_BODY:
        raise ValueError('MANUAL_AUDIO_IMPORT_LENGTH')
    handler.connection.settimeout(20)
    raw=handler.rfile.read(int(lengths[0]))
    if len(raw)!=int(lengths[0]):raise ValueError('MANUAL_AUDIO_IMPORT_TRUNCATED')
    def unique(pairs):
        out=dict(pairs)
        if len(out)!=len(pairs):raise ValueError('MANUAL_AUDIO_IMPORT_DUPLICATE_PROPERTY')
        return out
    return json.loads(raw,object_pairs_hook=unique)

def serve(handler):
    service=getattr(handler.server,'manual_audio_import',None)
    if not service:return handler.respond(404,{'error':'MANUAL_AUDIO_IMPORT_DISABLED'})
    if (handler.server.server_address[0]!='127.0.0.1' or handler.client_address[0]!='127.0.0.1'
        or handler.headers.get_all('Host')!=['127.0.0.1:'+str(handler.server.server_port)]
        or handler.path!=PATH or handler.command!='POST'
        or any(handler.headers.get(k) is not None for k in ('Origin','Sec-Fetch-Site','Transfer-Encoding'))
        or handler.headers.get_all('Content-Type')!=['application/json']):
        return handler.respond(403,{'error':'MANUAL_AUDIO_IMPORT_INTERNAL_ONLY'})
    credential=handler.headers.get_all('Authorization')
    if not credential or len(credential)!=1 or not credential[0].startswith('Bearer ') or not TOKEN.fullmatch(credential[0][7:]) or not hmac.compare_digest(
        hashlib.sha256(credential[0][7:].encode()).digest(),service[0]):
        return handler.respond(401,{'error':'MANUAL_AUDIO_IMPORT_OWNER_REQUIRED'})
    try:
        body=_json(handler)
        if type(body) is not dict or set(body)!={'schemaVersion','projectId','title','source','artifact'}:
            raise ValueError('MANUAL_AUDIO_IMPORT_INVALID')
        if body['schemaVersion']!=SCHEMA or body['projectId']!='binari' or type(body['title']) is not str or type(body['artifact']) is not dict or type(body['artifact'].get('sha256')) is not str or not SHA.fullmatch(body['artifact']['sha256']):
            raise ValueError('MANUAL_AUDIO_IMPORT_INVALID')
        rid='audio.import.'+digest([SCHEMA,body['projectId'],body['title'],body['source'],body['artifact']['sha256']])
    except (ValueError,TypeError,UnicodeError,OSError,RecursionError):
        return handler.respond(422,{'error':'MANUAL_AUDIO_IMPORT_INVALID'})
    try:
        with service[1]() as writer:
            result=writer.call('POST',WRITER,body)
        if result!={'ok':True,'resultRef':'canonical-files/binari/audio/'+rid+'/metadata.json'}:
            raise ValueError('MANUAL_AUDIO_IMPORT_RECEIPT')
        return handler.respond(200,{**result,'id':rid,'sha256':body['artifact']['sha256']})
    except AudioPrewriteRejected:
        return handler.respond(422,{'error':'MANUAL_AUDIO_IMPORT_INVALID'})
    except (ValueError,OSError,TypeError):
        # If transport failed after canonical rename, caller retries exact request.
        return handler.respond(503,{'error':'MANUAL_AUDIO_IMPORT_OUTCOME_UNCONFIRMED'})
