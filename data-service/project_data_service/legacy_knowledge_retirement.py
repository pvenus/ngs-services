"""Unregistered legacy-retirement adapter. No HTTP route or production activation.
Only the five retired DB entries are eligible. Filex is never read or written.
A registered Laya stage and official request adapter are prerequisites to use.
"""
import json
from .knowledge_requests import canonical,digest
IDS=frozenset({'character-image-generation-guide','codex-work-direction','document-migration-plan','mcp-capability-overview','seojin-character-brief'})
STAGE='knowledge.legacy-retire'
WORKER='knowledge-legacy-retirement'
def fingerprint(row):
 return digest({k:row[k]for k in row.keys()if k not in ('status','version','updated_at')})
def inspect(connection):
 rows=connection.execute('SELECT * FROM entries WHERE id IN (?,?,?,?,?) ORDER BY id',tuple(sorted(IDS))).fetchall()
 if {r['id']for r in rows}!=IDS:raise ValueError('LEGACY_RETIRE_SCOPE_MISSING')
 return {r['id']:{'version':r['version'],'status':r['status'],'bodyHash':fingerprint(r)}for r in rows}
def retire(connection,request,selection):
 if set(request)!={'requestId','operation','entries'}or request['operation']!=STAGE or set(request['entries'])!=IDS:raise ValueError('LEGACY_RETIRE_SCOPE_DENIED')
 q=selection.get('selectionQuality',{});stages=selection.get('stages',[])
 if selection.get('requestHash')!=digest(request)or len(stages)!=1 or stages[0].get('id')!=STAGE or stages[0].get('workerId')!=WORKER or q.get('passed')is not True or type(q.get('score'))not in (float,int)or type(q.get('threshold'))not in (float,int)or not 0<=q['threshold']<=q['score']<=1:raise ValueError('LEGACY_RETIRE_LAYA_REQUIRED')
 if connection.in_transaction:raise ValueError('LEGACY_RETIRE_TRANSACTION_REQUIRED')
 connection.execute('BEGIN IMMEDIATE')
 try:
  connection.execute('CREATE TABLE IF NOT EXISTS legacy_retirement_receipts(request_id TEXT PRIMARY KEY,input_hash TEXT NOT NULL,receipt TEXT NOT NULL)')
  old=connection.execute('SELECT input_hash,receipt FROM legacy_retirement_receipts WHERE request_id=?',(request['requestId'],)).fetchone()
  if old:
   if old[0]!=digest(request):raise ValueError('LEGACY_RETIRE_IDEMPOTENCY_CONFLICT')
   result=json.loads(old[1]);current=inspect(connection)
   if any(current[i]['status']!='deleted'or current[i]['bodyHash']!=result['after'][i]['bodyHash']for i in IDS):raise ValueError('LEGACY_RETIRE_RESULT_CHANGED')
   connection.rollback();return result
  before=inspect(connection)
  if before!=request['entries']:raise ValueError('LEGACY_RETIRE_VERSION_CONFLICT')
  for i in sorted(IDS):
   if before[i]['status']=='deleted':continue
   connection.execute('UPDATE entries SET status=?,version=version+1 WHERE id=? AND version=?',('deleted',i,before[i]['version']))
  after=inspect(connection)
  if any(after[i]['status']!='deleted'or after[i]['bodyHash']!=before[i]['bodyHash']for i in IDS):raise ValueError('LEGACY_RETIRE_BODY_CHANGED')
  result={'requestId':request['requestId'],'inputHash':digest(request),'state':'completed','recoverable':True,'before':before,'after':after,'filexChanged':False}
  connection.execute('INSERT INTO legacy_retirement_receipts VALUES(?,?,?)',(request['requestId'],digest(request),canonical(result)));connection.commit();return result
 except Exception:connection.rollback();raise
