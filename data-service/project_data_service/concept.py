"""PDS-owned concept history; independent of project/display revisions."""
import hashlib,json,re
from pathlib import Path
from .database import DataError,RevisionConflict,utc_now
from .concept_commit_preview import envelope,check_writer_expectation,expectation
from .pds_publication_capability import publication_capability
from .concept_validation.concept_content_v2 import validate as validate_v2
from .concept_validation.legacy import validate_legacy
ROOT=Path(__file__).parent/'concept_validation'
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(v):return hashlib.sha256(canonical(v).encode()).hexdigest()
def validate(v):return validate_v2(v,json.loads((ROOT/'seed.json').read_text()),validate_legacy)
class ConceptModule:
 def __init__(self,store):
  self.store=store
  with store.lock,store.connection:
   store.connection.execute('CREATE TABLE IF NOT EXISTS concept_revisions(project_id TEXT NOT NULL,revision INTEGER NOT NULL,saved_at TEXT NOT NULL,note TEXT NOT NULL,document TEXT NOT NULL,PRIMARY KEY(project_id,revision))')
   store.connection.execute('CREATE TABLE IF NOT EXISTS concept_receipts(operation_key TEXT PRIMARY KEY,request_hash TEXT NOT NULL,response_json TEXT NOT NULL,created_at TEXT NOT NULL)')
   store.connection.execute("CREATE TRIGGER IF NOT EXISTS concept_history_no_update BEFORE UPDATE ON concept_revisions BEGIN SELECT RAISE(ABORT,'concept history immutable'); END")
   store.connection.execute("CREATE TRIGGER IF NOT EXISTS concept_history_no_delete BEFORE DELETE ON concept_revisions BEGIN SELECT RAISE(ABORT,'concept history immutable'); END")
 def publication_capability(self):return publication_capability()
 def publication_preview(self,p):
  if not isinstance(p,dict) or set(p)!={'expectedRevision','note','document'}:raise DataError('invalid publication preview fields')
  document=validate(p['document'])
  proof=expectation(p['expectedRevision'],p['note'],document)
  with self.store.lock:
   current=self.current()
   if current is None or current['revision']!=p['expectedRevision']:raise RevisionConflict('concept revision conflict')
   return {'schemaVersion':'pds-concept-publication-preview.v1','capability':publication_capability(),
           'sourceProof':{'revision':current['revision'],'documentHash':digest(current['document'])},
           'validatedDocumentHash':digest(document),'noteHash':digest(p['note'].strip()),
           'writerExpectation':proof}
 def _envelope(self,r):return None if r is None else {'revision':r['revision'],'savedAt':r['saved_at'],'note':r['note'],'document':json.loads(r['document'])}
 def current(self):
  with self.store.lock:return self._envelope(self.store.connection.execute("SELECT * FROM concept_revisions WHERE project_id='ProjectBS' ORDER BY revision DESC LIMIT 1").fetchone())
 def revision(self,n):
  with self.store.lock:return self._envelope(self.store.connection.execute("SELECT * FROM concept_revisions WHERE project_id='ProjectBS' AND revision=?",(n,)).fetchone())
 def history(self):
  with self.store.lock:return [{'revision':r['revision'],'savedAt':r['saved_at'],'note':r['note']} for r in self.store.connection.execute("SELECT revision,saved_at,note FROM concept_revisions WHERE project_id='ProjectBS' ORDER BY revision DESC LIMIT 100")]
 def export(self):
  # One lock/read transaction creates an internally consistent WAL export.
  with self.store.lock:
   c=self.store.connection;c.execute('BEGIN')
   try:
    e=self.current();schema=(ROOT/'schema.json').read_bytes()
    if e is None:raise DataError('concept not initialized')
    body=canonical(e).encode();return {'envelope':e,'schema':schema.decode(),'sourceRevisionVector':{'concept':e['revision'],'concept.document':hashlib.sha256(body).hexdigest(),'concept.schema':hashlib.sha256(schema).hexdigest()}}
   finally:c.rollback()
 def import_history(self,rows):
  if not rows or [r[0] for r in rows]!=list(range(1,len(rows)+1)):raise DataError('non-contiguous history')
  # Historical bodies predate current validation; preserve verbatim, never upgrade.
  for r in rows:
   d=json.loads(r[3])
   if not isinstance(d,dict) or d.get("projectId")!="ProjectBS":raise DataError("invalid historical project")
  validate(json.loads(rows[-1][3]))
  with self.store.lock,self.store.connection:
   c=self.store.connection;old=c.execute("SELECT revision,saved_at,note,document FROM concept_revisions WHERE project_id='ProjectBS' ORDER BY revision").fetchall()
   if old:
    if [tuple(x) for x in old]!=[tuple(x) for x in rows]:raise DataError('history import differs; never overwrite')
    return False
   c.executemany('INSERT INTO concept_revisions VALUES(?,?,?,?,?)',[('ProjectBS',*r) for r in rows]);return True
 def save(self,p):
  if set(p)-{'expectedRevision','note','document','requestId','actor','operationHash','publicationExpectation'}:raise DataError('unknown concept fields')
  expected=p['expectedRevision'];note=p['note'];document=validate(p['document'])
  if type(expected) is not int or expected<1 or not isinstance(note,str) or not 3<=len(note.strip())<=300:raise DataError('invalid concept revision/reason')
  note=note.strip();request_hash=digest({'expectedRevision':expected,'note':note,'document':document})
  marker=re.search(r'\[(?:concept-proposal|mirror):([^\]]+)\]',note)
  # Existing client markers and complete payload digest cover response-loss replay.
  key=p.get('operationHash') or p.get('requestId') or (marker.group(0) if marker else request_hash)
  if not isinstance(key,str) or not 1<=len(key)<=300:raise DataError('invalid concept operation key')
  actor=p.get('actor')
  if actor is not None and (not isinstance(actor,str) or not 2<=len(actor)<=128):raise DataError('invalid actor')
  with self.store.lock,self.store.connection:
   c=self.store.connection;old=c.execute('SELECT request_hash,response_json FROM concept_receipts WHERE operation_key=?',(key,)).fetchone()
   if old:
    if old['request_hash']!=request_hash:raise DataError('concept operation key conflict')
    saved=json.loads(old['response_json'])
    if 'publicationExpectation' in p:check_writer_expectation(saved,p['publicationExpectation'])
    return saved,True
   current=self.current()
   if current is None or current['revision']!=expected:raise RevisionConflict('concept revision conflict')
   # No caller-supplied timestamp. Exact serialization is checked before INSERT.
   e=envelope(expected,note,document,utc_now())
   if 'publicationExpectation' in p:check_writer_expectation(e,p['publicationExpectation'])
   c.execute('INSERT INTO concept_revisions VALUES(?,?,?,?,?)',('ProjectBS',e['revision'],e['savedAt'],note,json.dumps(document,ensure_ascii=False)))
   c.execute('INSERT INTO concept_receipts VALUES(?,?,?,?)',(key,request_hash,canonical(e),e['savedAt']))
   # Older compatibility clients have no actor: retain note and receipt, invent none.
   if actor is not None:self.store.audit(c,request_id=p.get('requestId') or key,actor=actor,operation='concept.save',resource_type='concept',resource_id='ProjectBS',before_revision=expected,after_revision=expected+1,reason=note,details={'documentHash':digest(document),'operationKey':key})
   return e,False
