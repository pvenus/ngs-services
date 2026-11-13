"""PDS-owned NPC/story/knowledge/portrait storage. No model or worker execution."""
import json,sqlite3,threading,hashlib,base64
from pathlib import Path
from types import SimpleNamespace
from .domain_contracts import legacy_http as native
from .domain_contracts.knowledge_catalog import KnowledgeStore,KnowledgeError
from .database import DataError

def digest(v):return hashlib.sha256(json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
class VersionStore(native.StoryStore):
 def __init__(self,path,lock):
  self.lock=lock;self.connection=sqlite3.connect(path,check_same_thread=False);self.connection.execute('CREATE TABLE IF NOT EXISTS pds_write_receipts(key TEXT PRIMARY KEY,request_hash TEXT,response TEXT)');self.connection.commit()
 def save(self,expected,note,document):
  key=digest([expected,note,document])
  with self.lock,self.connection:
   r=self.connection.execute('SELECT response FROM pds_write_receipts WHERE key=?',(key,)).fetchone()
   if r:return json.loads(r[0])
   latest=self.current()['revision']
   if expected!=latest:return None
   now=native.datetime.now(native.timezone.utc).isoformat();e={'revision':latest+1,'savedAt':now,'note':note,'document':document}
   self.connection.execute('INSERT INTO revisions VALUES(?,?,?,?)',(latest+1,now,note,json.dumps(document,ensure_ascii=False)));self.connection.execute('INSERT INTO pds_write_receipts VALUES(?,?,?)',(key,key,json.dumps(e,ensure_ascii=False)));return e
class OwnedKnowledge(KnowledgeStore):
 def __init__(self,path,lock):
  self.lock=lock;self.connection=sqlite3.connect(path,check_same_thread=False);self.connection.row_factory=sqlite3.Row;self.connection.execute('PRAGMA foreign_keys=ON');self.connection.execute('CREATE TABLE IF NOT EXISTS pds_write_receipts(key TEXT PRIMARY KEY,request_hash TEXT,response TEXT)');self.connection.commit()
 def _write(self,key,fn):
  with self.lock,self.connection:
   r=self.connection.execute('SELECT response FROM pds_write_receipts WHERE key=?',(key,)).fetchone()
   if r:return json.loads(r[0])
   value=fn()
   if value is not None:self.connection.execute('INSERT INTO pds_write_receipts VALUES(?,?,?)',(key,key,json.dumps(value,ensure_ascii=False)))
   return value
 def create_entry(self,value,note):return self._write(digest(['create',value,note]),lambda:super(OwnedKnowledge,self).create_entry(value,note))
 def update_entry(self,id,expected,note,changes):return self._write(digest(['update',id,expected,note,changes]),lambda:super(OwnedKnowledge,self).update_entry(id,expected,note,changes))
class DomainModule:
 def __init__(self,storage,concept):
  self.root=Path(storage)/'domains';self.concept=concept;self.lock=threading.RLock();self.enabled=all((self.root/(n+'.sqlite3')).is_file() for n in ('npc','story','knowledge'))
  if not self.enabled:return
  native.ROOT=self.root;native.SEED=self.root/'seed.json';native.STORY_SEED=self.root/'story_seed.json';native.NPC_SEED=self.root/'npc_seed.json';native.PORTRAIT_ROOT=self.root/'assets/portraits';native.LOCK=self.lock
  self.npcs=VersionStore(self.root/'npc.sqlite3',self.lock);self.story=VersionStore(self.root/'story.sqlite3',self.lock);self.knowledge=OwnedKnowledge(self.root/'knowledge.sqlite3',self.lock)
 def close(self):
  if self.enabled:
   for x in (self.npcs,self.story,self.knowledge):x.connection.close()
 def server(self,real):return SimpleNamespace(server_port=real.server_port,npc_store=self.npcs,story_store=self.story,knowledge_store=self.knowledge)
 def dispatch(self,handler):
  if not self.enabled:return handler.respond(503,{'error':'domains_not_initialized'})
  with self.lock:
   saved=handler.server;handler.server=self.server(saved)
   try:return native.Handler.route(handler)
   finally:handler.server=saved
 def category(self,p):
  if not self.enabled:raise DataError('domains not initialized')
  project=p['projectId'];op=p['operationHash'];meta=p['metadata'];document=p['document'];expected=p['expectedRevision'];note=p['note']
  if project!='ProjectBS' or document.get('projectId')!=project or type(expected) is not int or not isinstance(op,str):raise DataError('invalid NPC category binding')
  native.validate_npcs(document)
  with self.lock,self.npcs.connection:
   c=self.npcs.connection;r=c.execute('SELECT receipt FROM npc_category_apply_receipts WHERE project_id=? AND operation_hash=?',(project,op)).fetchone()
   if r:
    receipt=json.loads(r[0])
    if receipt.get('inputHash')!=meta.get('inputHash'):raise DataError('category operation conflict')
    return receipt
   if self.npcs.current()['revision']!=expected:raise DataError('CATEGORY_REVISION_CONFLICT')
   now=native.datetime.now(native.timezone.utc).isoformat();receipt={**meta,'revision':expected+1,'savedAt':now,'operationHash':op,'documentHash':digest(document),'commitId':'npc-category.'+op}
   c.execute('INSERT INTO revisions VALUES(?,?,?,?)',(expected+1,now,note,json.dumps(document,ensure_ascii=False)));c.execute('INSERT INTO npc_category_apply_receipts VALUES(?,?,?)',(project,op,json.dumps(receipt,ensure_ascii=False)));return receipt
 def category_receipt(self,key):
  with self.lock:
   r=self.npcs.connection.execute("SELECT receipt FROM npc_category_apply_receipts WHERE project_id='ProjectBS' AND operation_hash=?",(key,)).fetchone();return json.loads(r[0]) if r else None

 def capture(self,all_history=False):
  from .domain_export.binari_current import BinariCurrentSource
  from .domain_export.source import LegacySource
  # Lock order is domain -> concept; concept mutations never acquire domain lock.
  with self.lock,self.concept.store.lock:
   reader=LegacySource(self.root) if all_history else BinariCurrentSource(self.root)
   reader.concept_export=self.concept.export
   def api(method,path,payload):
    if path=='/api/revisions':return {'revisions':[{'revision':r['revision'],'savedAt':r['saved_at'],'note':r['note']} for r in self.concept.store.connection.execute("SELECT revision,saved_at,note FROM concept_revisions WHERE project_id='ProjectBS' ORDER BY revision DESC")]}
    return self.concept.revision(int(path.rsplit('/',1)[-1]))
   reader.concept_api=api
   value=reader.capture() if all_history else reader.discover()
   return {**value,'resources':{k:{**{a:b for a,b in v.items() if a!='bytes'},'base64':base64.b64encode(v['bytes']).decode()} for k,v in value['resources'].items()}}
