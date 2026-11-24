"""Immutable request-attempt media originals. No NPC slot or music catalog mutation.
Root is a dedicated deployment binding, never supplied by a worker.
"""
import base64, hashlib, io, json, re, struct, wave, zlib
from server import Store, Conflict

from media_validation import SCOPE,wire,sha,require,validate

class MediaStore(Store):
 namespace='request-media-artifacts.v1'
 def internal(self,*parts):
  p=self.root
  for part in parts:
   p=p/part;require(not p.is_symlink(),'media symlink')
  return p
 def path(self,key):raise ValueError('generic media access forbidden')
 def snapshot(self):raise ValueError('generic media access forbidden')
 def cas(self,*a,**kw):raise ValueError('generic media access forbidden')
 def rid(self,request,attempt):
  require(all(isinstance(x,str)and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]{0,199}',x)for x in (request,attempt)),'media receipt ids')
  return sha(wire([request,attempt]))
 def media_receipt(self,request,attempt):
  with self.lock():return self.read_receipt(request,attempt)
 def read_receipt(self,request,attempt):
  p=self.internal('receipts',self.rid(request,attempt)+'.json');r=json.loads(p.read_bytes());h=r.pop('receiptHash');require(sha(wire(r))==h,'media receipt integrity');r['receiptHash']=h
  require(r['requestId']==request and r['attemptId']==attempt,'media receipt binding');self.read_asset(r['artifact']['assetId']);return r
 def read_asset(self,asset):
  require(isinstance(asset,str)and re.fullmatch('[a-f0-9]{64}\\.(wav|png)',asset),'media asset id')
  data=self.internal('assets',asset).read_bytes();require(sha(data)==asset.split('.')[0],'media asset integrity')
  return {'assetId':asset,'sha256':sha(data),'bytes':len(data),'base64':base64.b64encode(data).decode()}
 def media_asset(self,asset):
  with self.lock():return self.read_asset(asset)
 def media_publish(self,p):
  data,ext=validate(p);identity=sha(wire(p));rid=self.rid(p['requestId'],p['attemptId'])
  with self.lock():
   rp=self.internal('receipts',rid+'.json')
   if rp.exists():
    r=self.read_receipt(p['requestId'],p['attemptId'])
    if r['publicationHash']!=identity:raise Conflict('media idempotency conflict')
    return r
   # Intent persists before bytes. Interrupted attempts can only replay exact bytes.
   intent=self.internal('intents',rid+'.json')
   if intent.exists():
    if json.loads(intent.read_bytes())!={'publicationHash':identity}:raise Conflict('media intent conflict')
   else:self.write(intent,wire({'publicationHash':identity}))
   asset=p['artifact']['sha256']+'.'+ext;ap=self.internal('assets',asset)
   if ap.exists():require(ap.read_bytes()==data,'media asset collision')
   else:self.write(ap,data)
   require(ap.read_bytes()==data,'media readback')
   r={k:p[k]for k in SCOPE};r.update(schemaVersion='pds.media-artifact-receipt.v1',status='committed',authority='filex',verified=True,publicationHash=identity,artifact={k:v for k,v in p['artifact'].items()if k!='base64'},provenance=p['provenance']);r['artifact']['assetId']=asset;r['receiptHash']=sha(wire(r));self.write(rp,wire(r));return self.read_receipt(p['requestId'],p['attemptId'])

# Cinematics has its own narrow authenticated mount; no generic access to it.
CINEMATIC_ROUTE='/v1/workflow/cinematics'
CINEMATIC_MAX=8*1024*1024
CINEMATIC_BODY=12*1024*1024
CINEMATIC_ID=r'request\.official\.[a-f0-9]{64}'
CINEMATIC_DIGEST=r'[a-f0-9]{64}'
CINEMATIC_KEYS={'schemaVersion','projectId','officialRequestId','sourceSha256','sha256',
                'bytes','mimeType','width','height','fps','frameCount'}

def cinematic_match(value,pattern):
 return type(value)is str and re.fullmatch(pattern,value) is not None

def validate_cinematic_manifest(m):
 require(type(m)is dict and set(m)==CINEMATIC_KEYS and m['schemaVersion']=='ngs.cinematic-result.v1' and
  m['projectId']=='binari' and cinematic_match(m['officialRequestId'],CINEMATIC_ID) and
  all(cinematic_match(m[key],CINEMATIC_DIGEST) for key in ('sourceSha256','sha256')) and
  m['mimeType']=='video/mp4' and type(m['bytes'])is int and 0<m['bytes']<=CINEMATIC_MAX and
  type(m['fps'])is int and m['fps']==24 and type(m['frameCount'])is int and m['frameCount']==45 and
  all(type(m[k])is int and 128<=m[k]<=512 and m[k]%32==0 for k in ('width','height')),'cinematic manifest')

def validate_cinematic_mp4(data,m):
 require(len(data)==m['bytes'] and sha(data)==m['sha256'],'cinematic bytes')
 at=0; kinds=set()
 while at<len(data):
  require(len(data)-at>=8,'cinematic mp4 box')
  size,kind=struct.unpack_from('>I4s',data,at); header=8
  if size==1:
   require(len(data)-at>=16,'cinematic mp4 extended size')
   size=struct.unpack_from('>Q',data,at+8)[0]; header=16
  require(header<=size<=len(data)-at and (at>0 or kind==b'ftyp' and size>=16),'cinematic mp4 layout')
  if kind in (b'ftyp',b'mdat',b'moov'):
   require(kind not in kinds and size>header,'cinematic mp4 mandatory box')
   kinds.add(kind)
  at+=size
 require(kinds=={b'ftyp',b'mdat',b'moov'},'cinematic mp4 incomplete')

class CinematicStore(Store):
 namespace='canonical-files/binari/cinematics/'
 def _path(self,folder,name):
  require(folder in ('sources','results') and not (self.root/folder).is_symlink(),'cinematic folder')
  p=self.root/folder/name
  require(not p.is_symlink(),'cinematic symlink')
  return p
 def _read(self,path,maximum):
  # Directories and final entry are opened without following symlinks.
  import os,stat
  directory=os.open(path.parent,os.O_RDONLY|os.O_NOFOLLOW|os.O_DIRECTORY)
  try:
   with os.fdopen(os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory),'rb') as f:
    prior=os.fstat(f.fileno())
    require(stat.S_ISREG(prior.st_mode) and prior.st_nlink==1 and 0<prior.st_size<=maximum,'cinematic file')
    raw=f.read(maximum+1); after=os.fstat(f.fileno())
    require(len(raw)==prior.st_size==after.st_size and prior.st_mtime_ns==after.st_mtime_ns and prior.st_ctime_ns==after.st_ctime_ns,'cinematic source stable')
    return raw
  finally:os.close(directory)
 def cinematic_source(self,p,*,status=False):
  require(type(p)is dict and set(p)=={'sourceSha256'} and cinematic_match(p['sourceSha256'],CINEMATIC_DIGEST),'cinematic source')
  h=p['sourceSha256']
  with self.lock():
   raw=self._read(self._path('sources',h+'.png'),CINEMATIC_MAX)
   require(raw[:16]==b'\x89PNG\r\n\x1a\n\0\0\0\rIHDR' and len(raw)>=33 and sha(raw)==h,'cinematic source integrity')
   w,y=struct.unpack_from('>II',raw,16)
   require(0<w<=4096 and 0<y<=4096 and w*y<=8388608,'cinematic source dimensions')
  return {'sourceSha256':h} if status else {'sourceSha256':h,'base64':base64.b64encode(raw).decode()}
 def _result(self,oid):
  require(cinematic_match(oid,CINEMATIC_ID),'cinematic result id')
  manifest_raw=self._read(self._path('results',oid+'.json'),8192)
  m=json.loads(manifest_raw)
  validate_cinematic_manifest(m)
  require(m['officialRequestId']==oid and wire(m)==manifest_raw,'cinematic manifest integrity')
  data=self._read(self._path('results',oid+'.mp4'),CINEMATIC_MAX)
  validate_cinematic_mp4(data,m)
  return m
 def cinematic_result(self,p):
  require(type(p)is dict and set(p)=={'officialRequestId'},'cinematic result input')
  with self.lock():return self._result(p['officialRequestId'])
 def cinematic_publish(self,p):
  require(type(p)is dict and set(p)=={'manifest','base64'} and type(p['base64'])is str and
          len(p['base64'])<=4*((CINEMATIC_MAX+2)//3),'cinematic publish')
  m=p['manifest'];validate_cinematic_manifest(m)
  raw=base64.b64decode(p['base64'],validate=True);validate_cinematic_mp4(raw,m)
  oid=m['officialRequestId']
  with self.lock():
   video=self._path('results',oid+'.mp4'); metadata=self._path('results',oid+'.json')
   if metadata.exists():
    if self._result(oid)!=m:raise Conflict('cinematic immutable receipt conflict')
    return m
   # MP4 is persisted and checked before the catalog sidecar becomes visible.
   # An interrupted write is resumable only with the identical result bytes.
   if video.exists():
    if self._read(video,CINEMATIC_MAX)!=raw:raise Conflict('cinematic immutable video conflict')
   else:self.write(video,raw)
   require(self._read(video,CINEMATIC_MAX)==raw,'cinematic video readback')
   self.write(metadata,wire(m))
   return self._result(oid)
