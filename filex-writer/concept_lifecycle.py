"""Recoverable concept subtree removal under the existing writer lock.
Only PDS-compiled scoped requests are accepted; no arbitrary path deletion API.
"""
import hashlib,json,re,os
from pathlib import Path

def encoded(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def digest(v):return hashlib.sha256(encoded(v)).hexdigest()
def require(v,reason):
 if not v:raise ValueError(reason)

class ConceptLifecycle:
 def lifecycle_internal(self,relative):
  require(isinstance(relative,str) and all(x not in ('','..','.')for x in relative.split('/')),'concept lifecycle internal path')
  p=self.root
  for part in relative.split('/'):
   p=p/part
   require(not p.is_symlink(),'concept lifecycle symlink')
  return p
 def scope_paths(self,documents,scope):
  require(isinstance(scope,dict) and set(scope)=={'categoryId','itemId'},'concept removal scope')
  cid=scope['categoryId'];iid=scope['itemId']
  require(isinstance(cid,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',cid),'concept category id')
  if iid is not None:require(isinstance(iid,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',iid),'concept item id')
  prefix='sections/'+cid+'/'
  paths=[prefix+'fields/'+iid+'.json']if iid else sorted(p for p in documents if p.startswith(prefix))
  require(paths and all(p in documents for p in paths),'concept removal target missing')
  return paths
 def removed_scope(self,backup_id):
  require(isinstance(backup_id,str) and re.fullmatch('[a-f0-9]{64}',backup_id),'concept backup id')
  base='.removed/'+backup_id;manifest=json.loads(self.lifecycle_internal(base+'/manifest.json').read_bytes())
  require(manifest.get('schemaVersion')=='concept-removed-scope.v1' and digest(manifest['binding'])==manifest['bindingHash'],'concept backup binding')
  documents={}
  for p,h in manifest['hashes'].items():
   self.path(self.namespace+p);raw=self.lifecycle_internal(base+'/files/'+p).read_bytes()
   require(hashlib.sha256(raw).hexdigest()==h,'concept backup integrity');documents[p]=json.loads(raw)
  require(set(self.scope_paths(documents,manifest['binding']['scope']))==set(documents),'concept backup scope')
  return {'backupId':backup_id,'manifest':manifest,'documents':documents}
 def recover_lifecycle(self):
  jp=self.lifecycle_internal('.concept-lifecycle.json')
  if not jp.exists():return
  tx=json.loads(jp.read_bytes());backup=self.removed_scope(tx['backupId'])
  for item in tx['writes']:
   p=self.path(self.namespace+item['path']);actual=digest(json.loads(p.read_bytes()))if p.exists()else None
   if actual==item['afterHash']:continue
   require(actual==item['expectedHash'],'concept lifecycle recovery conflict')
   require(digest(item['body'])==item['afterHash'],'concept lifecycle write corruption');self.write(p,encoded(item['body']))
  for rel,h in tx['removes'].items():
   p=self.path(self.namespace+rel)
   if not p.exists():continue
   require(hashlib.sha256(p.read_bytes()).hexdigest()==h and backup['manifest']['hashes'].get(rel)==h,'concept lifecycle removal changed')
   p.unlink()
   fd=os.open(p.parent,os.O_RDONLY)
   try:os.fsync(fd)
   finally:os.close(fd)
   # Empty directories only, never recursive deletion.
   parent=p.parent
   while parent!=self.root:
    try:parent.rmdir()
    except OSError:break
    parent=parent.parent
    fd=os.open(parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
  receipt=tx['receipt'];receipt['status']='committed';self.write(self.receipt_path(tx['requestId']),encoded(receipt));jp.unlink()
  fd=os.open(self.root,os.O_RDONLY)
  try:os.fsync(fd)
  finally:os.close(fd)
 def lifecycle_commit(self,request_id,input_hash,scope,expected_source,files,read_set,*,restore_info=None,backup_id=None):
  require(bool(request_id)and bool(input_hash)and isinstance(files,list)and files and isinstance(read_set,dict),'concept lifecycle contract')
  payload_hash=digest({'scope':scope,'expectedSourceVersion':expected_source,'files':files,'readSet':read_set,'restoreInfo':restore_info,'backupId':backup_id})
  with self.lock():
   if self.modern_concept_authority():raise ValueError('concept markdown workflow required')
   rp=self.receipt_path(request_id)
   if rp.exists():
    old=json.loads(rp.read_bytes());require(old['inputHash']==input_hash and old['payloadHash']==payload_hash,'concept lifecycle request reused');return old
   snap=self.snapshot_unlocked();require(snap['sourceVersion']==expected_source,'concept lifecycle stale source')
   before=snap['documents'];restore=backup_id is not None
   if restore:
    backup=self.removed_scope(backup_id);require(backup['manifest']['binding']['scope']==scope,'concept restore scope mismatch')
    removed={};restored=backup['documents'];require(not any(p in before for p in restored),'concept restore target occupied')
   else:
    targets=self.scope_paths(before,scope)
    if scope['itemId']is None:
     folder=self.root/'sections'/scope['categoryId'];entries=list(folder.rglob('*'))
     require(not any(p.is_symlink()for p in entries),'concept removal subtree symlink')
     require({p.relative_to(self.root).as_posix()for p in entries if p.is_file()}==set(targets),'concept removal undeclared files')
    backup_id=hashlib.sha256(request_id.encode()).hexdigest();removed={p:hashlib.sha256(self.path(self.namespace+p).read_bytes()).hexdigest()for p in targets};restored={}
    binding={'requestId':request_id,'inputHash':input_hash,'sourceVersion':expected_source,'scope':scope,'restoreInfo':restore_info}
    manifest={'schemaVersion':'concept-removed-scope.v1','binding':binding,'bindingHash':digest(binding),'hashes':removed}
    base='.removed/'+backup_id
    if self.lifecycle_internal(base+'/manifest.json').exists():
     require(self.removed_scope(backup_id)['manifest']==manifest,'concept prior backup changed')
    else:
     for p in targets:self.write(self.lifecycle_internal(base+'/files/'+p),self.path(self.namespace+p).read_bytes())
     self.write(self.lifecycle_internal(base+'/manifest.json'),encoded(manifest));self.removed_scope(backup_id)
   writes=[];seen=set()
   require(read_set=={self.namespace+p:h for p,h in snap['hashes'].items()}|{self.namespace+p:None for p in restored},'concept lifecycle full read set')
   for item in files:
    require(set(item)=={'path','body','expectedHash'},'concept lifecycle write shape');self.path(item['path']);rel=item['path'].removeprefix(self.namespace)
    require(rel not in seen and rel not in removed and item['path']in read_set and item['expectedHash']==read_set[item['path']]and isinstance(item['body'],dict),'concept lifecycle write basis')
    seen.add(rel);writes.append({'path':rel,'body':item['body'],'expectedHash':item['expectedHash'],'afterHash':digest(item['body'])})
   require('metadata.json'in seen and set(restored)<=seen,'concept lifecycle index required')
   # Restored original bytes may change only ordering priority, never content.
   for rel,body in restored.items():
    new=next(x['body']for x in writes if x['path']==rel)
    require({k:v for k,v in body.items()if k!='priority'}=={k:v for k,v in new.items()if k!='priority'},'concept restore content changed')
   hashes={self.namespace+x['path']:x['afterHash']for x in writes}
   receipt={'requestId':request_id,'inputHash':input_hash,'payloadHash':payload_hash,'status':'prepared','backupId':backup_id,'backupVerified':True,'scope':scope,'mode':'restore'if restore else'remove','hashes':hashes,'removedPaths':list(removed),'recoverable':True}
   tx={'requestId':request_id,'backupId':backup_id,'writes':writes,'removes':removed,'receipt':receipt}
   self.write(self.lifecycle_internal('.concept-lifecycle.json'),encoded(tx));self.recover_lifecycle();return json.loads(rp.read_bytes())
