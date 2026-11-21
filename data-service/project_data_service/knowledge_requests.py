"""Formal knowledge mutations: pinned Laya selection, durable CAS and recovery.
No caller-provided selection is accepted. Filex owns canonical content. SQLite
keeps request state, receipts and explicitly retained compressed scratch recovery
archives. Pointer CAS and recover are provided by Filex.
"""
import hashlib,json,urllib.request,urllib.error,sqlite3,threading,time
from pathlib import Path
from .domain_contracts.knowledge_catalog import KnowledgeError
from .registry_contract import validate_active_registry
from .filex_knowledge import find_entry
from .knowledge_category_removal import current as request_current

def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(v):return hashlib.sha256(canonical(v).encode()).hexdigest()
def select(request):
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*a,**k):raise ValueError('KNOWLEDGE_LAYA_REDIRECT_DENIED')
 body={'mode':'single','requestType':'knowledge.'+request['operation'],'request':request}
 req=urllib.request.Request('http://127.0.0.1:8040/v1/workflow-selections',data=canonical(body).encode(),headers={'Content-Type':'application/json'},method='POST')
 try:
  with urllib.request.build_opener(NoRedirect).open(req,timeout=210)as response:return json.load(response)
 except urllib.error.HTTPError as error:
  raise KnowledgeError('KNOWLEDGE_LAYA_REJECTED: '+error.read(65536).decode('utf-8',errors='replace')) from None

class RequestJournal:
 def __init__(self,path):
  path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);self.path=path
  self.lock=threading.RLock();self.connection=sqlite3.connect(path,check_same_thread=False);self.connection.row_factory=sqlite3.Row
 def close(self):self.connection.close()

def selection_not_started(error):
 """Only the explicit Laya pre-selection source-read failure permits retry."""
 prefix='KNOWLEDGE_LAYA_REJECTED: '
 if not isinstance(error,str)or not error.startswith(prefix):return False
 try:value=json.loads(error[len(prefix):])
 except ValueError:return False
 return value.get('error')=='LAYA_KNOWLEDGE_UNAVAILABLE'

class KnowledgeRequests:
 def __init__(self,store,selector=select,*,content=None,authorer=None,executor=None):
  self.store,self.selector,self.content=store,selector,content
  self.authorer=authorer
  from .knowledge_execution import KnowledgeExecution
  import os
  self.executor=executor or KnowledgeExecution(os.environ.get('NGS_KNOWLEDGE_WORKER_ROOT') or Path(store.path).parent/'knowledge-worker-scratch')
  with store.lock,store.connection:
   store.connection.execute('CREATE TABLE IF NOT EXISTS knowledge_requests(id TEXT PRIMARY KEY,input_hash TEXT NOT NULL,request_json TEXT NOT NULL,state TEXT NOT NULL,selection_json TEXT,result_json TEXT,error TEXT)')
   store.connection.execute('CREATE TABLE IF NOT EXISTS knowledge_authoring_traces(id TEXT PRIMARY KEY,trace_json TEXT NOT NULL)')
   store.connection.execute('CREATE TABLE IF NOT EXISTS knowledge_request_files(id TEXT PRIMARY KEY,files_json TEXT NOT NULL)')
   store.connection.execute('CREATE TABLE IF NOT EXISTS knowledge_execution_cleanup(id TEXT PRIMARY KEY,status_json TEXT NOT NULL)')
 def view(self,key):
  with self.store.lock:
   row=self.store.connection.execute('SELECT * FROM knowledge_requests WHERE id=?',(key,)).fetchone()
   if not row:return None
   authored=self.store.connection.execute('SELECT trace_json FROM knowledge_authoring_traces WHERE id=?',(key,)).fetchone()
   cleanup=self.store.connection.execute('SELECT status_json FROM knowledge_execution_cleanup WHERE id=?',(key,)).fetchone()
   return {'cleanup':json.loads(cleanup[0])if cleanup else None,'authoringTrace':json.loads(authored[0])if authored else None,'requestId':key,'state':row['state'],'request':json.loads(row['request_json']),'selection':json.loads(row['selection_json']) if row['selection_json'] else None,'result':json.loads(row['result_json']) if row['result_json'] else None,'error':row['error'],'publicationVerified':bool(row['result_json'] and json.loads(row['result_json']).get('verified')),'ngsReplicaPulled':False,'inputHash':row['input_hash'],'resumeAvailable':row['state']=='failed' and (bool(row['selection_json'])or selection_not_started(row['error']))}
 def validate_receipt(self,key,input_hash,result):
  if (not isinstance(result,dict) or result.get('requestId')!=key or result.get('inputHash')!=input_hash or not result.get('sourceVersion') or not isinstance(result.get('entry'),dict) or result.get('verified') is not True or result.get('fileHash')!=digest(result['entry'])):raise KnowledgeError('KNOWLEDGE_FILEX_RECEIPT_INVALID')
  return result
 def cleanup(self,request,result):
  # Publication is already durable. Cleanup failure is separate and replay retries it.
  try:status=self.executor.finalize(request,result,self.store)
  except Exception as error:status={'state':'pending','error':str(error)[:2000],'publicationPreserved':True}
  status={**status,'attemptedAt':time.time()}
  with self.store.lock,self.store.connection:self.store.connection.execute('INSERT OR REPLACE INTO knowledge_execution_cleanup VALUES(?,?)',(request['requestId'],canonical(status)))
  return status
 def completed(self,key,input_hash,stored):
  try:
   recovered=self.content.recover(key,input_hash)
   if recovered is None:raise KnowledgeError('KNOWLEDGE_COMMIT_OUTCOME_UNCERTAIN')
   self.validate_receipt(key,input_hash,recovered)
   with self.store.lock:row=self.store.connection.execute('SELECT request_json FROM knowledge_requests WHERE id=?',(key,)).fetchone()
   self.cleanup(json.loads(row[0]),recovered)
   return recovered
  except ValueError as error:
   if 'SUPERSEDED'in str(error):
    with self.store.lock:row=self.store.connection.execute('SELECT request_json FROM knowledge_requests WHERE id=?',(key,)).fetchone()
    self.cleanup(json.loads(row[0]),stored)
    return {**stored,'resultSuperseded':True}
   raise
 def retry_cleanup(self,limit=8):
  # Only already committed requests with our new owned scratch are eligible.
  # No selector, model, publication, or failed/unknown request is executed here.
  with self.store.lock:
   rows=self.store.connection.execute("SELECT r.request_json,r.result_json FROM knowledge_requests r LEFT JOIN knowledge_execution_cleanup c ON r.id=c.id WHERE r.state='completed' AND r.result_json IS NOT NULL ORDER BY COALESCE(json_extract(c.status_json,'$.attemptedAt'),0),r.id").fetchall()
  results=[]
  for row in rows:
   request=json.loads(row[0]);receipt=json.loads(row[1])
   if not (self.executor.root/'requests'/digest(request)).exists():continue
   try:
    with self.executor.locked(request,namespace='pds-'):
     results.append({'requestId':request['requestId'],'cleanup':self.cleanup(request,receipt)})
   except ValueError as error:
    if 'BUSY' not in str(error):raise
   if len(results)>=limit:break
  return results
 def metadata(self,result):
  return {**{k:v for k,v in result.items()if k!='entry'},'entryId':result['entry'].get('id'),'entryVersion':result['entry'].get('version')}
 def resume(self,key,expected_hash):
  old=self.view(key)
  if old is None or old['inputHash']!=expected_hash:raise KnowledgeError('KNOWLEDGE_RESUME_BINDING_INVALID')
  if old['state']=='completed':return self.completed(key,expected_hash,old['result'])
  if not old['selection']and not (old['state']=='failed'and selection_not_started(old['error'])):raise KnowledgeError('KNOWLEDGE_SELECTION_OUTCOME_UNKNOWN_NO_REEXECUTION')
  return self.submit(old['request'],_resume=True)
 def snapshot(self):
  if self.content is None:raise KnowledgeError('KNOWLEDGE_FILEX_PORT_NOT_CONFIGURED')
  snapshot=self.content.snapshot()
  if not isinstance(snapshot,dict) or not snapshot.get('sourceVersion') or not isinstance(snapshot.get('documents'),dict):raise KnowledgeError('KNOWLEDGE_FILEX_SNAPSHOT_INVALID')
  return snapshot
 def submit(self,r,_resume=False):
  # Serialize the complete PDS transaction, not just model execution/cleanup.
  # Invalid input is still rejected by _submit before any DB mutation.
  with self.executor.locked(r,namespace='pds-'):
   return self._submit(r,_resume)
 def _submit(self,r,_resume=False):
  required={'schemaVersion','requestId','projectId','operation','entryId','expectedVersion','note'}
  if not isinstance(r,dict) or not required<=set(r) or set(r)-required-{'entry','changes','authoring'} or r['schemaVersion']!='ngs.knowledge-request.v1' or r['projectId']!='binari':raise KnowledgeError('KNOWLEDGE_REQUEST_INVALID')
  op=r['operation'];key=r['requestId'];entry=r['entryId']
  if op not in ('create','update','delete','delete-category') or not isinstance(key,str) or not 8<=len(key)<=200 or type(r['expectedVersion'])is not int or not isinstance(entry,str):raise KnowledgeError('KNOWLEDGE_REQUEST_INVALID')
  if op=='create' and (r['expectedVersion']!=0 or r.get('entry',{}).get('id')!=entry):raise KnowledgeError('KNOWLEDGE_CREATE_INVALID')
  if entry=='laya-worker-registry' and (op=='delete' or op=='update' and any(r.get('changes',{}).get(k,v)!=v for k,v in [('status','active'),('authority','authoritative')])):raise KnowledgeError('KNOWLEDGE_BOOTSTRAP_DEPENDENCY_REQUIRED')
  if op!='delete' and (r.get('changes')or r.get('entry')or{}).get('status')=='deleted':raise KnowledgeError('KNOWLEDGE_DELETE_REQUEST_REQUIRED')
  if 'authoring'in r:
   if op in ('delete','delete-category') or not isinstance(r['authoring'],dict)or set(r['authoring'])!={'description'}or not isinstance(r['authoring']['description'],str)or not r['authoring']['description'].strip():raise KnowledgeError('KNOWLEDGE_AUTHORING_INPUT_INVALID')
   if entry=='laya-worker-registry':raise KnowledgeError('KNOWLEDGE_EXECUTION_REGISTRY_REQUIRES_EXPLICIT_CONTRACT')
  if callable(getattr(self.content,'require_write_capability',None)):self.content.require_write_capability()
  h=digest(r);c=self.store.connection;prior_selection=None
  snapshot=self.snapshot();documents=snapshot['documents']
  with self.store.lock,c:
   old=c.execute('SELECT * FROM knowledge_requests WHERE id=?',(key,)).fetchone()
   if old:
    if old['input_hash']!=h:raise KnowledgeError('KNOWLEDGE_IDEMPOTENCY_CONFLICT')
    if old['state']=='completed':return self.completed(key,h,json.loads(old['result_json']))
    # Unknown in-flight selection cannot start a second model call.
    if not _resume or (not old['selection_json']and not(old['state']=='failed'and selection_not_started(old['error']))):raise KnowledgeError('KNOWLEDGE_REQUEST_'+old['state'].upper()+': '+str(old['error'] or 'inspect persisted request before retry'))
    prior_selection=json.loads(old['selection_json'])if old['selection_json']else None
    c.execute('UPDATE knowledge_requests SET state=?,error=NULL WHERE id=?',('selecting',key))
   current=request_current(documents,r)
   recovered=self.content.recover(key,h) if old else None
   if recovered is not None:
    self.validate_receipt(key,h,recovered)
    c.execute('UPDATE knowledge_requests SET state=?,result_json=?,error=NULL WHERE id=?',('completed',canonical(self.metadata(recovered)),key));c.execute('DELETE FROM knowledge_request_files WHERE id=?',(key,));c.commit();self.cleanup(r,recovered);return recovered
   if (op=='create' and current is not None) or (op!='create' and (not current or current.get('version',0)!=r['expectedVersion'])):raise KnowledgeError('KNOWLEDGE_VERSION_CONFLICT')
   if not old:c.execute('INSERT INTO knowledge_requests VALUES(?,?,?,?,NULL,NULL,NULL)',(key,h,canonical(r),'selecting'))
  try:
   selection=prior_selection if prior_selection is not None else self.selector(r)
   with self.store.lock,c:c.execute('UPDATE knowledge_requests SET selection_json=? WHERE id=?',(canonical(selection),key))
   q=selection.get('selectionQuality',{});stages=selection.get('stages',[])
   score=q.get('score');threshold=q.get('threshold')
   if (selection.get('requestHash')!=h or len(stages)!=1 or stages[0].get('id')!='knowledge.'+op or stages[0].get('workerId')!='knowledge-mutation' or len(selection.get('invocations',[]))!=1 or q.get('passed')is not True or type(score)not in (int,float) or type(threshold)not in (int,float) or not 0<=threshold<=score<=1):raise KnowledgeError('KNOWLEDGE_SELECTION_REJECTED')
   # Recover the exact committed file operation before considering another write.
   recovered=self.content.recover(key,h)
   if recovered is not None:
    saved=recovered
   else:
    latest=self.snapshot();documents=latest['documents'];current=request_current(documents,r)
    if (op=='create' and current is not None) or (op!='create' and (not current or current.get('version',0)!=r['expectedVersion'])):raise KnowledgeError('KNOWLEDGE_VERSION_CONFLICT')
    source=selection.get('knowledgeSource')or{};registry=find_entry(documents,'laya-worker-registry')[1]
    if registry is None:raise KnowledgeError('KNOWLEDGE_BOOTSTRAP_REQUIRED')
    if source.get('entryVersion')!=registry['version'] or source.get('coreRegistryHash',selection.get('registryHash'))!=digest(registry['data']):raise KnowledgeError('KNOWLEDGE_SELECTION_SOURCE_CHANGED')
    if source.get('sourceVersion') and source['sourceVersion']!=latest['sourceVersion']:raise KnowledgeError('KNOWLEDGE_SELECTION_SOURCE_CHANGED')
    if entry=='laya-worker-registry':
     proposed=r['entry'] if op=='create' else {**current,**r['changes']}
     if proposed.get('type')!='worker-registry' or proposed.get('status')!='active' or proposed.get('authority')!='authoritative':raise KnowledgeError('KNOWLEDGE_REGISTRY_ENVELOPE_INVALID')
     validate_active_registry(proposed.get('data'))
    with self.store.lock,c:c.execute('UPDATE knowledge_requests SET state=? WHERE id=?',('applying',key))
    with self.store.lock:
     prepared=c.execute('SELECT files_json FROM knowledge_request_files WHERE id=?',(key,)).fetchone()
    if prepared:files=json.loads(prepared[0])
    else:
     output=self.executor.execute(r,latest,authorer=self.authorer)
     files=output['files'];author_trace=output.get('authoringTrace')
     with self.store.lock,c:
      if author_trace is not None:c.execute('INSERT OR REPLACE INTO knowledge_authoring_traces VALUES(?,?)',(key,canonical(author_trace)))
      c.execute('INSERT INTO knowledge_request_files VALUES(?,?)',(key,canonical(files)))
    # Reject inconsistent new workflow knowledge before replacing the current file.
    from .filex_knowledge import FilexKnowledge
    proposed_documents={**latest['documents'],**{f['path']:f['body']for f in files}}
    core=find_entry(proposed_documents,'laya-worker-registry')[1]
    if core and isinstance(core.get('data'),dict)and core['data'].get('schemaVersion')=='ngs.worker-registry.v1':
     proposed_hashes={p:digest(v)for p,v in proposed_documents.items()}
     FilexKnowledge.compile_laya_catalog({'documents':proposed_documents,'hashes':proposed_hashes,'sourceVersion':digest(proposed_hashes)})
    saved=self.content.apply(r,latest['sourceVersion'],selection,files=files)
   self.validate_receipt(key,h,saved)
   with self.store.lock,c:
    c.execute('UPDATE knowledge_requests SET state=?,result_json=?,error=NULL WHERE id=?',('completed',canonical(self.metadata(saved)),key))
    c.execute('DELETE FROM knowledge_request_files WHERE id=?',(key,))
   self.cleanup(r,saved)
   return saved
  except Exception as error:
   with self.store.lock,c:c.execute("UPDATE knowledge_requests SET state=?,error=? WHERE id=? AND state!='completed'",('failed',str(error)[:2000],key))
   raise
