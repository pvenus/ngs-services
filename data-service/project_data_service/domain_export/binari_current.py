"""Reviewed current-only capture. Never call LegacySource._capture or source modules.

Candidate inventory is local metadata; publication requires an explicitly enabled,
hash-pinned policy. Source references are retained as data and never followed.
"""
import json,re
from pathlib import Path
from .source import LegacySource
from .core import MirrorError,canonical,digest,sha

MAX_FILE=32*1024*1024
SAFE_ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
PORTRAIT=re.compile(r'([a-f0-9]{64})\.(png|jpg|jpeg|webp)\Z')
RELATION_KEYS=('goodwill','trust','vigilance','fear','hostility','unknown')
SECRET_KEY=re.compile(r'(?:password|passwd|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key|client[_-]?secret)\Z',re.I)
SECRET_VALUE=re.compile(r'(?:sk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY|(?:/|\\)(?:\.aws|\.ssh|\.env)(?:/|\\|\b))')

def inspect_data(value):
    if isinstance(value,dict):
        if any(SECRET_KEY.fullmatch(k) for k in value):raise MirrorError('SENSITIVE_FIELD_DENIED')
        if any(k in value and value[k] not in ('ProjectBS','binari') for k in ('projectId','projectKey')):raise MirrorError('EMBEDDED_OTHER_PROJECT_DENIED')
        for v in value.values():inspect_data(v)
    elif isinstance(value,list):
        for v in value:inspect_data(v)
    elif isinstance(value,str):
        if SECRET_VALUE.search(value):raise MirrorError('SENSITIVE_VALUE_DENIED')
        if re.search(r'(?:/Users/|/Volumes/|/home/|file://)',value):raise MirrorError('LOCAL_PRIVATE_PATH_DENIED')

def npc_publication_envelope(value):
    # Authority/concurrency transport metadata is not persisted NPC content.
    return {k:value[k] for k in ('revision','savedAt','note','document')}

def identity(value):
    if not isinstance(value,str) or not SAFE_ID.fullmatch(value):raise MirrorError('SOURCE_ID_DENIED')
    return value

class BinariCurrentSource(LegacySource):
    def __init__(self,registered_root):
        super().__init__(registered_root,'ProjectBS')
        if any(p.is_symlink() for p in [self.root,*self.root.parents]):raise MirrorError('SOURCE_SYMLINK_DENIED')

    def _bounded(self,relative):
        raw=self.read_file(relative,MAX_FILE+1)
        if len(raw)>MAX_FILE:raise MirrorError('RESOURCE_TOO_LARGE')
        return raw

    def _asset_bytes(self,path,asset_id):
        if path.startswith('assets/portraits/') and hasattr(self,'npc_asset_read'):
            return self.npc_asset_read(asset_id)
        return self._bounded(path)

    def _current_capture(self):
        resources={};vector={};metadata={'npcCount':0,'portraitIds':[],'categoryIds':[],'entryIds':[],'excludedSourcePaths':0};connections=[]
        def document(key,value):
            inspect_data(value);resources[key]={'kind':'document','mime':'application/json','bytes':canonical(value)}
        def asset(key,path,asset_id,mime,expected_hash=None):
            raw=self._asset_bytes(path,asset_id)
            if len(raw)>MAX_FILE:raise MirrorError('RESOURCE_TOO_LARGE')
            if expected_hash and sha(raw)!=expected_hash:raise MirrorError('PORTRAIT_CONTENT_ADDRESS_MISMATCH')
            if mime=='image/png' and not raw.startswith(b'\x89PNG\r\n\x1a\n'):raise MirrorError('ASSET_TYPE_MISMATCH')
            if mime=='image/jpeg' and not raw.startswith(b'\xff\xd8\xff'):raise MirrorError('ASSET_TYPE_MISMATCH')
            if mime=='image/webp' and not (raw.startswith(b'RIFF') and raw[8:12]==b'WEBP'):raise MirrorError('ASSET_TYPE_MISMATCH')
            if mime=='image/svg+xml':
                text=raw.decode('utf-8')
                if re.search(r'<\s*(?:script|foreignObject|image|use)\b|\bon\w+\s*=|(?:href\s*=|url\s*\()',text,re.I):raise MirrorError('SVG_ACTIVE_CONTENT_DENIED')
            resources[key]={'kind':'asset','assetId':asset_id,'mime':mime,'bytes':raw}
        try:
            data=self.concept_export()
            base={'sourceRevisionVector':data['sourceRevisionVector'],'resources':{'concept':{'kind':'document','mime':'application/json','bytes':canonical(data['envelope'])},'concept.schema':{'kind':'document','mime':'application/json','bytes':data['schema'].encode()}}}
            envelope=json.loads(base['resources']['concept']['bytes']);inspect_data(envelope)
            if envelope['document'].get('projectId')!=self.project:raise MirrorError('SOURCE_PROJECT_MISMATCH')
            resources.update(base['resources']);vector.update(base['sourceRevisionVector'])
            documents={}
            for key,filename in [('story','story.sqlite3'),('npcs','npc.sqlite3')]:
                if key=='npcs' and hasattr(self,'npc_current'):
                    current=self.npc_current();value=current['document']
                    if value.get('projectId')!=self.project:raise MirrorError('SOURCE_PROJECT_MISMATCH')
                    documents[key]=value;document(key,npc_publication_envelope(current));vector[key]=current['revision'];continue
                db=self.connect(filename);connections.append(db)
                row=db.execute('SELECT revision,saved_at,note,document FROM revisions ORDER BY revision DESC LIMIT 1').fetchone()
                if not row:raise MirrorError('SOURCE_EMPTY')
                value=json.loads(row['document'])
                if value.get('projectId')!=self.project:raise MirrorError('SOURCE_PROJECT_MISMATCH')
                documents[key]=value;document(key,{'revision':row['revision'],'savedAt':row['saved_at'],'note':row['note'],'document':value});vector[key]=row['revision']
            chars=documents['npcs']['characters'];ids=[identity(c['id']) for c in chars]
            if len(set(ids))!=len(ids):raise MirrorError('DUPLICATE_NPC_ID')
            portraits=set()
            for c in chars:
                refs={p['assetId'] for p in c.get('portraits',[])}
                if any(not isinstance(a,str) or not PORTRAIT.fullmatch(a) for a in refs):raise MirrorError('PORTRAIT_ID_DENIED')
                if c.get('activePortraitId') and c['activePortraitId'] not in refs:raise MirrorError('ACTIVE_PORTRAIT_NOT_REFERENCED')
                portraits.update(refs)
            document('npc-profile-catalog',json.loads(self._bounded('npc_profile_catalog.json')))
            relations=json.loads(self._bounded('assets/relationships/manifest.json'));icons=relations['icons']
            if len(icons)!=len(RELATION_KEYS) or {i['key'] for i in icons}!=set(RELATION_KEYS):raise MirrorError('RELATION_MANIFEST_SCOPE_CHANGED')
            for icon in icons:
                for ext,mime in [('svg','image/svg+xml'),('png','image/png')]:
                    expected=icon['key']+'.'+ext
                    if icon[ext]!=expected:raise MirrorError('RELATION_PATH_DENIED')
                    aid='relationship.'+expected;asset('asset.'+aid,'assets/relationships/'+expected,aid,mime)
            document('relationship-icon-manifest',relations)
            for name in sorted(portraits):
                m=PORTRAIT.fullmatch(name);mime={'png':'image/png','jpg':'image/jpeg','jpeg':'image/jpeg','webp':'image/webp'}[m[2]]
                asset('asset.'+name,'assets/portraits/'+name,name,mime,m[1])
            metadata.update(npcCount=len(chars),npcIds=ids,portraitIds=sorted(portraits),chapterCount=len(documents['story'].get('chapters',[])),episodeCount=sum(len(c.get('episodes',[])) for c in documents['story'].get('chapters',[])),npcMasterRevision=documents['npcs'].get('masterRevision'),npcStoryRevision=documents['npcs'].get('storyRevision'),storyMasterRevision=documents['story'].get('masterRevision'))
        finally:
            for db in connections:db.close()
        vector['currentContent']=digest({k:sha(v['bytes']) for k,v in sorted(resources.items())})
        return {'resources':resources,'sourceRevisionVector':vector,'consistency':{'database':'rollback-journal-read-cohort','static':'file-identity-and-repeated-hashes','globalAtomic':False,'history':'excluded-current-only'},'metadata':metadata}

    def discover(self):
        """Local review only. No module imports, history query or filesystem enumeration."""
        if self._observations is not None:raise MirrorError('CONCURRENT_CAPTURE_DENIED')
        self._observations={}
        try:
            with self.read_cohort():
                first=self._current_capture();second=self._current_capture()
                if first!=second:raise MirrorError('SOURCE_CHANGED_DURING_CAPTURE')
                for relative in list(self._observations):self.read_file(relative,0)
                return second
        finally:self._observations=None

    def candidate(self,capture):
        body={'schemaVersion':'binari-current-policy.v1','projectId':'binari','legacySourceProjectId':self.project,'enabled':False,'sourceRevisionVector':capture['sourceRevisionVector'],'resources':[{'logicalResourceId':k,'kind':v['kind'],'mime':v['mime'],'size':len(v['bytes']),'sha256':sha(v['bytes']),**({'assetId':v['assetId']} if 'assetId' in v else {})} for k,v in sorted(capture['resources'].items())],'metadata':capture['metadata']}
        return {**body,'policyHash':digest(body)}

    def capture(self,policy=None,*,local_review=False):
        if not policy:raise MirrorError('EXPLICIT_CURRENT_POLICY_REQUIRED')
        expected_hash=policy.get('policyHash');body={k:v for k,v in policy.items() if k!='policyHash'}
        if digest(body)!=expected_hash:raise MirrorError('POLICY_HASH_MISMATCH')
        if policy.get('projectId')!='binari' or policy.get('legacySourceProjectId')!=self.project:raise MirrorError('POLICY_PROJECT_MISMATCH')
        if not local_review and policy.get('enabled') is not True:raise MirrorError('CURRENT_POLICY_NOT_ENABLED')
        observed=self.discover();candidate=self.candidate(observed)
        for key in ('sourceRevisionVector','resources','metadata'):
            if candidate[key]!=policy.get(key):raise MirrorError('CURRENT_SCOPE_OR_BYTES_CHANGED_REVIEW_REQUIRED')
        return observed
