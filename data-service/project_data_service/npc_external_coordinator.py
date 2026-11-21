"""Trusted local adapter boundary; not a public request endpoint.
Uses its OWN ledger DB (never operational requests.sqlite). Every adapter admission,
producer registration, publication gate and cleanup must share this coordinator.
A backend PDS adapter supplies verified generation and deletion receipt over its
existing authenticated transport; callers pass IDs, never client receipt objects.
"""
import contextlib,fcntl,json,sqlite3,shutil
from pathlib import Path
from .npc_external_lifecycle import digest,require,safe,sha,plan,retire,atomic

class Coordinator:
 def __init__(self,ledger,roots,quarantine,pds):
  self.path=Path(ledger);self.path.parent.mkdir(parents=True,exist_ok=True)
  require(not self.path.is_symlink(),'LEDGER_SYMLINK')
  self.roots=roots;self.quarantine=quarantine;self.pds=pds
  with self.locked() as db:
   db.executescript('''CREATE TABLE IF NOT EXISTS npc_generation(npc TEXT PRIMARY KEY,generation TEXT NOT NULL,state TEXT NOT NULL,delete_request TEXT,owner TEXT,receipt TEXT);
CREATE TABLE IF NOT EXISTS leases(npc TEXT,generation TEXT,request TEXT,PRIMARY KEY(npc,generation,request));
CREATE TABLE IF NOT EXISTS owned(npc TEXT,generation TEXT,request TEXT,root TEXT,path TEXT,sha TEXT,kind TEXT,PRIMARY KEY(root,path));
CREATE TABLE IF NOT EXISTS completed_copy_cleanup(id TEXT PRIMARY KEY,npc TEXT,generation TEXT,request TEXT,root TEXT,path TEXT,sha TEXT,proof TEXT,state TEXT);''')
 @contextlib.contextmanager
 def locked(self):
  lock=self.path.with_suffix('.lock');require(not lock.is_symlink(),'LOCK_SYMLINK')
  with open(lock,'a+b') as f:
   fcntl.flock(f,fcntl.LOCK_EX)
   db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row
   try:
    db.execute('BEGIN IMMEDIATE');yield db;db.commit()
   except BaseException:db.rollback();raise
   finally:db.close();fcntl.flock(f,fcntl.LOCK_UN)
 def row(self,db,npc,generation):
  row=db.execute('SELECT * FROM npc_generation WHERE npc=?',(npc,)).fetchone()
  require(row is not None and row['generation']==generation,'GENERATION_MISMATCH');return row
 def activate(self,npc,generation):
  with self.locked() as db:
   authority=self.pds.get_generation(npc)
   require(authority=={'npcId':npc,'generationId':generation,'state':'active'},'PDS_GENERATION_MISMATCH')
   old=db.execute('SELECT * FROM npc_generation WHERE npc=?',(npc,)).fetchone()
   if old:
    require(old['generation']==generation and old['state']=='active','GENERATION_REACTIVATION_FORBIDDEN');return
   db.execute('INSERT INTO npc_generation VALUES(?,?,?,NULL,NULL,NULL)',(npc,generation,'active'))
 def admit(self,npc,generation,request):
  with self.locked() as db:
   require(self.row(db,npc,generation)['state']=='active','NPC_ADMISSION_FENCED')
   require(self.pds.get_generation(npc)=={'npcId':npc,'generationId':generation,'state':'active'},'PDS_GENERATION_MISMATCH')
   db.execute('INSERT OR IGNORE INTO leases VALUES(?,?,?)',(npc,generation,request))
 def register(self,npc,generation,request,root,path,kind):
  """Called by producer AFTER durable output, before releasing its lease."""
  from .npc_external_lifecycle import KINDS
  with self.locked() as db:
   require(self.row(db,npc,generation)['state']=='active','NPC_ADMISSION_FENCED')
   require(db.execute('SELECT 1 FROM leases WHERE npc=? AND generation=? AND request=?',(npc,generation,request)).fetchone(),'LEASE_REQUIRED')
   require(root in self.roots and kind in KINDS,'ROOT_OR_KIND_FORBIDDEN')
   file=safe(self.roots[root],path);require(file.is_file(),'FILE_REQUIRED');h=sha(file)
   old=db.execute('SELECT * FROM owned WHERE root=? AND path=?',(root,path)).fetchone()
   if old:
    require((old['npc'],old['generation'],old['request'],old['kind'])==(npc,generation,request,kind),'OWNER_OR_BYTES_CONFLICT')
    db.execute('UPDATE owned SET sha=? WHERE root=? AND path=?',(h,root,path))
   else:db.execute('INSERT INTO owned VALUES(?,?,?,?,?,?,?)',(npc,generation,request,root,path,h,kind))
   return h
 def finish(self,npc,generation,request):
  with self.locked() as db:
   self.row(db,npc,generation)
   db.execute('DELETE FROM leases WHERE npc=? AND generation=? AND request=?',(npc,generation,request))
 def publication_gate(self,npc,generation,request,publish):
  """publish must perform PDS generation CAS; lock spans call and result."""
  with self.locked() as db:
   require(self.row(db,npc,generation)['state']=='active','NPC_PUBLICATION_FENCED')
   require(db.execute('SELECT 1 FROM leases WHERE npc=? AND generation=? AND request=?',(npc,generation,request)).fetchone(),'LEASE_REQUIRED')
   require(self.pds.get_generation(npc)=={'npcId':npc,'generationId':generation,'state':'active'},'PDS_GENERATION_MISMATCH')
   return publish(generation)
 def prepare_delete(self,npc,generation,request,coverage_guard=None):
  """Persist admission fence + exact ledger inventory before PDS backup/delete."""
  with self.locked() as db:
   scope_evidence=coverage_guard(db) if coverage_guard is not None else None
   row=self.row(db,npc,generation)
   require(row['state'] in ('active','fenced') and row['delete_request'] in (None,request),'DELETE_CONFLICT')
   require(not db.execute('SELECT 1 FROM leases WHERE npc=?',(npc,)).fetchone(),'ACTIVE_LEASES')
   if row['owner']:return json.loads(row['owner'])
   files=[]
   for e in db.execute('SELECT * FROM owned WHERE npc=? AND generation=? ORDER BY root,path',(npc,generation)):
    files.append({'npcId':npc,'generationId':generation,'requestId':e['request'],'exclusive':True,'rootKey':e['root'],'path':e['path'],'sha256':e['sha'],'kind':e['kind']})
   owner={'schemaVersion':'npc-external-owner.v1','npcId':npc,'generationId':generation,'files':files}
   if scope_evidence is not None:
    require(scope_evidence.get('scope')=='current-generation' and scope_evidence.get('globalCoverageComplete')is False and scope_evidence.get('legacyPreserved')is True,'RETIREMENT_SCOPE_INVALID')
    owner['retirementScope']=scope_evidence
   # Whole deletion recovery includes external bytes BEFORE canonical deletion.
   # This is deletion-only backup, never a per-edit/versioning mechanism.
   backup_id=digest(owner);backup=Path(self.quarantine)/'predelete'/backup_id
   require(backup.resolve()==backup,'BACKUP_SYMLINK');backup.mkdir(parents=True,exist_ok=True)
   for i,e in enumerate(files):
    src=safe(self.roots[e['rootKey']],e['path']);require(src.is_file() and sha(src)==e['sha256'],'SOURCE_CHANGED')
    dst=backup/str(i);require(not dst.is_symlink(),'BACKUP_SYMLINK')
    if not dst.exists():
     with open(src,'rb') as source,open(dst,'xb') as target:
      shutil.copyfileobj(source,target);target.flush()
      import os
      os.fsync(target.fileno())
    require(dst.is_file() and sha(dst)==e['sha256'],'BACKUP_HASH_MISMATCH')
   manifest=backup/'manifest.json'
   if manifest.exists():require(json.loads(manifest.read_text())==owner,'BACKUP_MANIFEST_CHANGED')
   else:atomic(manifest,owner)
   owner['externalBackup']={'id':backup_id,'manifestHash':digest(owner)}
   db.execute('UPDATE npc_generation SET state=?,delete_request=?,owner=? WHERE npc=?',('fenced',request,json.dumps(owner,sort_keys=True),npc))
   return owner
 def cleanup_deleted(self,npc,generation,request):
  with self.locked() as db:
   row=self.row(db,npc,generation)
   require(row['state'] in ('fenced','retired') and row['delete_request']==request,'DELETE_NOT_PREPARED')
   require(not db.execute('SELECT 1 FROM leases WHERE npc=?',(npc,)).fetchone(),'ACTIVE_LEASES')
   owner=json.loads(row['owner'])
   recovery=owner['externalBackup'];backup=Path(self.quarantine)/'predelete'/recovery['id']
   require(backup.resolve()==backup,'BACKUP_SYMLINK')
   original=json.loads((backup/'manifest.json').read_text())
   require(digest(original)==recovery['manifestHash'] and recovery['id']==digest(original),'BACKUP_MANIFEST_CHANGED')
   for i,e in enumerate(original['files']):
    file=backup/str(i);require(not file.is_symlink() and file.is_file() and sha(file)==e['sha256'],'BACKUP_HASH_MISMATCH')
   # Authenticated PDS read by ID, NOT accepting any receipt from the client.
   receipt=self.pds.get_deletion_receipt(request)
   require(receipt.get('requestId')==request and receipt.get('generationId')==generation and receipt.get('deletedCharacter')==npc,'PDS_RECEIPT_TARGET')
   require(receipt.get('externalOwnerManifestHash')==digest(owner),'PDS_EXTERNAL_MANIFEST_BINDING')
   require(self.pds.get_generation(npc)=={'npcId':npc,'generationId':generation,'state':'deleted'},'PDS_NOT_DELETED')
   require(receipt.get('status')=='committed' and receipt.get('backupVerified') is True,'DELETE_BACKUP_UNVERIFIED')
   require(all(receipt.get(k)for k in ('backupId','backupManifestHash','inputHash','ownerManifestHash')),'RECEIPT_INCOMPLETE')
   # canonical ownerManifestHash remains in the saved PDS evidence; primitive's
   # ownerManifestHash field is bound to the separate external ledger inventory.
   bound={**receipt,'ownerManifestHash':receipt['externalOwnerManifestHash']}
   fence={'npcId':npc,'activeLeases':0,'deleted':True,'admissionBlocked':True}
   if row['receipt']:
    saved=json.loads(row['receipt']);require(saved['pdsReceipt']==receipt,'PDS_RECEIPT_CHANGED')
    retirement=saved['plan']
   else:
    retirement=plan(owner,bound,self.roots,fence=fence)
    saved={'pdsReceipt':receipt,'plan':retirement}
    # Persist plan BEFORE any moves. Keep flock; subsequent transaction retains
    # exclusion while allowing process-crash recovery from prepared state.
    db.execute('UPDATE npc_generation SET receipt=? WHERE npc=?',(json.dumps(saved,sort_keys=True),npc));db.commit();db.execute('BEGIN IMMEDIATE')
   result=retire(retirement,self.roots,self.quarantine,fence=fence)
   db.execute('UPDATE npc_generation SET state=? WHERE npc=?',('retired',npc))
   return result

 def cleanup_published_copy(self,npc,generation,request,root,path,completion_reader):
  """Trusted host call only, no RPC. Delete ONLY a verified current duplicate.
  Journals/unique intermediates remain. This creates metadata audit, not versions.
  Caller/producer shares this coordinator lock; the global PDS lease stays held.
  """
  import base64,hashlib,os
  with self.locked() as db:
   require(self.row(db,npc,generation)['state']=='active','NPC_CLEANUP_FENCED')
   require(db.execute('SELECT 1 FROM leases WHERE npc=? AND generation=? AND request=?',(npc,generation,request)).fetchone(),'LEASE_REQUIRED')
   terminal=completion_reader(npc,generation,request)
   require(isinstance(terminal,dict) and all(terminal.get(k)==v for k,v in (('npcId',npc),('generationId',generation),('requestId',request))) and terminal.get('state')=='completed' and terminal.get('resumeRequired') is False and terminal.get('nonrunning') is True and terminal.get('dependenciesComplete') is True and terminal.get('unpublishedInputs')==[],'COPY_CLEANUP_FINALIZATION_REQUIRED')
   require(root in self.roots,'ROOT_NOT_ALLOWED');file=safe(self.roots[root],path)
   identity=digest([npc,generation,request,root,path]);audit=db.execute('SELECT * FROM completed_copy_cleanup WHERE id=?',(identity,)).fetchone()
   item=db.execute('SELECT * FROM owned WHERE root=? AND path=?',(root,path)).fetchone()
   require(callable(getattr(self.pds,'reserve_equivalent',None)) and callable(getattr(self.pds,'release_equivalent',None)),'COPY_CLEANUP_RESERVATION_REQUIRED')
   if audit:
    saved_proof=json.loads(audit['proof'])
    require(saved_proof.get('reservationId')==identity and saved_proof.get('reservationToken'),'COPY_CLEANUP_LEGACY_AUDIT_REVIEW_REQUIRED')
   def release(proof):
    require(proof.get('reservationId')==identity and proof.get('reservationToken'),'COPY_CLEANUP_RESERVATION_REQUIRED')
    self.pds.release_equivalent(npc,generation,request,identity,proof['reservationToken'])
   if audit and audit['state'] in ('removed','released'):
    require(not file.exists() and item is None,'COPY_CLEANUP_PATH_REUSED')
    if audit['state']=='removed':
     release(json.loads(audit['proof']));db.execute('UPDATE completed_copy_cleanup SET state=? WHERE id=?',('released',identity))
    return {'removed':True,'auditId':identity,'duplicateOnly':True}
   require(item is not None and (item['npc'],item['generation'],item['request'])==(npc,generation,request),'COPY_CLEANUP_OWNER_MISMATCH')
   require(item['kind'] in ('animation-scratch','pose-scratch','frame-scratch','portrait-scratch'),'COPY_CLEANUP_KIND_FORBIDDEN')
   if audit and audit['state']=='prepared' and not file.exists():
    # Roll forward a durably approved unlink; latest canonical slot may already
    # have advanced, which must not re-create obsolete local version bytes.
    require(audit['sha']==item['sha'],'COPY_CLEANUP_AUDIT_CHANGED')
    db.execute('DELETE FROM owned WHERE root=? AND path=?',(root,path))
    db.execute('UPDATE completed_copy_cleanup SET state=? WHERE id=?',('removed',identity))
    db.commit();db.execute('BEGIN IMMEDIATE');release(json.loads(audit['proof']));db.execute('UPDATE completed_copy_cleanup SET state=? WHERE id=?',('released',identity))
    return {'removed':True,'auditId':identity,'duplicateOnly':True}
   # Before any new unlink, download the current equivalent, never old hashes.
   proof=self.pds.reserve_equivalent(npc,generation,request,item['sha'],identity)
   require(isinstance(proof,dict) and proof.get('npcId')==npc and proof.get('generationId')==generation and proof.get('sha256')==item['sha'] and proof.get('currentSlot') is True and proof.get('slotPath') and proof.get('publicationReceiptId'),'COPY_CLEANUP_NO_EQUIVALENT')
   require(proof.get('reservationId')==identity and proof.get('reservationToken'),'COPY_CLEANUP_RESERVATION_REQUIRED')
   data=base64.b64decode(proof['base64'],validate=True)
   require(hashlib.sha256(data).hexdigest()==item['sha'],'COPY_CLEANUP_EQUIVALENT_HASH')
   evidence={k:v for k,v in proof.items()if k!='base64'}
   if file.exists():require(file.is_file() and sha(file)==item['sha'],'COPY_CLEANUP_BYTES_CHANGED')
   else:require(audit is not None and audit['state']=='prepared' and audit['sha']==item['sha'],'COPY_CLEANUP_SOURCE_MISSING')
   if not audit:
    db.execute('INSERT INTO completed_copy_cleanup VALUES(?,?,?,?,?,?,?,?,?)',(identity,npc,generation,request,root,path,item['sha'],json.dumps(evidence,sort_keys=True),'prepared'))
    db.commit();db.execute('BEGIN IMMEDIATE')
   if file.exists():
    require(not file.is_symlink() and sha(file)==item['sha'],'COPY_CLEANUP_BYTES_CHANGED')
    file.unlink()
    fd=os.open(file.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
   db.execute('DELETE FROM owned WHERE root=? AND path=?',(root,path))
   db.execute('UPDATE completed_copy_cleanup SET state=? WHERE id=?',('removed',identity))
   db.commit();db.execute('BEGIN IMMEDIATE');release(evidence);db.execute('UPDATE completed_copy_cleanup SET state=? WHERE id=?',('released',identity))
   return {'removed':True,'auditId':identity,'duplicateOnly':True}
 def cleanup_completed_copies(self,npc,generation,request,completion_reader):
  with self.locked() as db:
   self.row(db,npc,generation)
   items=[dict(r)for r in db.execute("SELECT root,path FROM owned WHERE npc=? AND generation=? AND request=? UNION SELECT root,path FROM completed_copy_cleanup WHERE npc=? AND generation=? AND request=? AND state='removed'",(npc,generation,request,npc,generation,request))]
  removed=[];retained=[]
  for item in items:
   try:
    result=self.cleanup_published_copy(npc,generation,request,item['root'],item['path'],completion_reader)
    removed.append({**item,**result})
   except ValueError as error:
    if str(error) not in ('COPY_CLEANUP_NO_EQUIVALENT','COPY_CLEANUP_KIND_FORBIDDEN'):raise
    retained.append({**item,'reason':str(error)})
  return {'complete':True,'removed':removed,'retained':retained,'createsVersionBackups':False}
