"""Explicit once-only migration: old knowledge API to current Filex canonical files.
Defaults to dry-run. Requires owner-approved cutover; never falls back at runtime.
Only create-if-absent CAS, no removal or change of existing canonical files.
"""
import argparse,datetime,hashlib,json,pathlib,urllib.request
from project_data_service.filex_cas_transport import FilexCASTransport
from project_data_service.filex_read import canonical,digest
from project_data_service.registry_contract import validate_active_registry
PREFIX='canonical-files/binari/knowledge/'
def read(path):
 with urllib.request.urlopen('http://127.0.0.1:8030'+path,timeout=20)as r:return json.load(r)
def capture():
 overview=read('/api/knowledge/overview');docs={'overview.json':overview}
 for c in overview['categories']:
  cat=read('/api/knowledge/categories/'+urllib.parse.quote(c['id'],safe=''));docs['categories/'+c['id']+'.json']=cat
  for item in cat['entries']:
   entry=read('/api/knowledge/entries/'+urllib.parse.quote(item['id'],safe=''));docs['entries/'+entry['id']+'.json']=entry
 if read('/api/knowledge/overview')!=overview:raise ValueError('BOOTSTRAP_SOURCE_CHANGED')
 return docs
def execute(registry_path,apply=False):
 if apply:raise ValueError('LEGACY_KNOWLEDGE_MIGRATION_CANCELLED_BY_OWNER')
 raw=pathlib.Path(registry_path).read_bytes();registry=json.loads(raw);validate_active_registry(registry)
 docs=capture();original_hashes={p:digest(d)for p,d in docs.items()};now=datetime.datetime.now(datetime.timezone.utc).isoformat()
 key='entries/laya-worker-registry.json'
 if key in docs:raise ValueError('BOOTSTRAP_EXISTING_REGISTRY_REQUIRES_REVIEW')
 docs[key]={'id':'laya-worker-registry','categoryId':'workflow','type':'worker-registry','title':'NGS 요청 처리 워커 지식','purpose':'라야의 요청 처리 워커·입출력·실행 순서 선택','content':'NGS에 표시되는 이 구조화 지식이 라야 선택의 원본입니다. 변경은 정식 요청으로 처리합니다.','data':registry,'triggers':['요청 처리','워커','워크플로'],'authority':'authoritative','status':'active','maxReadChars':50000,'version':1,'updatedAt':now}
 report={'mode':'apply'if apply else'dry-run','sourceRevision':docs['overview.json']['revision'],'preservedFiles':len(original_hashes),'existingEntryCount':sum(p.startswith('entries/')for p in original_hashes),'registryVersion':registry['version'],'registryBytesHash':hashlib.sha256(raw).hexdigest(),'registryHash':digest(registry),'sourceHashes':original_hashes,'created':[]}
 if not apply:return report
 with FilexCASTransport()as t:
  if t.capabilities().get('atomicCAS')is not True:raise ValueError('BOOTSTRAP_ATOMIC_CAS_REQUIRED')
  start=t.snapshot()
  if set(start['documents'])-set(docs):raise ValueError('BOOTSTRAP_UNEXPECTED_CANONICAL_FILES')
  # Preflight every already present file before any write. Registry timestamp on
  # resume is retained, but no existing data or runtime registry can be replaced.
  for p,v in start['documents'].items():
   if p==key:
    if {k:x for k,x in v.items()if k!='updatedAt'}!={k:x for k,x in docs[p].items()if k!='updatedAt'}:raise ValueError('BOOTSTRAP_CANONICAL_CONFLICT')
    docs[p]=v
   elif v!=docs[p]:raise ValueError('BOOTSTRAP_CANONICAL_CONFLICT')
  # Metadata first; registry last makes Laya unavailable rather than partial.
  for p in sorted(docs,key=lambda p:(p==key,p)):
   s=t.snapshot()
   if p in s['documents']:
    if s['documents'][p]!=docs[p]:raise ValueError('BOOTSTRAP_CANONICAL_CHANGED')
    continue
   if capture()!={p:v for p,v in docs.items()if p!=key}:raise ValueError('BOOTSTRAP_SOURCE_CHANGED')
   body=canonical(docs[p]);h=hashlib.sha256(body).hexdigest();request='bootstrap.knowledge.'+digest({'path':p,'hash':h})[:40]
   t.compare_and_swap(PREFIX+p,None,body,request_id=request,input_hash=digest({'migration':'pds-to-filex-once','path':p,'sha256':h}),expected_source_version=s['sourceVersion'])
   report['created'].append(p)
  final=t.snapshot()
  if final['documents']!=docs:raise ValueError('BOOTSTRAP_READBACK_MISMATCH')
  if any(final['hashes'][p]!=h for p,h in original_hashes.items()):raise ValueError('BOOTSTRAP_ORIGINAL_HASH_MISMATCH')
  report.update(verified=True,sourceVersion=final['sourceVersion'],existingEntriesUnchanged=True,originalPDSUnchanged=capture()=={p:v for p,v in docs.items()if p!=key})
 return report
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--registry',required=True);p.add_argument('--apply',action='store_true');args=p.parse_args();print(json.dumps(execute(args.registry,args.apply),ensure_ascii=False,indent=2))
