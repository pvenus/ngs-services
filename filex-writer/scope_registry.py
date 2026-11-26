"""One Filex HTTP listener; explicit scope, existing role authority, exact mounts."""
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlsplit
import hmac
import json
import os
import re
import stat

SCOPE_PREFIX='/v1/scopes/'
SCHEMA='ngs.filex-writer-scopes.v1'
REGISTRY=Path(__file__).with_name('scopes.json')
IDENT=r'[a-z][a-z0-9-]{0,39}'
CREDENTIALS=frozenset(('npc','concept','motion','media'))
PROFILES=frozenset(('npc','concept','motion','media','audio','props','cinematics'))
PROFILE_CREDENTIAL=MappingProxyType({'npc':'npc','concept':'concept','motion':'motion','media':'media','audio':'concept','props':'concept','cinematics':'media'})
RECEIPT_TOKEN=r'(?:[A-Za-z0-9_.:-]|%[a-fA-F0-9]{2}){1,384}'
HEX=r'[a-f0-9]{64}'
CAPS='/v1/capabilities'
CANONICAL_NAMESPACE='canonical-files/binari/'
# Fixed operation profiles. Domain handlers still verify exact payload/query,
# store namespace, revision/receipt, claims, and lifecycle fences.
_CONCEPT_GET={'/v1/snapshot','/v1/object','/v1/concept/removed-scope'}
_CONCEPT_POST={'/v1/batch','/v1/concept/category-markdown','/v1/concept/remove-scope','/v1/cas'}
_NPC_GET={'/v1/snapshot','/v1/object','/v1/asset','/v1/backup-preview','/v1/generation',
 '/v1/generation/staged-delete-fence','/v1/generation/equivalent','/v1/generation/publications','/v1/migration/status'}
_NPC_POST={'/v1/asset','/v1/batch','/v1/cas','/v1/create-character','/v1/restore-character','/v1/delete-character',
 '/v1/generation/prepare-create','/v1/generation/prepare-restore','/v1/generation/bootstrap',
 '/v1/generation/admit','/v1/generation/finish','/v1/generation/stage-delete','/v1/generation/prepare-delete',
 '/v1/generation/reserve-equivalent','/v1/generation/release-equivalent','/v1/generation/abandon-creation',
 '/v1/migration/prepare','/v1/migration/asset','/v1/migration/activate','/v1/migration/commit',
 '/v1/portrait-assets/register','/v1/workflow/delete-character','/v1/workflow/preset',
 '/v1/workflow/animation','/v1/workflow/animation/read','/v1/workflow/frames','/v1/workflow/frames/read','/v1/workflow/frames/asset',
 '/v1/workflow/portrait-pixel','/v1/workflow/portrait-pixel/read',
 '/v1/workflow/npc-planning-add','/v1/workflow/npc-planning-add/read',
 '/v1/workflow/npc-planning-update','/v1/workflow/npc-planning-update/read',
 '/v1/workflow/plan-current','/v1/workflow/plan-idle','/v1/workflow/plan-representative',
 '/v1/workflow/portrait-representative','/v1/workflow/portrait-idle-reference','/v1/workflow/portrait-direction',
 '/v1/workflow/portrait-combat-direction','/v1/workflow/portrait-combat-reference'}
_AUDIO_POST={'/v1/workflow/audio','/v1/workflow/audio/read','/v1/workflow/audio-separate',
 '/v1/workflow/audio-separate/read','/v1/workflow/audio-separate/asset','/v1/storage/delete-target'}
_PROPS_POST={'/v1/workflow/props','/v1/workflow/props/read','/v1/workflow/props/head','/v1/workflow/props/asset','/v1/storage/delete-target'}
_EXACT=frozenset()
_ALLOW=MappingProxyType({
 'concept':(frozenset(_CONCEPT_GET),frozenset(_CONCEPT_POST)),
 'npc':(frozenset(_NPC_GET),frozenset(_NPC_POST)),
 'motion':(frozenset({'/v1/snapshot','/v1/object','/v1/asset'}),frozenset({'/v1/asset','/v1/batch','/v1/cas'})),
 'media':(_EXACT,frozenset({'/v1/media-artifacts/publish'})),
 'audio':(_EXACT,frozenset(_AUDIO_POST)),
 'props':(_EXACT,frozenset(_PROPS_POST)),
 'cinematics':(_EXACT,frozenset('/v1/workflow/cinematics'+p for p in
  ('','/asset','/source-status','/result','-sequence','-sequence/frame','-sequence/result','-playback','-playback/target-status','-playback/result','-source','-source/result','-source/reconcile',
   '-sequence-remove','-sequence-remove/result','-background','-background/result','-background/reconcile','-background/asset','-background/source-status','-background/identity'))|{'/v1/storage/delete-target','/v1/storage/delete-target/cinematic-inventory'}),
})
_DYNAMIC=MappingProxyType({
 'concept':(('GET',r'/v1/requests/'+RECEIPT_TOKEN),),
 'npc':(('GET',r'/v1/requests/'+RECEIPT_TOKEN),('GET',r'/v1/portrait-assets/[a-zA-Z0-9_.-]{1,170}'),
        ('POST',r'/v1/generation/frame-maintenance/(?:begin|reserve|release|finish)')),
 'motion':(('GET',r'/v1/requests/'+RECEIPT_TOKEN),),
 'media':(('GET',r'/v1/media-artifacts/receipts/'+RECEIPT_TOKEN+'/'+RECEIPT_TOKEN),
          ('GET',r'/v1/media-artifacts/assets/'+HEX+r'\.(?:png|wav)')),
})

@dataclass(frozen=True)
class Scope:
    id:str
    folder:str
    credential:str
    profile:str

def _require(v,message='FILEX_SCOPE_REGISTRY_INVALID'):
    if not v:raise ValueError(message)

def _distinct(pairs):
    v=dict(pairs);_require(len(v)==len(pairs));return v

def load_registry(path=REGISTRY):
    raw=json.loads(Path(path).read_text(encoding='utf-8'),object_pairs_hook=_distinct)
    _require(type(raw)is dict and set(raw)=={'schemaVersion','scopes'} and raw['schemaVersion']==SCHEMA and
             type(raw['scopes'])is list and 0<len(raw['scopes'])<=64)
    out=[]
    for r in raw['scopes']:
        _require(type(r)is dict and set(r)=={'id','folder','credential','profile'} and
                 all(type(r[k])is str and re.fullmatch(IDENT,r[k]) for k in ('id','folder')) and
                 r['credential'] in CREDENTIALS and r['profile'] in PROFILES and PROFILE_CREDENTIAL[r['profile']]==r['credential'])
        out.append(Scope(**r))
    _require(len({r.id for r in out})==len(out)==len({r.folder for r in out}))
    return tuple(out)

def allowed(profile,method,path):
    if method not in ('GET','POST'):return False
    if method=='GET' and path==CAPS:return True
    return path in _ALLOW[profile][method=='POST'] or any(
        method==m and re.fullmatch(pattern,path) for m,pattern in _DYNAMIC.get(profile,()))

def _special_caps(profile,folder):
    entries={
       'media':{'mediaArtifactsV1':True},
       'audio':{'workflowAudioResultV1':True,'workflowAudioSeparateV1':True,'workflowAudioDeleteV1':True},
       'props':{'workflowPropPackageV1':True,'workflowPinnedPropFolderDeleteV1':True,'workflowPropAnimationRemoveV1':True},
       'cinematics':{'workflowCinematicV1':True,'workflowCinematicSequenceV1':True,'workflowCinematicPlaybackV1':True,'workflowCinematicSourceV1':True,'workflowBackgroundSourceV1':True,'workflowBackgroundPixelV1':True,'workflowBackgroundAnimationV1':True,'workflowCinematicSequenceRemoveV1':True,'workflowPinnedCinematicTargetDeleteV1':True,'workflowPinnedBackgroundFileDeleteV1':True},
    }
    return {'namespace':CANONICAL_NAMESPACE+folder+'/',**entries[profile]} if profile in entries else None

def _read_bearer(token_root,credential):
    root=token_root/credential;p=root/'writer_token'
    _require(root.resolve(strict=True)==root and p.resolve(strict=True)==p,'FILEX_SCOPE_TOKEN_INVALID')
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        st=os.fstat(stream.fileno())
        _require(stat.S_ISREG(st.st_mode) and st.st_nlink==1 and 32<=st.st_size<=512 and
             st.st_uid==os.getuid() and not (stat.S_IMODE(st.st_mode)&0o077),'FILEX_SCOPE_TOKEN_INVALID')
        token=stream.read(513).decode('ascii').strip()
    _require(len(token)>=32 and re.fullmatch(r'[\x21-\x7e]{32,511}',token),'FILEX_SCOPE_TOKEN_INVALID')
    return token

def writable_mounts():
    table=Path('/proc/self/mountinfo').read_text(encoding='utf-8')
    by_mount={}
    for line in table.splitlines():
        r=line.split(' - ',1)[0].split()
        if len(r)>5:
            _require(r[4] not in by_mount or not r[4].startswith('/canonical/'),'FILEX_SCOPE_MOUNT_DUPLICATE')
            by_mount[r[4]]='rw' in r[5].split(',')
    return frozenset(p for p,w in by_mount.items() if w)

def bind_scopes(registry, root, credentials, *, verify_mounts=None):
    """Explicit private fixture override only. Production roots/credentials are fixed in main."""
    root=Path(root);credentials=Path(credentials)
    _require(root.is_absolute() and root.resolve(strict=True)==root and root.is_dir())
    roots={}
    for spec in registry:
        p=root/spec.folder
        _require(p.resolve(strict=True)==p and p.is_dir(),'FILEX_SCOPE_ROOT_INVALID')
        if verify_mounts is not None:
            _require(str(root) not in verify_mounts and str(p) in verify_mounts,'FILEX_SCOPE_MOUNT_REQUIRED')
        roots[spec.id]=p
    if verify_mounts is not None:
        scoped={x for x in verify_mounts if x.startswith(str(root)+'/')}
        _require(scoped=={str(p) for p in roots.values()},'FILEX_SCOPE_MOUNT_UNKNOWN')
    _require(len({(p.stat().st_dev,p.stat().st_ino) for p in roots.values()})==len(roots),
             'FILEX_SCOPE_ROOT_ALIAS')
    real_tokens={k:_read_bearer(credentials,k)
                 for k in sorted({r.credential for r in registry})}
    _require(len(set(real_tokens.values()))==len(real_tokens),'FILEX_SCOPE_TOKEN_ALIAS')
    from server import handler
    from concept_store import ConceptStore
    from fenced_store import FencedOwnedNPCStore
    from motion_store import MotionStore
    from media_store import MediaStore,CinematicProjectStore
    from workflow_audio_store import AudioResult
    from prop_package_store import PropPackageStore
    constructors={'concept':ConceptStore,'npc':FencedOwnedNPCStore,'motion':MotionStore,
                  'media':MediaStore,'props':PropPackageStore,'cinematics':CinematicProjectStore}
    stores={r.id:constructors[r.profile](roots[r.id]) for r in registry if r.profile!='audio'}
    for r in registry:
        if r.profile in ('concept','motion'):
            stores[r.id].namespace=CANONICAL_NAMESPACE+r.folder+'/'
    out={}
    for r in registry:
        extra={};store=stores.get(r.id)
        if r.profile=='npc':
            extra={arg:True for arg in ('workflow_delete','workflow_preset','workflow_representative',
               'workflow_portrait','workflow_idle_plan','workflow_idle_portrait','workflow_direction_portrait',
               'workflow_native_media','workflow_shared_plan','workflow_npc_folder_delete',
               'workflow_npc_planning_update','workflow_npc_planning_markdown','workflow_npc_planning_add')}
        elif r.profile=='audio':
            # Audio's active sound metadata must still use the exact same
            # concept writer object/lock (not a new process or shadow copy).
            _require('concept' in stores and isinstance(stores['concept'],ConceptStore))
            store=stores['concept'];extra={'workflow_audio':AudioResult(store,roots[r.id]),'workflow_audio_delete':True}
        elif r.profile=='props':extra={'workflow_props':store}
        token=real_tokens[r.credential]
        out[r.id]=(r,token,handler(store,token,**extra))
    return MappingProxyType(out)

def unified_handler(bindings):
    class ScopeHandler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def reply(self,status,value,raw=False):
            body=value if raw else json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/octet-stream' if raw else 'application/json')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.end_headers();self.wfile.write(body)
        def dispatch(self):
            try:q=urlsplit(self.path)
            except ValueError:return self.reply(400,{'error':'INVALID_REQUEST'})
            m=re.fullmatch(r'/v1/scopes/('+IDENT+r')(/[^\s?#]+)',q.path) if not q.scheme and not q.netloc and not q.fragment else None
            if not m or m[1] not in bindings:return self.reply(404,{'error':'NOT_FOUND'})
            spec,token,domain=bindings[m[1]];sub='/v1'+m[2]
            auth=self.headers.get_all('Authorization') or []
            # No cross-scope fallback or browser credentials.
            if len(auth)!=1 or not hmac.compare_digest(auth[0].encode('utf-8'),'Bearer '.encode()+token.encode('ascii')) or self.headers.get('Origin') is not None:
                return self.reply(401,{'error':'UNAUTHORIZED'})
            if not allowed(spec.profile,self.command,sub):return self.reply(404,{'error':'NOT_FOUND'})
            if sub==CAPS and q.query:return self.reply(400,{'error':'INVALID_REQUEST'})
            caps=_special_caps(spec.profile,spec.folder) if sub==CAPS else None
            if caps is not None:return self.reply(200,caps)
            original=self.path
            try:
                self.path=sub+('?' + q.query if q.query else '')
                return domain.dispatch(self)
            finally:self.path=original
        do_GET=dispatch
        do_POST=dispatch
    return ScopeHandler
