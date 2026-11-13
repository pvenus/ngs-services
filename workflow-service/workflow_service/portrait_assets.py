"""Fixed canonical portrait transport and immutable native reference cache."""
import hashlib,json,os,pathlib,re,struct,urllib.request,urllib.parse,uuid,zlib
from .scene_portraits import verify_selection,digest

def png(raw,expected):
 if len(raw)>33554432 or len(raw)<45 or raw[:8]!=b'\x89PNG\r\n\x1a\n' or hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('PORTRAIT_REFERENCE_BYTES_OR_HASH_INVALID')
 pos=8;size=None;idat=False;ended=False
 while pos<len(raw):
  if pos+12>len(raw):raise ValueError('PORTRAIT_REFERENCE_PNG_INVALID')
  n=struct.unpack('>I',raw[pos:pos+4])[0];kind=raw[pos+4:pos+8];data=raw[pos+8:pos+8+n]
  if pos+12+n>len(raw) or zlib.crc32(kind+data)&0xffffffff!=struct.unpack('>I',raw[pos+8+n:pos+12+n])[0]:raise ValueError('PORTRAIT_REFERENCE_PNG_INVALID')
  if kind==b'IHDR':
   if size or n!=13:raise ValueError('PORTRAIT_REFERENCE_PNG_INVALID')
   size=struct.unpack('>II',data[:8])
   if not all(1<=x<=16384 for x in size) or size[0]*size[1]>67108864:raise ValueError('PORTRAIT_REFERENCE_DIMENSIONS_INVALID')
  if kind==b'IDAT':idat=True
  pos+=12+n
  if kind==b'IEND':ended=True;break
 if not size or not idat or not ended or pos!=len(raw):raise ValueError('PORTRAIT_REFERENCE_PNG_INVALID')
 return {'bytes':len(raw),'width':size[0],'height':size[1],'sha256':expected}

class CanonicalSceneSource:
 def __init__(self,base_url='http://127.0.0.1:8030'):self.base_url=base_url.rstrip('/')
 def _read(self,path,limit):
  url=urllib.parse.urlsplit(self.base_url)
  if url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost') or url.port!=8030 or url.path not in ('','/') or url.query or url.fragment or url.username:raise ValueError('PORTRAIT_CANONICAL_LOOPBACK_SOURCE_REQUIRED')
  class NoRedirect(urllib.request.HTTPRedirectHandler):
   def redirect_request(self,*a):raise ValueError('PORTRAIT_SOURCE_REDIRECT_DENIED')
  with urllib.request.build_opener(NoRedirect).open(self.base_url+path,timeout=15) as response:raw=response.read(limit+1)
  if len(raw)>limit:raise ValueError('PORTRAIT_SOURCE_TOO_LARGE')
  return raw
 def snapshot(self):return {k:json.loads(self._read('/api/'+p,2097152)) for k,p in [('story','story'),('npcs','npcs')]}
 def fetch(self,asset):
  if not isinstance(asset,str) or not re.fullmatch('[a-f0-9]{64}\\.png',asset):raise ValueError('PORTRAIT_ASSET_ID_INVALID')
  return self._read('/assets/portraits/'+asset,33554432)

class PortraitReferenceCache:
 def __init__(self,root,fetch):self.root=pathlib.Path(root);self.fetch=fetch
 def materialize(self,selection):
  verify_selection(selection);blockers=list(selection['blockers']);refs=[]
  if not self.root.is_absolute() or self.root.is_symlink():raise ValueError('PORTRAIT_CACHE_ROOT_INVALID')
  self.root.mkdir(parents=True,exist_ok=True,mode=0o700);root=self.root.resolve()
  for item in selection['selected']:
   if item['status']!='selected':continue
   try:
    asset=item['mainPortraitAssetId'];expected=item['sha256']
    if not re.fullmatch('[a-f0-9]{64}\\.png',asset) or asset[:-4]!=expected:raise ValueError('PORTRAIT_ASSET_ID_HASH_MISMATCH')
    path=root/asset
    if path.is_symlink():raise ValueError('PORTRAIT_CACHE_SYMLINK_DENIED')
    # Re-read canonical bytes even when cached. Reachability and hash are not
    # inferred from a filename or a stale previous download.
    raw=self.fetch(asset);meta=png(raw,expected)
    if path.exists():
     if path.read_bytes()!=raw:raise ValueError('PORTRAIT_CACHE_CORRUPT')
    else:
     temp=root/('.'+asset+'.'+uuid.uuid4().hex)
     try:
      with temp.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
      os.chmod(temp,0o600);os.replace(temp,path)
     finally:
      if temp.exists():temp.unlink()
    refs.append({'npcId':item['npcId'],'name':item['name'],'mainPortraitAssetId':asset,'assetVersion':item['assetVersion'],'npcRevision':item['npcRevision'],'portraitEntryHash':item['portraitEntryHash'],'localPath':str(path),**meta})
   except Exception as e:blockers.append({'npcId':item['npcId'],'code':'MAIN_PORTRAIT_ASSET_UNAVAILABLE_OR_INVALID','reason':str(e)[:200]})
  ready=not blockers and len(refs)==len(selection['selected']) and 1<=len(refs)<=5
  if len(refs)>5:blockers.append({'code':'NATIVE_REFERENCE_LIMIT_EXCEEDED'})
  out={'schemaVersion':'npc-node-portrait-reference-manifest.v1','selectionHash':selection['selectionHash'],'references':refs,'blockers':blockers,'generationReady':ready};out['manifestHash']=digest(out);return out
 def verify(self,manifest):
  if manifest.get('manifestHash')!=digest({k:v for k,v in manifest.items() if k!='manifestHash'}):raise ValueError('PORTRAIT_REFERENCE_MANIFEST_HASH_INVALID')
  for ref in manifest['references']:
   path=pathlib.Path(ref['localPath'])
   if path.is_symlink() or path.parent.resolve()!=self.root.resolve() or path.name!=ref['mainPortraitAssetId']:raise ValueError('PORTRAIT_REFERENCE_PATH_BINDING_INVALID')
   png(path.read_bytes(),ref['sha256'])
  return manifest
