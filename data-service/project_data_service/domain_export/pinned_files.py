"""Experimental pinned file export. No HTTP route or production factory is enabled.
The host supplies retained revision files; clients only supply opaque resource IDs.
"""
import hashlib,json,os,re,stat,time
from pathlib import Path
from datetime import datetime,timezone
CHUNK=24576
MAX_RESOURCES=256
MAX_RESOURCE=33554432
MAX_TOTAL=134217728
PAGE_ITEMS=64
PAGE_BYTES=262144

def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def digest(v):return hashlib.sha256(canonical(v)).hexdigest()
def identity(s):return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
class ExportError(ValueError):pass

def bounded_open(root,path):
 root=Path(root).resolve();path=Path(path)
 try:relative=path.relative_to(root)
 except ValueError:raise ExportError('EXPORT_PATH_SCOPE')
 if not relative.parts or any(p in ('.','..')for p in relative.parts):raise ExportError('EXPORT_PATH_SCOPE')
 fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:
  for part in relative.parts[:-1]:
   nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=nxt
  out=os.open(relative.parts[-1],os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
 finally:os.close(fd)
 if not stat.S_ISREG(os.fstat(out).st_mode):os.close(out);raise ExportError('EXPORT_REGULAR_FILE_REQUIRED')
 return os.fdopen(out,'rb')

class PinnedFileExport:
 def __init__(self,project,revision,root,resources,authorize=None,freshness=None,*,enabled=False,ttl=900,clock=time.monotonic,captured_at=None):
  if enabled is not True:raise ExportError('EXPERIMENTAL_EXPORT_DISABLED')
  if not callable(authorize)or not callable(freshness):raise ExportError('EXPORT_GUARDS_REQUIRED')
  if type(ttl)is not int or not 1<=ttl<=900:raise ExportError('EXPORT_TTL')
  if not isinstance(project,str)or not re.fullmatch(r'[A-Za-z0-9._:-]{1,160}',project):raise ExportError('EXPORT_PROJECT')
  if not isinstance(revision,dict)or not revision or len(revision)>64:raise ExportError('EXPORT_REVISION')
  if not 1<=len(resources)<=MAX_RESOURCES:raise ExportError('RESOURCE_LIMIT')
  self.project,self.revision,self.root=project,dict(revision),Path(root).resolve();self.authorize,self.freshness=authorize,freshness;self.clock=clock;self.deadline=clock()+ttl;self.files={};rows=[];total=0
  self.guard()
  for item in sorted(resources,key=lambda x:x['logicalResourceId']):
   rid=item['logicalResourceId']
   if not re.fullmatch(r'[A-Za-z0-9._:-]{1,160}',rid)or rid in self.files:raise ExportError('EXPORT_RESOURCE_ID')
   if item.get('kind')not in ('document','asset'):raise ExportError('EXPORT_RESOURCE_KIND')
   if item.get('mime')not in ('application/json','text/plain','text/markdown','text/html','image/png','image/jpeg','image/webp','image/gif','image/svg+xml'):raise ExportError('EXPORT_RESOURCE_MIME')
   self.guard(rid)
   path=Path(item['path']);whole=hashlib.sha256();parts=[]
   with bounded_open(self.root,path)as f:
    before=identity(os.fstat(f.fileno()));size=before[2];total+=size
    if size>MAX_RESOURCE:raise ExportError('OBJECT_LIMIT_REQUIRES_CHUNK_CONTRACT')
    if total>MAX_TOTAL:raise ExportError('SNAPSHOT_TOTAL_LIMIT')
    while True:
     self.guard(rid)
     raw=f.read(CHUNK)
     if not raw:break
     whole.update(raw);parts.append({'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw)})
    if identity(os.fstat(f.fileno()))!=before:raise ExportError('EXPORT_SOURCE_CHANGED')
   row={k:item[k]for k in ('logicalResourceId','kind','mime')};row.update(size=size,sha256=whole.hexdigest())
   if 'assetId'in item:row['assetId']=item['assetId']
   if size>CHUNK:row['chunks']=parts
   self.files[rid]=(path,before);rows.append(row)
  self.manifest={'schemaVersion':'npc-mirror.v1','projectId':project,'snapshotId':'snapshot.'+digest({'projectId':project,'sourceRevisionVector':self.revision,'resources':rows}),'sourceRevision':digest(self.revision),'sourceRevisionVector':self.revision,'lastSyncedAt':captured_at or datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),'resources':rows}
  # Host retained-cohort identity is pinned; this experimental transfer timestamp
  # is stable across resume, never evidence of global atomic source capture.
  self.manifest['contentHash']=digest(self.manifest);self.session=digest(self.manifest);self.rows=rows;self.guard()
 def guard(self,resource=None):
  if self.clock()>self.deadline:raise ExportError('EXPORT_EXPIRED')
  self.authorize(self.project,resource);self.freshness(self.revision)
 def page(self,session,cursor=0,limit=PAGE_ITEMS):
  self.guard()
  if session!=self.session:raise ExportError('EXPORT_SESSION')
  if type(cursor)is not int or not 0<=cursor<=len(self.rows)or type(limit)is not int or not 1<=limit<=PAGE_ITEMS:raise ExportError('EXPORT_PAGE_INPUT')
  rows=[];used=0
  for row in self.rows[cursor:cursor+limit]:
   self.guard(row['logicalResourceId'])
   n=len(canonical(row))
   if used+n>PAGE_BYTES:break
   rows.append(row);used+=n
  if cursor<len(self.rows)and not rows:raise ExportError('EXPORT_DESCRIPTOR_PAGE_LIMIT')
  return {'protocol':'pds-file-export.v1','session':self.session,'projectId':self.project,'snapshotId':self.manifest['snapshotId'],'contentHash':self.manifest['contentHash'],'sourceRevision':self.manifest['sourceRevision'],'sourceRevisionVector':self.revision,'lastSyncedAt':self.manifest['lastSyncedAt'],'cursor':cursor,'nextCursor':cursor+len(rows)if cursor+len(rows)<len(self.rows)else None,'resourceCount':len(self.rows),'resources':rows}
 def chunk(self,session,rid,index):
  self.guard()
  if session!=self.session or rid not in self.files:raise ExportError('EXPORT_SESSION_RESOURCE')
  self.guard(rid)
  row=next(x for x in self.rows if x['logicalResourceId']==rid);parts=row.get('chunks',[{'sha256':row['sha256'],'size':row['size']}])
  if type(index)is not int or not 0<=index<len(parts):raise ExportError('EXPORT_CHUNK_INDEX')
  path,pin=self.files[rid]
  with bounded_open(self.root,path)as f:
   if identity(os.fstat(f.fileno()))!=pin:raise ExportError('EXPORT_SOURCE_CHANGED')
   f.seek(index*CHUNK);raw=f.read(parts[index]['size'])
   if identity(os.fstat(f.fileno()))!=pin:raise ExportError('EXPORT_SOURCE_CHANGED')
  if len(raw)!=parts[index]['size']or hashlib.sha256(raw).hexdigest()!=parts[index]['sha256']:raise ExportError('EXPORT_CHUNK_HASH')
  self.guard(rid)
  return {'session':session,'snapshotId':self.manifest['snapshotId'],'logicalResourceId':rid,'index':index,'sha256':parts[index]['sha256'],'bytes':raw}
