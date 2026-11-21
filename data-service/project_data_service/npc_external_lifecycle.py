"""Recoverable, explicitly owned external scratch retirement. No purge API.
Caller must hold the NPC admission fence and global storage lock for entire call.
Shared caches, presets, databases and backups are never eligible roots.
"""
import hashlib,json,os,re
from pathlib import Path

KINDS={'animation-scratch','pose-scratch','frame-scratch','portrait-scratch','npc-replica'}
def digest(value):
 return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def sha(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def require(v,message):
 if not v:raise ValueError(message)
def safe(root,rel):
 root=Path(root);p=Path(rel)
 require(root.is_absolute() and not root.is_symlink(),'ROOT_INVALID')
 require(not p.is_absolute() and p.parts and all(s not in ('..','.') for s in p.parts),'PATH_INVALID')
 require(root.resolve()==root,'ROOT_SYMLINK')
 q=root
 for part in p.parts:
  q=q/part;require(not q.is_symlink(),'SYMLINK_FORBIDDEN')
 require(root in q.parents,'PATH_ESCAPE')
 return q

def plan(owner,receipt,roots,*,fence):
 """owner is a trusted producer ledger, not a user-submitted file list."""
 npc=owner.get('npcId')
 require(owner.get('schemaVersion')=='npc-external-owner.v1' and bool(npc),'OWNER_SCHEMA')
 require(receipt.get('status')=='committed' and receipt.get('backupVerified') is True,'DELETE_BACKUP_UNVERIFIED')
 require(receipt.get('deletedCharacter')==npc and receipt.get('ownerManifestHash')==digest(owner),'OWNER_RECEIPT_BINDING')
 require(all(receipt.get(k) for k in ('requestId','inputHash','backupId','backupManifestHash')),'RECEIPT_INCOMPLETE')
 require(fence.get('npcId')==npc and fence.get('deleted') is True and fence.get('activeLeases')==0 and fence.get('admissionBlocked') is True,'NPC_NOT_FENCED')
 files=[];seen=set()
 for e in owner.get('files',[]):
  require(e.get('npcId')==npc and e.get('exclusive') is True and e.get('kind') in KINDS,'NOT_EXCLUSIVELY_OWNED')
  key=e.get('rootKey');require(key in roots,'ROOT_NOT_ALLOWED')
  p=safe(roots[key],e['path']);require(str(p) not in seen,'DUPLICATE_PATH');seen.add(str(p))
  require(p.is_file() and sha(p)==e['sha256'],'SOURCE_CHANGED')
  files.append({'rootKey':key,'path':e['path'],'sha256':e['sha256'],'kind':e['kind']})
 return {'schemaVersion':'npc-external-retirement.v1','npcId':npc,'receipt':{k:receipt[k] for k in ('requestId','inputHash','backupId','backupManifestHash','ownerManifestHash')},'files':files}

def atomic(path,value):
 raw=json.dumps(value,sort_keys=True).encode();temp=path.with_suffix('.tmp')
 with open(temp,'xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 os.replace(temp,path)
 fd=os.open(path.parent,os.O_RDONLY)
 try:os.fsync(fd)
 finally:os.close(fd)

def move(src,dst):
 os.rename(src,dst)
 for directory in {src.parent,dst.parent}:
  fd=os.open(directory,os.O_RDONLY)
  try:os.fsync(fd)
  finally:os.close(fd)

def retire(plan,roots,quarantine,*,fence):
 require(fence.get('npcId')==plan['npcId'] and fence.get('activeLeases')==0 and fence.get('deleted') is True and fence.get('admissionBlocked') is True,'NPC_NOT_FENCED')
 # Quarantine must be a separate persistent recovery volume, not inside a source.
 q=Path(quarantine);require(q.is_absolute() and q.resolve()==q,'QUARANTINE_INVALID')
 require(all(q!=Path(r) and Path(r) not in q.parents and q not in Path(r).parents for r in roots.values()),'QUARANTINE_OVERLAP')
 q.mkdir(parents=True,exist_ok=True)
 run=q/digest(plan);run.mkdir(exist_ok=True);manifest=run/'manifest.json'
 if manifest.exists():require(json.loads(manifest.read_text())['plan']==plan,'PLAN_CHANGED')
 else:atomic(manifest,{'plan':plan,'state':'prepared'})
 # Preflight all paths before moving any; a retried interrupted move is accepted
 # only when the exact bytes are already in quarantine and absent at source.
 pairs=[]
 for i,e in enumerate(plan['files']):
  require(e['rootKey'] in roots,'ROOT_NOT_ALLOWED');src=safe(roots[e['rootKey']],e['path']);dst=run/str(i)
  require(not dst.is_symlink(),'SYMLINK_FORBIDDEN')
  if dst.exists():require(not src.exists() and dst.is_file() and sha(dst)==e['sha256'],'QUARANTINE_CONFLICT')
  else:require(src.is_file() and sha(src)==e['sha256'] and src.stat().st_dev==run.stat().st_dev,'SOURCE_CHANGED_OR_CROSS_DEVICE')
  pairs.append((src,dst))
 for src,dst in pairs:
  if not dst.exists():move(src,dst)
 atomic(manifest,{'plan':plan,'state':'retired'})
 return {'quarantineId':run.name,'manifestHash':sha(manifest),'files':len(pairs),'permanentlyDeleted':False}

def restore(quarantine,quarantine_id,roots):
 require(re.fullmatch('[0-9a-f]{64}',quarantine_id) is not None,'ID_INVALID')
 run=safe(Path(quarantine),quarantine_id);m=json.loads((run/'manifest.json').read_text());p=m['plan']
 require(digest(p)==quarantine_id,'MANIFEST_CHANGED')
 pairs=[]
 for i,e in enumerate(p['files']):
  dst=safe(roots[e['rootKey']],e['path']);src=run/str(i)
  require(not src.is_symlink(),'SYMLINK_FORBIDDEN')
  if dst.exists():require(not src.exists() and dst.is_file() and sha(dst)==e['sha256'],'RESTORE_CONFLICT')
  else:require(src.is_file() and sha(src)==e['sha256'],'RESTORE_CONFLICT')
  pairs.append((src,dst))
 for src,dst in pairs:
  if not dst.exists():dst.parent.mkdir(parents=True,exist_ok=True);move(src,dst)
 atomic(run/'manifest.json',{'plan':p,'state':'restored'})
 # Restoring scratch must not reactivate NPC/queue entries; separate official restore.
 return {'restoredFiles':len(pairs),'reactivated':False}
