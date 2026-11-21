"""Explicit one-time exact-copy migration. Never edits legacy DB or existing Filex files."""
import json,urllib.request
from .concept_filex import factory,split,join,PREFIX,FilexConcept
from .request_primitives import digest

def migrate(envelope,transport_factory=factory):
 files=split(envelope)
 with transport_factory() as t:
  cap=t.capabilities()
  if cap.get('namespace')!=PREFIX or cap.get('lockedBatch')is not True:raise ValueError('CONCEPT_SCOPED_BATCH_WRITER_REQUIRED')
  snapshot=t.snapshot()
  if snapshot['documents']:
   if snapshot['documents']!=files:raise ValueError('CONCEPT_MIGRATION_EXISTING_FILES_DIFFER')
  else:
   h=digest(envelope)
   t.call('POST','/v1/batch',{'requestId':'concept.seed.'+h[:32],'inputHash':h,'files':[{'path':PREFIX+p,'body':v,'expectedHash':None}for p,v in files.items()],'readSet':{PREFIX+p:None for p in files}})
 current=FilexConcept(transport_factory=transport_factory).current()
 if current!=envelope:raise ValueError('CONCEPT_MIGRATION_READBACK_MISMATCH')
 return {'verified':True,'documentHash':digest(current['document']),'envelopeHash':digest(current),'revision':current['revision'],'files':len(files)}

if __name__=='__main__':
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self,*args,**kwargs):raise ValueError('CONCEPT_MIGRATION_REDIRECT_DENIED')
 with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open('http://127.0.0.1:8030/api/concept',timeout=30)as r:envelope=json.load(r)
 print(json.dumps(migrate(envelope)))
