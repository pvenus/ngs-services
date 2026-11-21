"""One-result formal master edits. Source is injected; no direct content DB writes.
Only exact named fields may change. The journal fences workflow selection and author calls;
saved output and source operation receipts permit recovery without regeneration.
"""
import copy,json,re,urllib.request,fcntl,os
from pathlib import Path
from .request_primitives import canonical,digest

def select(request):
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*a,**k):raise ValueError('CONCEPT_WORKFLOW_REDIRECT_DENIED')
 req=urllib.request.Request(os.environ.get('WORKFLOW_DECISION_URL','http://127.0.0.1:8040').rstrip('/')+'/v1/workflow-selections',data=canonical({'mode':'single','requestType':'concept.modify','request':request}).encode(),headers={'Content-Type':'application/json'},method='POST')
 with urllib.request.build_opener(NoRedirect).open(req,timeout=210)as response:return json.load(response)

def validate_request(r):
 if not isinstance(r,dict) or set(r)!={'schemaVersion','requestId','projectId','intent','targets','expectedRevision','documentHash'} or r['schemaVersion']!='ngs.concept-modify-request.v1' or r['projectId']!='binari':raise ValueError('CONCEPT_REQUEST_INVALID')
 if not isinstance(r['requestId'],str) or not re.fullmatch(r'[A-Za-z0-9._:-]{8,180}',r['requestId']):raise ValueError('CONCEPT_REQUEST_ID_INVALID')
 if not isinstance(r['intent'],str) or not r['intent'].strip() or type(r['expectedRevision'])is not int or r['expectedRevision']<1 or not isinstance(r['documentHash'],str) or not re.fullmatch('[a-f0-9]{64}',r['documentHash']):raise ValueError('CONCEPT_REQUEST_INPUT_INVALID')
 if not isinstance(r['targets'],list)or not 1<=len(r['targets'])<=16:raise ValueError('CONCEPT_TARGETS_INVALID')
 seen=set()
 for t in r['targets']:
  if not isinstance(t,dict)or set(t)!={'categoryId','itemId'}or any(not isinstance(t[k],str)or not re.fullmatch(r'[A-Za-z0-9._:-]{1,180}',t[k])for k in t):raise ValueError('CONCEPT_TARGET_INVALID')
  pair=(t['categoryId'],t['itemId'])
  if pair in seen:raise ValueError('CONCEPT_TARGET_DUPLICATE')
  seen.add(pair)
 return r

def target_fields(document,targets):
 out=[];hierarchy=document.get('hierarchy',{})
 for t in targets:
  if t['categoryId']in hierarchy.get('archivedCategoryIds',[])or t in hierarchy.get('archivedItems',[]):raise ValueError('CONCEPT_TARGET_ARCHIVED')
  fields=[f for s in document['sections']if s['id']==t['categoryId']for f in s['fields']if f['id']==t['itemId']]
  if len(fields)!=1:raise ValueError('CONCEPT_TARGET_NOT_FOUND')
  out.append({**t,'label':fields[0]['label'],'value':fields[0]['value']})
 return out

def apply_values(document,targets,output):
 if not isinstance(output,dict)or set(output)!={'fields'}or not isinstance(output['fields'],list)or len(output['fields'])!=len(targets):raise ValueError('CONCEPT_OUTPUT_SHAPE')
 expected={(t['categoryId'],t['itemId'])for t in targets};seen=set();result=copy.deepcopy(document)
 for f in output['fields']:
  if not isinstance(f,dict)or set(f)!={'categoryId','itemId','value'}or (f['categoryId'],f['itemId'])not in expected or (f['categoryId'],f['itemId'])in seen or not isinstance(f['value'],str)or not 1<=len(f['value'].strip())<=12000:raise ValueError('CONCEPT_OUTPUT_TARGET_INVALID')
  seen.add((f['categoryId'],f['itemId']))
  for section in result['sections']:
   if section['id']==f['categoryId']:
    next(v for v in section['fields']if v['id']==f['itemId'])['value']=f['value']
 return result

class ConceptRequests:
 def __init__(self,journal,source,authorer,selector=select):
  self.journal,self.source,self.authorer,self.selector=journal,source,authorer,selector
  with journal.lock,journal.connection:journal.connection.execute('CREATE TABLE IF NOT EXISTS concept_formal_requests(id TEXT PRIMARY KEY,input_hash TEXT NOT NULL,request_json TEXT NOT NULL,state TEXT NOT NULL,selection_json TEXT,output_json TEXT,trace_json TEXT,result_json TEXT,error TEXT)')
 def view(self,key):
  with self.journal.lock:
   row=self.journal.connection.execute('SELECT * FROM concept_formal_requests WHERE id=?',(key,)).fetchone()
   if not row:return None
   return {'requestId':key,'inputHash':row['input_hash'],'state':row['state'],'request':json.loads(row['request_json']),'selection':json.loads(row['selection_json'])if row['selection_json']else None,'trace':json.loads(row['trace_json'])if row['trace_json']else None,'result':json.loads(row['result_json'])if row['result_json']else None,'error':row['error']}
 def write(self,key,**values):
  if not set(values)<={'state','selection_json','output_json','trace_json','result_json','error'}:raise ValueError('CONCEPT_JOURNAL_FIELD_INVALID')
  with self.journal.lock,self.journal.connection:self.journal.connection.execute('UPDATE concept_formal_requests SET '+','.join(k+'=?'for k in values)+' WHERE id=?',(*values.values(),key))
 def submit(self,r,resume=False):
  r=validate_request(r)
  dbpath=self.journal.connection.execute('PRAGMA database_list').fetchone()[2]
  root=Path(dbpath).parent/'.concept-request-locks';root.mkdir(exist_ok=True)
  with (root/(digest(r['requestId'])+'.lock')).open('a')as lock:
   try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:raise ValueError('CONCEPT_REQUEST_IN_PROGRESS')from None
   return self._submit(r,resume)
 def _submit(self,r,resume=False):
  r=validate_request(r);key=r['requestId'];h=digest(r);old=self.view(key)
  if old and old['inputHash']!=h:raise ValueError('CONCEPT_IDEMPOTENCY_CONFLICT')
  if old and old['state']=='completed':return old
  if old and not resume:raise ValueError('CONCEPT_REQUEST_EXISTS_INSPECT_OR_RESUME')
  # Source receipt is checked before stale-basis validation: lost replies never regenerate.
  recovered=self.source.recover(key,h)
  if recovered is not None:
   if not old:raise ValueError('CONCEPT_UNEXPECTED_RECEIPT')
   self.write(key,state='completed',result_json=canonical(recovered),error=None);return self.view(key)
  current=self.source.current()
  if current['revision']!=r['expectedRevision']or digest(current['document'])!=r['documentHash']:raise ValueError('CONCEPT_SOURCE_CONFLICT')
  fields=target_fields(current['document'],r['targets'])
  if not old:
   with self.journal.lock,self.journal.connection:self.journal.connection.execute('INSERT INTO concept_formal_requests VALUES(?,?,?,?,NULL,NULL,NULL,NULL,NULL)',(key,h,canonical(r),'selecting'))
  try:
   if old:
    if not old['selection']:raise ValueError('CONCEPT_SELECTION_OUTCOME_UNKNOWN')
    selection=old['selection']
   else:
    selection=self.selector(r);self.write(key,selection_json=canonical(selection))
   q=selection.get('selectionQuality',{});score=q.get('score');threshold=q.get('threshold');stages=selection.get('stages',[])
   if selection.get('requestHash')!=h or len(stages)!=1 or stages[0].get('id')!='concept.modify' or stages[0].get('workerId')!='concept-modify' or len(selection.get('invocations',[]))!=1 or q.get('passed')is not True or type(score)not in (int,float)or type(threshold)not in (int,float)or not 0<=threshold<=score<=1:raise ValueError('CONCEPT_SELECTION_REJECTED')
   latest=self.source.current()
   if latest!=current:raise ValueError('CONCEPT_SOURCE_CONFLICT')
   with self.journal.lock:saved=self.journal.connection.execute('SELECT output_json FROM concept_formal_requests WHERE id=?',(key,)).fetchone()[0]
   if saved:output=json.loads(saved)
   else:
    recovering=bool(old and old['state']in ('authoring','failed'))
    if recovering and not callable(getattr(self.authorer,'recover',None)):raise ValueError('CONCEPT_AUTHOR_OUTCOME_UNKNOWN_RECOVER_SAVED_OUTPUT')
    self.write(key,state='authoring')
    output,trace=self.authorer.recover(r,fields)if recovering else self.authorer(r,fields)
    apply_values(current['document'],r['targets'],output)
    self.write(key,output_json=canonical(output),trace_json=canonical(trace),state='generated')
   document=apply_values(current['document'],r['targets'],output)
   self.write(key,state='applying')
   result=self.source.apply(r,h,document,selection)
   if not isinstance(result,dict)or result.get('requestId')!=key or result.get('inputHash')!=h or result.get('documentHash')!=digest(document)or result.get('verified')is not True:raise ValueError('CONCEPT_RECEIPT_INVALID')
   self.write(key,state='completed',result_json=canonical(result),error=None);return self.view(key)
  except Exception as error:
   self.write(key,state='failed',error=str(error));raise
 def resume(self,key,input_hash):
  old=self.view(key)
  if not old or old['inputHash']!=input_hash:raise ValueError('CONCEPT_RESUME_BINDING_INVALID')
  return self.submit(old['request'],resume=True)
