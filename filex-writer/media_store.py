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
 def _ensure_unretired_source(self,sha256):
  CinematicTargetDelete(self).ensure_source_active(sha256)
 def _path(self,folder,name):
  require(folder in ('sources','results','backgrounds') and not (self.root/folder).is_symlink(),'cinematic folder')
  p=self.root/folder/name
  require(not p.is_symlink(),'cinematic symlink')
  return p
 def _scene_dir(self,source):
  require(cinematic_match(source,CINEMATIC_DIGEST),'cinematic scene key')
  base=self.root/'scenes';scene=base/source
  require(not base.is_symlink() and not scene.is_symlink(),'cinematic scene links')
  return scene
 def _scene_child(self,scene,folder,name):
  require(folder in ('sequences','results'),'cinematic scene child type')
  base=scene/folder;child=base/name
  require(not base.is_symlink() and not child.is_symlink(),'cinematic scene child links')
  return child
 def _source_path(self,source,ext,*,write=False):
  require(ext in ('png','json'),'cinematic source extension')
  scene=self._scene_dir(source)
  leaf=scene/('source.'+ext)
  require(not leaf.is_symlink(),'cinematic source link')
  old=self._path('sources',source+'.'+ext)
  if scene.exists():return leaf
  if write and not any(self._path('sources',source+'.'+kind).exists() for kind in ('png','json')):return leaf
  return old
 def _location(self,folder,oid):
  require(cinematic_match(oid,CINEMATIC_ID) and folder in ('sequences','results'),'cinematic location key')
  root=self.root/'scene-index'
  base=root/folder; p=base/(oid+'.json')
  require(not root.is_symlink() and not base.is_symlink() and not p.is_symlink(),'cinematic location links')
  return p
 def _located_scene(self,folder,oid):
  try:raw=self._read(self._location(folder,oid),256)
  except FileNotFoundError:return None
  value=json.loads(raw)
  require(type(value)is dict and set(value)=={'sourceSha256'} and raw==wire(value),'cinematic location integrity')
  return self._scene_dir(value['sourceSha256'])
 def _bind_scene(self,folder,oid,source):
  previous=self._located_scene(folder,oid)
  scene=self._scene_dir(source)
  if previous is not None and previous!=scene:raise Conflict('cinematic location already assigned')
  if previous is None:self.write(self._location(folder,oid),wire({'sourceSha256':source}))
  return scene
 def _result_path(self,oid,ext):
  require(ext in ('json','mp4'),'cinematic result extension')
  scene=self._located_scene('results',oid)
  return (self._scene_child(scene,'results',oid+'.'+ext) if scene is not None else self._path('results',oid+'.'+ext))
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
   self._ensure_unretired_source(h)
   raw=self._read(self._source_path(h,'png'),CINEMATIC_MAX)
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
  manifest_raw=self._read(self._result_path(oid,'json'),8192)
  m=json.loads(manifest_raw)
  validate_cinematic_manifest(m)
  require(m['officialRequestId']==oid and wire(m)==manifest_raw,'cinematic manifest integrity')
  scene=self._located_scene('results',oid)
  require(scene is None or scene.name==m['sourceSha256'],'cinematic result location integrity')
  data=self._read(self._result_path(oid,'mp4'),CINEMATIC_MAX)
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
   self._ensure_unretired_source(m['sourceSha256'])
   source=m['sourceSha256']
   if self._scene_dir(source).exists() and not any(self._path('results',oid+'.'+x).exists() for x in ('json','mp4')):
    self._bind_scene('results',oid,source)
   video=self._result_path(oid,'mp4'); metadata=self._result_path(oid,'json')
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
 require(type(m)is dict and set(m) in (SEQUENCE_KEYS,SEQUENCE_KEYS|{'group'},SEQUENCE_KEYS|{'group','upscale'}) and m['schemaVersion']=='ngs.cinematic-sequence-result.v1' and
  m['projectId']=='binari' and cinematic_match(m['officialRequestId'],CINEMATIC_ID) and
  cinematic_match(m['sourceSha256'],CINEMATIC_DIGEST) and type(m['masterFps'])is int and m['masterFps']==24 and
  type(m['frameCount'])is int and
  all(type(m[k])is int for k in ('width','height')) and
  type(m['frames'])is list,'sequence manifest')
 playback=sequence_playback(m['playback'])
 compact='group' in m and playback['durationFrames']==84 and len(playback['speedCurve'])==1 and playback['speedCurve'][0]['rate']==1/6
 count=8 if compact else SEQUENCE_FRAME_COUNT
 require(m['frameCount']==count and len(m['frames'])==count and (not compact or 'upscale' in m),'sequence frame count')
 if 'upscale' in m:
  u=m['upscale']
  base={'model','scale','sourceWidth','sourceHeight'}
  require(type(u)is dict and set(u) in (base,base|{'pixelCorrection'}) and u['model']=='seedvr2-7b-fp16' and
   type(u['scale'])is int and u['scale']==2 and
   all(type(u[k])is int and 128<=u[k]<=512 and u[k]%32==0 for k in ('sourceWidth','sourceHeight')),'sequence upscale geometry')
  pixel=type(m['group'])is dict and m['group'].get('variant')=='pixel'
  reduced=compact and pixel
  if 'pixelCorrection' in u:
   c=u['pixelCorrection'];keys={'method','upscaledFramesSha256'}|({'downscale'} if reduced else set())
   require(pixel and type(c)is dict and set(c)==keys and c['method']=='reference-palette-v1' and
    cinematic_match(c['upscaledFramesSha256'],CINEMATIC_DIGEST) and
    (not reduced or type(c['downscale'])is int and c['downscale']==2),'sequence pixel correction proof')
  else:require(not reduced,'sequence pixel correction proof')
  scale=1 if reduced else 2
  require((m['width'],m['height'])==(u['sourceWidth']*scale,u['sourceHeight']*scale),'sequence final geometry')
 else:require(all(128<=m[k]<=512 and m[k]%32==0 for k in ('width','height')),'sequence legacy geometry')
 require(sum(seq_frame_meta(row,i)['bytes'] for i,row in enumerate(m['frames']))<=SEQUENCE_MAX_TOTAL*(2 if 'upscale' in m else 1),'sequence byte budget')
 if 'group' in m:
  group=m['group']
  require(type(group)is dict and set(group)=={'requestId','variant','backgroundSha256'} and
   cinematic_match(group['requestId'],CINEMATIC_ID) and cinematic_match(group['backgroundSha256'],CINEMATIC_DIGEST) and
   group['variant'] in ('cinematic','pixel') and m['officialRequestId']=='request.official.'+
   sha(json.dumps([group['requestId'],'background_animation_'+group['variant']],separators=(',',':')).encode()),'sequence group integrity')
 return m

def seq_png(raw,line):
 require(len(raw)==line['bytes'] and sha(raw)==line['sha256'] and len(raw)>=57 and
  raw[:16]==b'\x89PNG\r\n\x1a\n\0\0\0\rIHDR' and raw[24:29]==b'\x08\x02\0\0\0','sequence RGB integrity')
 w,h=struct.unpack_from('>II',raw,16)
 require(all(128<=v<=512 and v%32==0 for v in (w,h)) or
  all(256<=v<=1024 and v%64==0 for v in (w,h)),'sequence geometry')
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
  if not name.startswith('.'):
   scene=self._located_scene('sequences',name)
   if scene is not None:return self._scene_child(scene,'sequences',name)
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
  scene=self._located_scene('sequences',oid)
  require(scene is None or scene.name==m['sourceSha256'] and not m.get('group'),'sequence scene integrity')
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
  geometry=seq_png(data,meta)
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
     require(saved+len(data)<=SEQUENCE_MAX_TOTAL*(2 if max(geometry)>512 else 1),'sequence stage byte budget')
     self.write(dest,data)
     require(self._read(dest,SEQUENCE_MAX_FRAME)==data,'sequence stage readback')
   return {'ok':True,'index':index,'sha256':meta['sha256']}
 def cinematic_sequence_publish(self,p):
  require(type(p)is dict and set(p)=={'manifest'},'sequence publish request')
  manifest=seq_manifest(p['manifest']);oid=manifest['officialRequestId']
  with self.lock():
   self._ensure_unretired_source(manifest['sourceSha256'])
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
   if not manifest.get('group') and self._scene_dir(manifest['sourceSha256']).exists():
    scene=self._bind_scene('sequences',oid,manifest['sourceSha256'])
    final=self._scene_child(scene,'sequences',oid)
    final.parent.mkdir(mode=0o700,exist_ok=True)
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
  scene=self._located_scene('sequences',oid)
  require(scene is None or scene.name==m['sourceSha256'] and not m.get('group'),'playback scene integrity')
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
BACKGROUND_ROUTE='/v1/workflow/cinematics-background'
BACKGROUND_SCHEMA='ngs.background-source-result.v1'
ARCHIVE_SCHEMA='ngs.cinematic-sequence-archive.v1'
ARCHIVE_LIMIT=1024*1024
MAX_ARCHIVED=4096

def source_entry(m,background=False):
 require(type(m)is dict and set(m)=={'schemaVersion','projectId','officialRequestId','title','sha256','bytes','width','height'} and
  m['schemaVersion']==(BACKGROUND_SCHEMA if background else SOURCE_SCHEMA) and m['projectId']=='binari' and cinematic_match(m['officialRequestId'],CINEMATIC_ID) and
  cinematic_match(m['sha256'],CINEMATIC_DIGEST) and type(m['bytes'])is int and 0<m['bytes']<=CINEMATIC_MAX and
  type(m['title'])is str and 1<=len(m['title'])<=120 and m['title']==m['title'].strip() and
  type(m['width'])is int and type(m['height'])is int and m['width']==1536 and m['height'] in ((1024,) if background else (768,1024)),'source metadata')
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

BACKGROUND_DELETE_ROOT='canonical-files/binari/cinematics/backgrounds'

class CinematicProjectStore(CinematicPlaybackStore):
 def _background_delete_marker(self,digest):
  require(cinematic_match(digest,CINEMATIC_DIGEST),'background removal digest')
  folder=self.root/'.background-deleted'
  require(not folder.is_symlink(),'background removal marker folder')
  return folder/(digest+'.json')
 def remove_background_file(self,command):
  from types import SimpleNamespace
  from single_target_delete import PinnedDeleteRejected,SingleTargetDelete,validate_target
  try:
   require(type(command)is dict and set(command)=={'operationId','target','expected'},'background deletion envelope')
   target=validate_target(command['target']); expected=command['expected']; operation=command['operationId']
   require(target['root']==BACKGROUND_DELETE_ROOT and target['kind']=='file' and
           cinematic_match(target['path'],r'[a-f0-9]{64}\.(?:png|json)') and
           type(expected)is dict and set(expected)=={'sourceRequestId'} and
           cinematic_match(expected['sourceRequestId'],CINEMATIC_ID) and
           cinematic_match(operation,r'workflow\.task\.[a-f0-9]{64}'),'background deletion binding')
  except (ValueError,KeyError) as exc:raise PinnedDeleteRejected('background deletion rejected before moving') from exc
  digest=target['path'].split('.')[0];oid=expected['sourceRequestId']
  with self.lock():
   try:
    marker=self._background_delete_marker(digest)
    try: frozen=self._read(marker,8192)
    except FileNotFoundError: frozen=None
    if frozen is None:
     current=self._result_source(oid,background=True)
     require(current['sha256']==digest,'background digest pin')
     frozen=wire(current)
     require(self._read(self._path('backgrounds',digest+'.json'),8192)==frozen,'background generation pin')
    else:
     current=source_entry(json.loads(frozen),True)
     require(wire(current)==frozen and current['sha256']==digest and current['officialRequestId']==oid,
             'background removal pin')
    leaf=self._path('backgrounds',target['path'])
    try: raw=self._read(leaf,CINEMATIC_MAX if target['path'].endswith('.png') else 8192)
    except FileNotFoundError:
     if not marker.exists():raise
    else:
     if target['path'].endswith('.png'):source_image(raw,current)
     else:require(raw==frozen,'background metadata pin')
   except (Conflict,ValueError,FileNotFoundError) as exc:
    raise PinnedDeleteRejected('background file changed before moving') from exc
   # The marker authorizes only the published pair, and prevents a delayed worker
   # from recreating either leaf between deletes or after the pair has been retired.
   if not marker.exists():self.write(marker,frozen)
   root=self._path('backgrounds',target['path']).parent
   return SingleTargetDelete(SimpleNamespace(root=root),BACKGROUND_DELETE_ROOT).remove_locked(
    {'operationId':operation,'target':target})

 def _private_result(self,folder,oid):
  require(cinematic_match(oid,CINEMATIC_ID) and folder in ('.source-receipts','.source-abandoned','.archive-receipts','.background-receipts','.background-abandoned'),'cinematic receipt')
  directory=self.root/folder;require(not directory.is_symlink(),'cinematic receipt folder')
  return directory/(oid+'.json')
 def _result_source(self,oid,background=False):
  receipt=self._private_result('.background-receipts' if background else '.source-receipts',oid)
  raw=self._read(receipt,8192);m=source_entry(json.loads(raw),background)
  require(wire(m)==raw and m['officialRequestId']==oid,'source receipt integrity')
  p=self._path('backgrounds',m['sha256']+'.png') if background else self._source_path(m['sha256'],'png')
  source_image(self._read(p,CINEMATIC_MAX),m)
  public=self._read(self._path('backgrounds',m['sha256']+'.json') if background else self._source_path(m['sha256'],'json'),8192)
  alias=source_entry(json.loads(public),background)
  require(wire(alias)==public and all(alias[k]==m[k] for k in ('sha256','bytes','width','height')),'source alias integrity')
  return m
 def _source_fence(self,oid,background=False):
  path=self._private_result('.background-abandoned' if background else '.source-abandoned',oid)
  try:raw=self._read(path,256)
  except FileNotFoundError:return path,0
  record=json.loads(raw)
  require(type(record)is dict and set(record)=={'claimStamp'} and wire(record)==raw and
          self._source_stamp(record['claimStamp']),'source fence integrity')
  return path,record['claimStamp']
 @staticmethod
 def _source_stamp(stamp):
  return type(stamp)is int and 0<stamp<=2**53-1
 def cinematic_source_reconcile(self,p,*,background=False):
  require(type(p)is dict and set(p)=={'officialRequestId','claimStamp'} and
          self._source_stamp(p['claimStamp']),'source reconcile packet')
  oid=p['officialRequestId'];stamp=p['claimStamp']
  with self.lock():
   receipt=self._private_result('.background-receipts' if background else '.source-receipts',oid)
   # Receipt is written before media. A partial publish is not an absent publish.
   try:self._read(receipt,8192)
   except FileNotFoundError:pass
   else:return {'state':'published','manifest':self._result_source(oid,background)}
   path,fence=self._source_fence(oid,background)
   # Under the writer lock, fence all delayed sends for this exact or older claim.
   if stamp>fence:self.write(path,wire({'claimStamp':stamp}))
   return {'state':'absent_fenced'}
 def cinematic_source_generate(self,p,*,background=False):
  require(type(p)is dict and set(p)=={'manifest','base64','claimStamp'} and self._source_stamp(p['claimStamp']) and type(p['base64'])is str and
   len(p['base64'])<=4*((CINEMATIC_MAX+2)//3),'source generation packet')
  m=source_entry(p['manifest'],background); raw=base64.b64decode(p['base64'],validate=True);source_image(raw,m)
  oid=m['officialRequestId'];s=m['sha256'];frozen=wire(m)
  with self.lock():
   if background:
    try:self._read(self._background_delete_marker(s),8192)
    except FileNotFoundError:pass
    else:raise Conflict('background source retired')
   else:self._ensure_unretired_source(s)
   _,fence=self._source_fence(oid,background)
   if p['claimStamp']<=fence:raise Conflict('source claim fenced')
   receipt=self._private_result('.background-receipts' if background else '.source-receipts',oid)
   if receipt.exists() or receipt.is_symlink():
    if self._read(receipt,8192)!=frozen:raise Conflict('source image request reused')
   else:self.write(receipt,frozen)
   image=self._path('backgrounds',s+'.png') if background else self._source_path(s,'png',write=True)
   meta=self._path('backgrounds',s+'.json') if background else self._source_path(s,'json',write=True)
   if image.exists():
    require(self._read(image,CINEMATIC_MAX)==raw,'source bytes collision')
   else:self.write(image,raw)
   require(self._read(image,CINEMATIC_MAX)==raw,'source bytes readback')
   if not meta.exists():self.write(meta,frozen)
   return self._result_source(oid,background)
 def background_pixel_source(self,p,*,status=False,identity=False):
  require(type(p)is dict and set(p)=={'sourceSha256'} and cinematic_match(p['sourceSha256'],r'[a-f0-9]{64}'),'background pixel source')
  sha=p['sourceSha256']
  with self.lock():
   raw=self._read(self._path('backgrounds',sha+'.json'),8192)
   m=source_entry(json.loads(raw),True)
   require(wire(m)==raw and m['sha256']==sha,'background pixel source proof')
   result={'sourceSha256':sha,'title':m['title']}
   if identity:
    image=self._read(self._path('backgrounds',sha+'.png'),CINEMATIC_MAX)
    source_image(image,m)
    return m
   if not status:
    image=self._read(self._path('backgrounds',sha+'.png'),CINEMATIC_MAX)
    source_image(image,m)
    result['base64']=base64.b64encode(image).decode('ascii')
   return result
 def cinematic_source_result(self,p,*,background=False):
  require(type(p)is dict and set(p)=={'officialRequestId'},'source result packet')
  with self.lock():return self._result_source(p['officialRequestId'],background)
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


# Cinematic inventory and pinned deletion adapter for the existing writer.
from single_target_delete import PinnedDeleteRejected, SingleTargetDelete, validate_target

ROOT='canonical-files/binari/cinematics'
DIGEST=r'[a-f0-9]{64}'
OID=r'request\.official\.'+DIGEST
SOURCE=re.compile(r'sources/('+DIGEST+r')\.(json|png)')
SEQUENCE=re.compile(r'sequences/('+OID+r')')
RESULT=re.compile(r'results/('+OID+r')\.(json|mp4)')


class CinematicTargetDelete:
    def __init__(self,store):
        self.store=store

    def _marker(self,source):
        return self.store._path('sources', '.removed-'+source+'.json')

    def ensure_source_active(self,source):
        # Invoked while the existing cinematic writer lock is held.
        marker=self._marker(source)
        if marker.exists():raise Conflict('cinematic source retired')

    def _legacy(self,oid):
        raw=self.store._read(self.store._path('results',oid+'.json'),8192)
        manifest=json.loads(raw)
        validate_cinematic_manifest(manifest)
        if manifest['officialRequestId']!=oid or wire(manifest)!=raw:
            raise ValueError('cinematic result ownership')
        return manifest

    def _uncorrelated_sources(self,source,owner):
        from media_store import source_entry,wire
        receipts=self.store.root/'.source-receipts'
        if receipts.is_symlink():raise ValueError('source receipt scope')
        if not receipts.exists():return
        for entry in receipts.iterdir():
            if not re.fullmatch(OID+r'\.json',entry.name):continue
            raw=self.store._read(entry,8192)
            m=source_entry(json.loads(raw))
            if wire(m)!=raw or entry.name!=m['officialRequestId']+'.json':
                raise ValueError('source receipt integrity')
            if m['sha256']==source and m['officialRequestId']!=owner:
                raise PinnedDeleteRejected('shared cinematic source; cannot delete scene')

    def _remaining_children(self,source):
        found=[]
        for folder in ('sequences','results'):
            path=self.store.root/folder
            if path.is_symlink():raise ValueError('cinematic child scope')
            if not path.exists():continue
            for entry in path.iterdir():
                if folder=='sequences' and re.fullmatch(OID,entry.name):
                    _,manifest=self.store._playback_base(entry.name)
                    if manifest.get('group'):continue
                elif folder=='results' and re.fullmatch(OID+r'\.json',entry.name):
                    manifest=self._legacy(entry.stem)
                elif folder=='results' and re.fullmatch(OID+r'\.mp4',entry.name):
                    # An interrupted/legacy write with no sidecar cannot be
                    # safely assigned to a scene. Do not guess from filenames.
                    if not entry.with_suffix('.json').is_file():
                        raise PinnedDeleteRejected('unbound cinematic result file')
                    continue
                else:continue
                if manifest['sourceSha256']==source:
                    if folder=='sequences':
                        found.append({'root':ROOT,'path':'sequences/'+entry.name,'kind':'directory'})
                    else:
                        video=self.store._path('results',entry.stem+'.mp4')
                        if video.exists():
                            found.append({'root':ROOT,'path':'results/'+video.name,'kind':'file'})
                        found.append({'root':ROOT,'path':'results/'+entry.name,'kind':'file'})
        return sorted(found,key=lambda t:(t['path'].split('/')[0]!='sequences',t['path'].rsplit('.',1)[0],not t['path'].endswith('.mp4')))

    def inventory(self,source):
        from media_store import CINEMATIC_MAX,source_entry,source_image,wire
        if type(source)is not str or not re.fullmatch(DIGEST,source):raise ValueError('cinematic delete source')
        with self.store.lock():
            marker=self._marker(source)
            scene=self.store._scene_dir(source)
            if scene.exists():
                self._validate_scene(scene,source)
                return {'sourceSha256':source,'targets':[{'root':ROOT,'path':'scenes/'+source,'kind':'directory'}]}
            metadata=self.store._path('sources',source+'.json')
            image=self.store._path('sources',source+'.png')
            raw=self.store._read(metadata if metadata.exists() else marker,8192)
            m=source_entry(json.loads(raw))
            if wire(m)!=raw or m['sha256']!=source:raise PinnedDeleteRejected('cinematic source alias changed')
            source_image(self.store._read(image,CINEMATIC_MAX),m)
            self._uncorrelated_sources(source,m['officialRequestId'])
            targets=self._remaining_children(source)
            if metadata.exists():targets.append({'root':ROOT,'path':'sources/'+metadata.name,'kind':'file'})
            targets.append({'root':ROOT,'path':'sources/'+image.name,'kind':'file'})
            return {'sourceSha256':source,'targets':targets}

    def _validate_scene(self,scene,source):
        raw=self.store._read(scene/'source.json',8192)
        m=source_entry(json.loads(raw))
        if wire(m)!=raw or m['sha256']!=source:raise PinnedDeleteRejected('cinematic scene source changed')
        source_image(self.store._read(scene/'source.png',CINEMATIC_MAX),m)
        self._uncorrelated_sources(source,m['officialRequestId'])
        if any(self.store._path('sources',source+'.'+ext).exists() for ext in ('png','json')) or self._remaining_children(source):
            raise PinnedDeleteRejected('cinematic scene migration incomplete')
        return raw

    def remove(self,command):
        from media_store import CINEMATIC_MAX,source_entry,source_image,wire
        if type(command)is not dict or set(command)!={'operationId','target','expected'}:raise ValueError('cinematic delete command')
        t=validate_target(command['target']);expected=command['expected']
        if t['root']!=ROOT or type(expected)is not dict or set(expected)!={'sourceSha256'}:
            raise ValueError('cinematic delete pin')
        source=expected['sourceSha256']
        if type(source)is not str or not re.fullmatch(DIGEST,source):raise ValueError('cinematic delete source')
        path=t['path']
        scene_target=t['kind']=='directory' and path=='scenes/'+source
        alias=SOURCE.fullmatch(path) if t['kind']=='file' else None
        seq=SEQUENCE.fullmatch(path) if t['kind']=='directory' else None
        result=RESULT.fullmatch(path) if t['kind']=='file' else None
        if not (scene_target or alias or seq or result) or alias and alias[1]!=source:raise ValueError('cinematic delete target')
        mover=SingleTargetDelete(self.store,ROOT)
        move={k:command[k] for k in ('operationId','target')}
        with self.store.lock():
            if scene_target:
                scene=self.store._scene_dir(source)
                if not scene.exists():return mover.remove_locked(move)
                raw=self._validate_scene(scene,source)
                marker=self._marker(source)
                if marker.exists() and self.store._read(marker,8192)!=raw:
                    raise PinnedDeleteRejected('cinematic retirement marker changed')
                if not marker.exists():self.store.write(marker,raw)
                return mover.remove_locked(move)
            if seq:
                leaf=self.store._seq_dir(seq[1])
            else:
                folder,name=path.split('/')
                leaf=self.store._path(folder,name)
            # True replay is verified by the common move record; absence alone
            # must never be reported as successfully deleted.
            try:leaf.lstat()
            except FileNotFoundError:return mover.remove_locked(move)
            if seq:
                _,m=self.store._playback_base(seq[1])
                if m['sourceSha256']!=source:raise PinnedDeleteRejected('cinematic sequence source changed')
            elif result:
                m=self._legacy(result[1])
                if m['sourceSha256']!=source:raise PinnedDeleteRejected('cinematic result source changed')
                if result[2]=='mp4':self.store._result(result[1])
                else:
                    video=self.store._path('results',result[1]+'.mp4')
                    if video.exists():raise PinnedDeleteRejected('delete legacy video before its sidecar')
            elif alias[2]=='json':
                raw=self.store._read(leaf,8192)
                m=source_entry(json.loads(raw))
                if wire(m)!=raw or m['sha256']!=source:raise PinnedDeleteRejected('cinematic source alias changed')
                png=self.store._path('sources',source+'.png')
                source_image(self.store._read(png,CINEMATIC_MAX),m)
                self._uncorrelated_sources(source,m['officialRequestId'])
                if self._remaining_children(source):raise PinnedDeleteRejected('scene still has cinematic children')
                marker=self._marker(source)
                if marker.exists():
                    if self.store._read(marker,8192)!=raw:raise PinnedDeleteRejected('cinematic retirement marker changed')
                else:self.store.write(marker,raw)
            else:
                raw=self.store._read(self._marker(source),8192)
                m=source_entry(json.loads(raw))
                if wire(m)!=raw or m['sha256']!=source:raise PinnedDeleteRejected('cinematic retirement marker mismatch')
                if self.store._path('sources',source+'.json').exists():
                    raise PinnedDeleteRejected('delete source sidecar before its image')
                source_image(self.store._read(leaf,CINEMATIC_MAX),m)
                self._uncorrelated_sources(source,m['officialRequestId'])
                if self._remaining_children(source):raise PinnedDeleteRejected('scene still has cinematic children')
            return mover.remove_locked(move)

UI_ROUTE='/v1/workflow/ui/assets'
UI_FIELDS={'schemaVersion','projectId','officialRequestId','kind','title','sha256','bytes','width','height','origin','designSha256'}


class UiAssetStore(MediaStore):
 namespace='canonical-files/binari/ui/'

 def ui_asset_result(self,p):
  require(type(p)is dict and set(p)=={'officialRequestId'} and cinematic_match(p['officialRequestId'],CINEMATIC_ID),'ui result id')
  oid=p['officialRequestId']
  with self.lock():
   m=json.loads(self.internal('assets',oid,'manifest.json').read_bytes())
   data=self.internal('assets',oid,'asset.png').read_bytes()
   self._validate(m,data,oid)
   return m

 @staticmethod
 def _validate(m,data,oid):
  require(type(m)is dict and set(m)==UI_FIELDS and m['schemaVersion']=='ngs.ui-asset-result.v1'
   and m['projectId']=='binari' and cinematic_match(oid,CINEMATIC_ID) and m['officialRequestId']==oid
   and m['kind'] in ('logo','button') and type(m['title'])is str and 0<len(m['title'])<=120
   and isinstance(m['bytes'],int) and not isinstance(m['bytes'],bool) and len(data)==m['bytes'] and len(data)<=CINEMATIC_MAX
   and sha(data)==m['sha256'] and m['origin'] in ('manual','imagegen')
   and (m['designSha256'] is None if m['origin']=='manual' else cinematic_match(m['designSha256'],CINEMATIC_DIGEST)),'ui manifest')
  png(data)
  require(data[24:26]==b'\x08\x06' and tuple(struct.unpack_from('>II',data,16))==(m['width'],m['height'])
   and all(type(m[k])is int and 0<m[k]<=4096 for k in ('width','height')),'ui png profile')

 def ui_asset_publish(self,p):
  import os, tempfile
  require(type(p)is dict and set(p)=={'manifest','base64'} and type(p['manifest'])is dict
   and type(p['base64'])is str and len(p['base64'])<=4*((CINEMATIC_MAX+2)//3),'ui artifact')
  m=p['manifest'];oid=m.get('officialRequestId');data=base64.b64decode(p['base64'],validate=True)
  self._validate(m,data,oid)
  dest=self.internal('assets',oid);assets=dest.parent
  with self.lock():
   if dest.exists():
    if self.internal('assets',oid,'manifest.json').read_bytes()!=wire(m) or self.internal('assets',oid,'asset.png').read_bytes()!=data:
     raise Conflict('ui asset already published with different bytes or metadata')
    return m
   assets.mkdir(exist_ok=True)
   # Move a complete directory as one publication; no visible incomplete item.
   with tempfile.TemporaryDirectory(prefix='.ui-stage-',dir=self.root) as tmp:
    from pathlib import Path
    stage=Path(tmp)/'publish'
    self.write(stage/'asset.png',data)
    self.write(stage/'manifest.json',wire(m))
    os.rename(stage,dest)
    for folder in (assets,self.root):
     fd=os.open(folder,os.O_RDONLY)
     try:os.fsync(fd)
     finally:os.close(fd)
  return m
