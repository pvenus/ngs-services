"""Filex-only current master fields. No fallback to DB, previous content or exporter.
The migration utility must provision and verify the initial source before cutover.
"""
import copy,json
from pathlib import Path
from .filex_cas_transport import FilexCASTransport
from .filex_read import private_read
from .concept import validate,ROOT
from .request_primitives import canonical,digest
from .database import utc_now
PREFIX='canonical-files/binari/concept/'
CONFIG=Path.home()/'Library/Application Support/NPCGen/pds-filex-concept-cas.json'

def factory():
 try:config=json.loads(private_read(CONFIG))
 except FileNotFoundError:raise ValueError('CONCEPT_FILEX_NOT_CONFIGURED')from None
 return FilexCASTransport(config)

def split(envelope):
 document=validate(copy.deepcopy(envelope['document']))
 metadata={k:v for k,v in envelope.items()if k!='document'}
 metadata['document']={k:v for k,v in document.items()if k!='sections'}
 metadata['sectionIds']=[s['id']for s in document['sections']]
 files={'metadata.json':metadata}
 for section in document['sections']:
  base='sections/'+section['id']+'/'
  files[base+'metadata.json']={**{k:v for k,v in section.items()if k!='fields'},'fieldIds':[f['id']for f in section['fields']]}
  for field in section['fields']:files[base+'fields/'+field['id']+'.json']=field
 return files

def join(files):
 meta=files['metadata.json'];sections=[]
 for sid in meta['sectionIds']:
  sm=files['sections/'+sid+'/metadata.json']
  if sm['id']!=sid:raise ValueError('CONCEPT_FILE_ID_MISMATCH')
  fields=[]
  for fid in sm['fieldIds']:
   field=files['sections/'+sid+'/fields/'+fid+'.json']
   if field['id']!=fid:raise ValueError('CONCEPT_FILE_ID_MISMATCH')
   fields.append(field)
  sections.append({**{k:v for k,v in sm.items()if k!='fieldIds'},'fields':fields})
 return {**{k:v for k,v in meta.items()if k not in ('document','sectionIds')},'document':validate({**meta['document'],'sections':sections})}

class FilexConcept:
 def __init__(self,store=None,transport_factory=factory):self.store,self.factory=store,transport_factory
 def snapshot(self):
  with self.factory()as t:
   cap=t.capabilities()
   if cap.get('namespace')!=PREFIX or cap.get('lockedBatch')is not True:raise ValueError('CONCEPT_SCOPED_BATCH_WRITER_REQUIRED')
   s=t.snapshot()
  hashes={p:digest(v)for p,v in s['documents'].items()}
  if hashes!=s['hashes']or digest(hashes)!=s['sourceVersion']:raise ValueError('CONCEPT_FILEX_HASH_MISMATCH')
  return s
 def current(self):return join(self.snapshot()['documents'])
 def history(self):
  e=self.current();return [{k:e[k]for k in ('revision','savedAt','note')}]
 def revision(self,n):
  e=self.current();return e if n==e['revision']else None
 def save(self,*args):raise ValueError('CONCEPT_FORMAL_REQUEST_REQUIRED')
 def recover(self,key,input_hash):
  with self.factory()as t:receipt=t.receipt(key)
  if not receipt:return None
  if receipt.get('inputHash')!=input_hash:raise ValueError('CONCEPT_IDEMPOTENCY_CONFLICT')
  if receipt.get('status')!='committed':return None
  s=self.snapshot()
  if any(s['hashes'].get(k.removeprefix(PREFIX))!=v for k,v in receipt['hashes'].items()):raise ValueError('CONCEPT_PRIOR_RESULT_SUPERSEDED')
  current=join(s['documents'])
  return {'requestId':key,'inputHash':input_hash,'documentHash':digest(current['document']),'revision':current['revision'],'sourceVersion':s['sourceVersion'],'verified':True,'source':'filex','ngsReplicaPulled':False}
 def apply(self,r,input_hash,document,selection,*,allow_add=False):
  previous=self.recover(r['requestId'],input_hash)
  if previous:return previous
  s=self.snapshot();current=join(s['documents'])
  if current['revision']!=r['expectedRevision']or digest(current['document'])!=r['documentHash']:raise ValueError('CONCEPT_SOURCE_CONFLICT')
  from .rendering_rules import validate_document
  validate_document(document)
  e={'revision':current['revision']+1,'savedAt':utc_now(),'note':'정식 마스터 수정 '+r['requestId'],'document':validate(document)}
  after=split(e);before=s['documents']
  if not set(before)<=set(after) or (not allow_add and set(before)!=set(after)):raise ValueError('CONCEPT_MODIFY_STRUCTURE_CHANGED')
  files=[{'path':PREFIX+p,'body':v,'expectedHash':s['hashes'].get(p)}for p,v in after.items()if v!=before.get(p)]
  with self.factory()as t:t.call('POST','/v1/batch',{'requestId':r['requestId'],'inputHash':input_hash,'files':files,'readSet':{PREFIX+p:s['hashes'].get(p) for p in set(before)|set(after)}})
  result=self.recover(r['requestId'],input_hash)
  if result is None:raise ValueError('CONCEPT_COMMIT_OUTCOME_UNKNOWN')
  return result
 def export(self):
  e=self.current();schema=(ROOT/'schema.json').read_text();body=canonical(e).encode()
  import hashlib
  return {'envelope':e,'schema':schema,'sourceRevisionVector':{'concept':e['revision'],'concept.document':hashlib.sha256(body).hexdigest(),'concept.schema':hashlib.sha256(schema.encode()).hexdigest()},'authority':'filex'}
 def publication_capability(self):return {'schemaVersion':'pds-concept-filex.v1','source':'filex','directWrite':False,'currentOnly':True}
 def publication_preview(self,*args):raise ValueError('CONCEPT_FORMAL_REQUEST_REQUIRED')
