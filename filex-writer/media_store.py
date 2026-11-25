"""Immutable request-attempt media originals. No NPC slot or music catalog mutation.
Root is a dedicated deployment binding, never supplied by a worker.
"""
import base64, hashlib, io, json, re, struct, wave, zlib
from server import Store, Conflict

from media_validation import SCOPE,wire,sha,require,validate,png

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
   png(raw)
   i=8
   while i<len(raw):
    size,kind=struct.unpack_from('>I4s',raw,i)
    require(kind not in (b'acTL',b'fcTL',b'fdAT'),'cinematic source must be a still PNG')
    i+=12+size
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

# Native PNG sequences share the cinematic authentication scope. Frames are
# never written to a visible result path until the whole manifest verifies.
import math,os,stat

SEQUENCE_ROUTE='/v1/workflow/cinematics-sequence'
SEQUENCE_MAX_FRAME=4*1024*1024
SEQUENCE_MAX_TOTAL=64*1024*1024
SEQUENCE_FRAME_COUNT=45
SEQUENCE_KEYS={'schemaVersion','projectId','officialRequestId','sourceSha256','width','height',
               'masterFps','frameCount','frames','playback'}

def sequence_playback(p):
 require(type(p)is dict and set(p)=={'durationFrames','speedCurve','cameraCurve'},'sequence playback fields')
 length=p['durationFrames']
 require(type(length)is int and 1<=length<=1440,'sequence duration')
 def bounded(n,low,high):
  return type(n) in (int,float) and low<=n<=high and math.isfinite(n)
 for name,limits in (('speedCurve',{'rate':(0,4)}),('cameraCurve',{'zoom':(1,3),'x':(0,1),'y':(0,1)})):
  points=p[name]
  require(type(points)is list and 1<=len(points)<=128,'sequence playback points')
  prior=-1
  for i,pt in enumerate(points):
   require(type(pt)is dict and set(pt)=={'frame',*limits} and type(pt['frame'])is int and
    prior<pt['frame']<length and (i>0 or pt['frame']==0) and
    all(bounded(pt[k],*limits[k]) for k in limits),'sequence playback curve')
   prior=pt['frame']
 return p

def seq_frame_meta(value,index=None):
 require(type(value)is dict and set(value)=={'index','sha256','bytes'} and
  type(value['index'])is int and 0<=value['index']<SEQUENCE_FRAME_COUNT and
  (index is None or value['index']==index) and cinematic_match(value['sha256'],CINEMATIC_DIGEST) and
  type(value['bytes'])is int and 0<value['bytes']<=SEQUENCE_MAX_FRAME,'sequence frame metadata')
 return value

def seq_manifest(m):
 require(type(m)is dict and set(m)==SEQUENCE_KEYS and m['schemaVersion']=='ngs.cinematic-sequence-result.v1' and
  m['projectId']=='binari' and cinematic_match(m['officialRequestId'],CINEMATIC_ID) and
  cinematic_match(m['sourceSha256'],CINEMATIC_DIGEST) and type(m['masterFps'])is int and m['masterFps']==24 and
  type(m['frameCount'])is int and m['frameCount']==SEQUENCE_FRAME_COUNT and
  all(type(m[k])is int and 128<=m[k]<=512 and m[k]%32==0 for k in ('width','height')) and
  type(m['frames'])is list and len(m['frames'])==SEQUENCE_FRAME_COUNT,'sequence manifest')
 require(sum(seq_frame_meta(row,i)['bytes'] for i,row in enumerate(m['frames']))<=SEQUENCE_MAX_TOTAL,'sequence byte budget')
 sequence_playback(m['playback'])
 return m

def seq_png(raw,line):
 require(len(raw)==line['bytes'] and sha(raw)==line['sha256'] and len(raw)>=57 and
  raw[:16]==b'\x89PNG\r\n\x1a\n\0\0\0\rIHDR' and raw[24:29]==b'\x08\x02\0\0\0','sequence RGB integrity')
 w,h=struct.unpack_from('>II',raw,16)
 require(all(128<=v<=512 and v%32==0 for v in (w,h)),'sequence geometry')
 png(raw)
 at=8
 while at<len(raw):
  n,kind=struct.unpack_from('>I4s',raw,at)
  require(kind not in (b'acTL',b'fcTL',b'fdAT',b'tRNS'),'sequence frame must be still RGB')
  at+=12+n
 return w,h

def seq_sync_dir(p):
 fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:os.fsync(fd)
 finally:os.close(fd)

class CinematicSequenceStore(CinematicStore):
 def _seq_dir(self,name):
  base=self.root/'sequences'
  require(not base.is_symlink(),'sequence scope folder')
  child=base/name
  require(not child.is_symlink(),'sequence result folder')
  return child
 def _seq_frames(self,root):
  p=root/'frames'
  require(not p.is_symlink(),'sequence frames folder')
  return p
 def _seq_verify_frames(self,root,m):
  folder=self._seq_frames(root)
  expected={f'{row["index"]:04}.png' for row in m['frames']}
  require({p.name for p in folder.iterdir()}==expected,'sequence files incomplete')
  for row in m['frames']:
   raw=self._read(folder/f'{row["index"]:04}.png',SEQUENCE_MAX_FRAME)
   require(seq_png(raw,row)==(m['width'],m['height']),'sequence frame geometry mismatch')
 def _seq_result(self,oid):
  require(cinematic_match(oid,CINEMATIC_ID),'sequence result id')
  folder=self._seq_dir(oid)
  raw=self._read(folder/'manifest.json',64*1024)
  m=seq_manifest(json.loads(raw))
  require(m['officialRequestId']==oid and wire(m)==raw,'sequence receipt integrity')
  self._seq_verify_frames(folder,m)
  return m
 def cinematic_sequence_result(self,p):
  require(type(p)is dict and set(p)=={'officialRequestId'},'sequence result request')
  with self.lock():return self._seq_result(p['officialRequestId'])
 def cinematic_sequence_frame(self,p):
  require(type(p)is dict and set(p)=={'officialRequestId','frame'} and cinematic_match(p['officialRequestId'],CINEMATIC_ID),'sequence frame request')
  oid=p['officialRequestId']; frame=p['frame']
  require(type(frame)is dict and set(frame)=={'index','sha256','bytes','base64'} and type(frame['base64'])is str and
   len(frame['base64'])<=4*((SEQUENCE_MAX_FRAME+2)//3),'sequence frame packet')
  meta=seq_frame_meta({k:v for k,v in frame.items() if k!='base64'})
  data=base64.b64decode(frame['base64'],validate=True)
  seq_png(data,meta)
  index=meta['index']; name=f'{index:04}.png'
  with self.lock():
   final=self._seq_dir(oid)
   if final.exists():
    # Replayed frame acknowledgements validate just that immutable frame.
    raw=self._read(final/'manifest.json',64*1024)
    m=seq_manifest(json.loads(raw))
    require(wire(m)==raw and m['officialRequestId']==oid,'sequence receipt integrity')
    if m['frames'][index]!=meta or self._read(self._seq_frames(final)/name,SEQUENCE_MAX_FRAME)!=data:
     raise Conflict('immutable sequence frame')
   else:
    stage=self._seq_dir('.staged-'+oid)
    folder=self._seq_frames(stage); dest=folder/name
    if dest.exists() or dest.is_symlink():
     if self._read(dest,SEQUENCE_MAX_FRAME)!=data:raise Conflict('sequence staged frame changed')
    else:
     saved=0
     if folder.exists():
      for f in folder.iterdir():
       n=f.lstat()
       require(re.fullmatch(r'00(?:[0-3][0-9]|4[0-4])\.png',f.name) and stat.S_ISREG(n.st_mode) and n.st_nlink==1 and
        0<n.st_size<=SEQUENCE_MAX_FRAME,'sequence stage integrity')
       saved+=n.st_size
     require(saved+len(data)<=SEQUENCE_MAX_TOTAL,'sequence stage byte budget')
     self.write(dest,data)
     require(self._read(dest,SEQUENCE_MAX_FRAME)==data,'sequence stage readback')
   return {'ok':True,'index':index,'sha256':meta['sha256']}
 def cinematic_sequence_publish(self,p):
  require(type(p)is dict and set(p)=={'manifest'},'sequence publish request')
  manifest=seq_manifest(p['manifest']);oid=manifest['officialRequestId']
  with self.lock():
   final=self._seq_dir(oid)
   if final.exists():
    if self._seq_result(oid)!=manifest:raise Conflict('immutable sequence manifest')
    return manifest
   stage=self._seq_dir('.staged-'+oid)
   self._seq_verify_frames(stage,manifest)
   self.write(stage/'manifest.json',wire(manifest))
   require(self._read(stage/'manifest.json',64*1024)==wire(manifest),'sequence pre-publication manifest readback')
   # The rename is the single visibility boundary. Its source and destination
   # are siblings in the same controlled mount. Folders are committed once.
   os.rename(stage,final)
   seq_sync_dir(final.parent)
   return self._seq_result(oid)

PLAYBACK_ROUTE='/v1/workflow/cinematics-playback'
PLAYBACK_SCHEMA='ngs.cinematic-playback-state.v1'
PLAYBACK_LIMIT=64*1024
MAX_SAFE_INTEGER=2**53-1

def playback_state(value,oid):
 require(type(value)is dict and set(value)=={'schemaVersion','projectId','sequenceId','revision','playback'} and
  value['schemaVersion']==PLAYBACK_SCHEMA and value['projectId']=='binari' and value['sequenceId']==oid and
  type(value['revision'])is int and 1<=value['revision']<=MAX_SAFE_INTEGER,'playback state')
 sequence_playback(value['playback'])
 return value

def playback_edit(value):
 require(type(value)is dict and set(value)=={'officialRequestId','sequenceId','expectedRevision','playback'} and
  all(cinematic_match(value[k],CINEMATIC_ID) for k in ('officialRequestId','sequenceId')) and
  type(value['expectedRevision'])is int and 0<=value['expectedRevision']<MAX_SAFE_INTEGER,'playback edit fields')
 sequence_playback(value['playback'])
 return value

class CinematicPlaybackStore(CinematicSequenceStore):
 def _playback_base(self,oid):
  require(cinematic_match(oid,CINEMATIC_ID),'playback sequence id')
  folder=self._seq_dir(oid)
  raw=self._read(folder/'manifest.json',64*1024)
  m=seq_manifest(json.loads(raw))
  require(m['officialRequestId']==oid and wire(m)==raw,'playback original manifest integrity')
  return folder,m
 def _playback_current(self,folder,manifest):
  oid=manifest['officialRequestId'];path=folder/'timeline.json'
  try:raw=self._read(path,PLAYBACK_LIMIT)
  except FileNotFoundError:return {'revision':0,'playback':manifest['playback']}
  state=playback_state(json.loads(raw),oid)
  require(wire(state)==raw,'playback state integrity')
  return state
 def _playback_plan(self,edit,folder):
  rev=edit['expectedRevision']+1
  state={'schemaVersion':PLAYBACK_SCHEMA,'projectId':'binari','sequenceId':edit['sequenceId'],
         'revision':rev,'playback':edit['playback']}
  receipts=folder/'.playback-receipts'; require(not receipts.is_symlink(),'playback receipt directory')
  entry=receipts/(str(rev)+'.json')
  receipt={'officialRequestId':edit['officialRequestId'],'state':state}
  return state,entry,receipt
 def cinematic_playback_target(self,packet):
  require(type(packet)is dict and set(packet)=={'sequenceId','expectedRevision'} and
   type(packet['expectedRevision'])is int and 0<=packet['expectedRevision']<MAX_SAFE_INTEGER,'playback target')
  with self.lock():
   folder,m=self._playback_base(packet['sequenceId'])
   current=self._playback_current(folder,m)
   if current['revision']!=packet['expectedRevision']:raise Conflict('playback target moved')
   return {'ok':True,'sequenceId':packet['sequenceId'],'revision':current['revision']}
 def cinematic_playback_update(self,packet):
  edit=playback_edit(packet)
  with self.lock():
   folder,m=self._playback_base(edit['sequenceId'])
   current=self._playback_current(folder,m)
   state,entry,receipt=self._playback_plan(edit,folder);frozen=wire(receipt)
   # Freeze the winning update before advancing the public state. A competing
   # request cannot take this revision after an interrupted write. Retry of
   # the owning request either continues or returns its immutable receipt.
   if entry.exists() or entry.is_symlink():
    if self._read(entry,PLAYBACK_LIMIT)!=frozen:raise Conflict('playback revision owned by another request')
   elif current['revision']!=edit['expectedRevision']:
    raise Conflict('playback update stale')
   else:self.write(entry,frozen)
   if current['revision']==edit['expectedRevision']:
    self.write(folder/'timeline.json',wire(state))
    require(self._playback_current(folder,m)==state,'playback durable write')
   else:
    require(current['revision']>=state['revision'] and
            (current['revision']>state['revision'] or current==state),'playback receipt not committed')
   require(self._read(entry,PLAYBACK_LIMIT)==frozen,'playback receipt readback')
   return state
 def cinematic_playback_result(self,packet):
  edit=playback_edit(packet)
  with self.lock():
   folder,m=self._playback_base(edit['sequenceId'])
   state,entry,receipt=self._playback_plan(edit,folder)
   current=self._playback_current(folder,m)
   require(current['revision']>=state['revision'] and
    (current['revision']>state['revision'] or current==state) and
    self._read(entry,PLAYBACK_LIMIT)==wire(receipt),'playback result evidence')
   return state

SOURCE_ROUTE='/v1/workflow/cinematics-source'
ARCHIVE_ROUTE='/v1/workflow/cinematics-sequence-remove'
SOURCE_SCHEMA='ngs.cinematic-source-result.v1'
ARCHIVE_SCHEMA='ngs.cinematic-sequence-archive.v1'
ARCHIVE_LIMIT=1024*1024
MAX_ARCHIVED=4096

def source_entry(m):
 require(type(m)is dict and set(m)=={'schemaVersion','projectId','officialRequestId','title','sha256','bytes','width','height'} and
  m['schemaVersion']==SOURCE_SCHEMA and m['projectId']=='binari' and cinematic_match(m['officialRequestId'],CINEMATIC_ID) and
  cinematic_match(m['sha256'],CINEMATIC_DIGEST) and type(m['bytes'])is int and 0<m['bytes']<=CINEMATIC_MAX and
  type(m['title'])is str and 1<=len(m['title'])<=120 and m['title']==m['title'].strip() and
  type(m['width'])is int and type(m['height'])is int and m['width']==1536 and m['height']==1024,'source metadata')
 return m

def source_image(raw,m):
 require(len(raw)==m['bytes'] and sha(raw)==m['sha256'] and len(raw)>=57 and
  raw[:16]==b'\x89PNG\r\n\x1a\n\0\0\0\rIHDR' and
  struct.unpack_from('>II',raw,16)==(m['width'],m['height']),'source image digest/size')
 png(raw)
 at=8
 while at<len(raw):
  n,k=struct.unpack_from('>I4s',raw,at)
  require(k not in (b'acTL',b'fcTL',b'fdAT'),'source image still')
  at+=12+n

def archive_ids(ids,limit=30):
 require(type(ids)is list and 1<=len(ids)<=limit and all(cinematic_match(p,CINEMATIC_ID) for p in ids) and
  len(set(ids))==len(ids),'archive selection')
 return ids

class CinematicProjectStore(CinematicPlaybackStore):
 def _private_result(self,folder,oid):
  require(cinematic_match(oid,CINEMATIC_ID) and folder in ('.source-receipts','.source-abandoned','.archive-receipts'),'cinematic receipt')
  directory=self.root/folder;require(not directory.is_symlink(),'cinematic receipt folder')
  return directory/(oid+'.json')
 def _result_source(self,oid):
  receipt=self._private_result('.source-receipts',oid)
  raw=self._read(receipt,8192);m=source_entry(json.loads(raw))
  require(wire(m)==raw and m['officialRequestId']==oid,'source receipt integrity')
  p=self._path('sources',m['sha256']+'.png')
  source_image(self._read(p,CINEMATIC_MAX),m)
  public=self._read(self._path('sources',m['sha256']+'.json'),8192)
  alias=source_entry(json.loads(public))
  require(wire(alias)==public and all(alias[k]==m[k] for k in ('sha256','bytes','width','height')),'source alias integrity')
  return m
 def _source_fence(self,oid):
  path=self._private_result('.source-abandoned',oid)
  try:raw=self._read(path,256)
  except FileNotFoundError:return path,0
  record=json.loads(raw)
  require(type(record)is dict and set(record)=={'claimStamp'} and wire(record)==raw and
          self._source_stamp(record['claimStamp']),'source fence integrity')
  return path,record['claimStamp']
 @staticmethod
 def _source_stamp(stamp):
  return type(stamp)is int and 0<stamp<=2**53-1
 def cinematic_source_reconcile(self,p):
  require(type(p)is dict and set(p)=={'officialRequestId','claimStamp'} and
          self._source_stamp(p['claimStamp']),'source reconcile packet')
  oid=p['officialRequestId'];stamp=p['claimStamp']
  with self.lock():
   receipt=self._private_result('.source-receipts',oid)
   # Receipt is written before media. A partial publish is not an absent publish.
   try:self._read(receipt,8192)
   except FileNotFoundError:pass
   else:return {'state':'published','manifest':self._result_source(oid)}
   path,fence=self._source_fence(oid)
   # Under the writer lock, fence all delayed sends for this exact or older claim.
   if stamp>fence:self.write(path,wire({'claimStamp':stamp}))
   return {'state':'absent_fenced'}
 def cinematic_source_generate(self,p):
  require(type(p)is dict and set(p)=={'manifest','base64','claimStamp'} and self._source_stamp(p['claimStamp']) and type(p['base64'])is str and
   len(p['base64'])<=4*((CINEMATIC_MAX+2)//3),'source generation packet')
  m=source_entry(p['manifest']); raw=base64.b64decode(p['base64'],validate=True);source_image(raw,m)
  oid=m['officialRequestId'];s=m['sha256'];frozen=wire(m)
  with self.lock():
   _,fence=self._source_fence(oid)
   if p['claimStamp']<=fence:raise Conflict('source claim fenced')
   receipt=self._private_result('.source-receipts',oid)
   if receipt.exists() or receipt.is_symlink():
    if self._read(receipt,8192)!=frozen:raise Conflict('source image request reused')
   else:self.write(receipt,frozen)
   image=self._path('sources',s+'.png'); meta=self._path('sources',s+'.json')
   if image.exists():
    require(self._read(image,CINEMATIC_MAX)==raw,'source bytes collision')
   else:self.write(image,raw)
   require(self._read(image,CINEMATIC_MAX)==raw,'source bytes readback')
   if not meta.exists():self.write(meta,frozen)
   return self._result_source(oid)
 def cinematic_source_result(self,p):
  require(type(p)is dict and set(p)=={'officialRequestId'},'source result packet')
  with self.lock():return self._result_source(p['officialRequestId'])
 def _archived(self):
  path=self.root/'sequence-archive.json'
  try:raw=self._read(path,ARCHIVE_LIMIT)
  except FileNotFoundError:return set()
  m=json.loads(raw)
  require(type(m)is dict and set(m)=={'schemaVersion','projectId','sequenceIds'} and m['schemaVersion']==ARCHIVE_SCHEMA and
   m['projectId']=='binari' and wire(m)==raw and m['sequenceIds']==sorted(archive_ids(m['sequenceIds'],MAX_ARCHIVED)),'archive state')
  return set(m['sequenceIds'])
 def _archive_packet(self,p):
  require(type(p)is dict and set(p)=={'officialRequestId','sequenceIds'} and cinematic_match(p['officialRequestId'],CINEMATIC_ID),'archive packet')
  archive_ids(p['sequenceIds'])
  return self._private_result('.archive-receipts',p['officialRequestId'])
 def _archive_result(self,p):
  receipt=self._archive_packet(p)
  require(self._read(receipt,8192)==wire(p) and set(p['sequenceIds'])<=self._archived(),'archive not committed')
  return p
 def cinematic_sequences_archive_result(self,p):
  with self.lock():return self._archive_result(p)
 def cinematic_sequences_archive(self,p):
  receipt=self._archive_packet(p)
  with self.lock():
   existing=self._archived();selection=set(p['sequenceIds']);new=existing|selection
   require(len(new)<=MAX_ARCHIVED,'archive bound')
   for oid in selection:self._playback_base(oid)
   if receipt.exists() or receipt.is_symlink():
    if self._read(receipt,8192)!=wire(p):raise Conflict('archive request reused')
   else:self.write(receipt,wire(p))
   if existing!=new:
    self.write(self.root/'sequence-archive.json',wire({'schemaVersion':ARCHIVE_SCHEMA,'projectId':'binari','sequenceIds':sorted(new)}))
   return self._archive_result(p)
