"""One operator-owned Filex writer binding; never selected from request data."""
from hashlib import sha256
import json
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit
from .filex_read import private_read, require

CONFIG_ENV = 'PDS_FILEX_WRITER_CONFIG'
CONFIG = Path.home() / 'Library/Application Support/NPCGen/pds-filex-writer.json'
SCOPES = frozenset(('npc', 'concept', 'motion', 'media', 'audio', 'props'))
AUDIO_NAMESPACE = 'canonical-files/binari/audio/'
PROP_NAMESPACE = 'canonical-files/binari/props/'
_SCOPE_ID = re.compile(r'[a-z][a-z0-9-]{0,39}\Z')


def _strict_pairs(values):
    output = dict(values)
    require(len(output) == len(values), 'FILEX_WRITER_CONFIG_DUPLICATE')
    return output


def _binding(source=None):
    path = Path(source if source is not None else os.environ.get(CONFIG_ENV) or CONFIG)
    try:
        content = private_read(path)
        require(len(content) <= 16384, 'FILEX_WRITER_CONFIG_LIMIT')
        value = json.loads(content, object_pairs_hook=_strict_pairs)
    except FileNotFoundError:
        raise ValueError('FILEX_WRITER_CONFIG_REQUIRED') from None
    require(type(value) is dict and set(value) == {'schemaVersion', 'projectId', 'endpoint', 'scopes'}
            and value['schemaVersion'] == 'pds-filex-writer.v1' and value['projectId'] == 'binari'
            and type(value['endpoint']) is str and type(value['scopes']) is list
            and len(SCOPES) <= len(value['scopes']) <= 64, 'FILEX_WRITER_CONFIG_INVALID')
    origin = urlsplit(value['endpoint'])
    require(origin.scheme == 'http' and origin.hostname == '127.0.0.1' and origin.port
            and origin.netloc == f'127.0.0.1:{origin.port}' and not origin.path
            and not origin.query and not origin.fragment and value['endpoint'] == origin.geturl(),
            'FILEX_WRITER_ENDPOINT_INVALID')
    by_scope = {}
    for entry in value['scopes']:
        require(type(entry) is dict and set(entry) == {'id', 'tokenFile'} and type(entry['id']) is str
                and _SCOPE_ID.fullmatch(entry['id']) and entry['id'] not in by_scope and type(entry['tokenFile']) is str
                and Path(entry['tokenFile']).is_absolute(), 'FILEX_WRITER_SCOPES_INVALID')
        by_scope[entry['id']] = entry['tokenFile']
    require(SCOPES <= by_scope.keys(), 'FILEX_WRITER_SCOPES_INVALID')
    return value['endpoint'], by_scope, sha256(content).hexdigest()


def fingerprint():
    return _binding()[2]


def scope_config(scope, *, source=None):
    endpoint, by_scope, _ = _binding(source)
    require(type(scope) is str and scope in by_scope, 'FILEX_WRITER_SCOPE_INVALID')
    return {'schemaVersion': 'pds-filex-cas.v1', 'enabled': True, 'projectId': 'binari',
            'endpoint': endpoint, 'tokenFile': by_scope[scope], 'writerScope': scope}


def scoped_path(scope, path):
    require(type(path) is str and '#' not in path and all(33 <= ord(c) <= 126 for c in path), 'FILEX_WRITER_ROUTE_INVALID')
    u = urlsplit(path)
    parts = u.path.split('/')[2:]
    require(type(scope) is str and _SCOPE_ID.fullmatch(scope) and not u.scheme and not u.netloc and not u.fragment
            and u.path.startswith('/v1/') and parts[0] != 'scopes' and all(
                p and p not in ('.', '..') and not any(c in p for c in '/\\%')
                and all(ord(c) >= 32 and ord(c) != 127 for c in p)
                for p in (unquote(piece, errors='strict') for piece in parts)),
            'FILEX_WRITER_ROUTE_INVALID')
    return f'/v1/scopes/{scope}{path[3:]}'


def writer(scope):
    from .filex_cas_transport import FilexCASTransport
    return FilexCASTransport(scope_config(scope))
