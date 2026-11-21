"""Explicit one-time current NPC migration; default is read-only plan.
Stop NPC-producing consumers/PDS writes before --apply; existing DB remains intact.
Never supplies or creates credentials, permissions, container mounts or networking.
"""
import argparse,json,sqlite3,sys,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from project_data_service.filex_npc_authority import migration_files,FilexCurrentNPC
from project_data_service.filex_npc_store import FilexNPCStore,NPCTransport
from project_data_service.filex_read import digest,private_read

def source(path):
 c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
 try:
  row=c.execute('SELECT revision,saved_at,note,document FROM revisions ORDER BY revision DESC LIMIT 1').fetchone()
  if not row:raise ValueError('NPC_SOURCE_EMPTY')
  return dict(revision=row[0],savedAt=row[1],note=row[2],document=json.loads(row[3]))
 finally:c.close()

def main():
 p=argparse.ArgumentParser();p.add_argument('--source-db',type=Path,required=True);p.add_argument('--config',type=Path);p.add_argument('--apply',action='store_true');p.add_argument('--source-frozen',action='store_true');a=p.parse_args()
 before=source(a.source_db);files=migration_files(before)
 portraits=[x for n in before['document']['characters'] for x in n.get('portraits',[])]
 summary={'sourceRevision':before['revision'],'documentHash':digest(before['document']),'npcCount':len(before['document']['characters']),'fileCount':len(files),'portraitCount':len(portraits),'applied':False}
 if not a.apply:print(json.dumps(summary));return
 if not a.config or not a.source_frozen:raise ValueError('EXPLICIT_CONFIG_AND_FROZEN_SOURCE_REQUIRED')
 # Do not silently leave referenced bytes in the old authoritative location.
 if portraits:raise ValueError('NPC_PORTRAIT_MIGRATION_REQUIRED')
 config=json.loads(private_read(a.config));store=FilexNPCStore(lambda:NPCTransport(config));current=store.snapshot()
 if current['documents']:
  if current['documents']!=files:raise ValueError('NPC_TARGET_NOT_EMPTY')
 else:
  if source(a.source_db)!=before:raise ValueError('NPC_SOURCE_CHANGED')
  key='npc.migrate.'+digest(before)
  store.apply(key,digest(before),before={},after=files,expected_hashes={k:None for k in files})
 after=FilexCurrentNPC(store).current()
 if after['document']!=before['document'] or after['revision']!=before['revision']:raise ValueError('NPC_MIGRATION_VERIFICATION_FAILED')
 if source(a.source_db)!=before:raise ValueError('NPC_SOURCE_CHANGED_AFTER_MIGRATION_DO_NOT_ACTIVATE')
 marker=a.source_db.parent/'npc-filex-authority.json'
 if marker.is_symlink():raise ValueError('NPC_CUTOVER_MARKER_SYMLINK')
 marker_value={'authority':'filex','schemaVersion':'npc-filex-cutover.v1','storageRoot':str(a.source_db.parent.parent.resolve())}
 if marker.exists():
  if json.loads(marker.read_text())!=marker_value:raise ValueError('NPC_CUTOVER_MARKER_CONFLICT')
 else:
  fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
  with os.fdopen(fd,'w') as f:json.dump(marker_value,f);f.flush();os.fsync(f.fileno())
  fd=os.open(marker.parent,os.O_RDONLY)
  try:os.fsync(fd)
  finally:os.close(fd)
 print(json.dumps({**summary,'applied':True,'verified':True,'sourceVersion':after['sourceVersion']}))
if __name__=='__main__':main()
