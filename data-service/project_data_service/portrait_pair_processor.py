"""PDS-owned pair validation and original canonical publication.

Uploaded bytes remain here. Request supplies metadata only. All ports are server
composition objects, never request-provided factories or filesystem paths.
"""
import hashlib,json
from pathlib import Path
from types import SimpleNamespace

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def sha(raw):return hashlib.sha256(raw).hexdigest()

class PortraitPairProcessor:
 def __init__(self,root,*,pairs,validate_alpha,finalize_pair,validate_bundle,assets,selection,scope,reference_asset,binding_reader):
  self.root=Path(root)
  if not self.root.is_absolute()or self.root.is_symlink()or self.root.resolve()!=self.root or not self.root.is_dir():raise ValueError('PDS_PAIR_STAGING_ROOT')
  if not all(callable(x)for x in (validate_alpha,finalize_pair,validate_bundle,assets,selection,scope,reference_asset,binding_reader)):raise ValueError('PDS_PAIR_SERVER_PORTS')
  self.pairs,self.validate_alpha,self.finalize,self.validate_bundle=pairs,validate_alpha,finalize_pair,validate_bundle
  self.assets,self.selection,self.scope,self.reference_asset=assets,selection,scope,reference_asset
  self.binding_reader=binding_reader
 def __call__(self,claim,packet,files,binding):
  import os,fcntl
  key=digest([claim['request_id'],claim['attempt_id']])
  fd=os.open(self.root/('.'+key+'.lock'),os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
  try:
   fcntl.flock(fd,fcntl.LOCK_EX)
   result=self._process(claim,packet,files,binding)
   if self.readback(claim,digest(packet))!=result:raise ValueError('PDS_PAIR_PRE_CLEANUP_READBACK')
   self._retire_inputs(key,claim,packet,files)
   return result
  finally:os.close(fd)
 def _process(self,claim,packet,files,binding):
  from workers.resident.contracts import Claim
  from workers.resident.deployment.portrait_pair_service import FILES
  if set(binding)!={'reservation','record','parentGuard'}or set(packet)!={'manifest'}or set(files)!=set(FILES):raise ValueError('PDS_PAIR_PROCESSOR_BINDING')
  v=dict(claim);v['input_refs']=tuple(v['input_refs']);c=Claim(**v);record=binding['record'];guard=binding['parentGuard']
  if dict(c.parameters)!=record or guard.get('requestId')!=record.get('parentId'):raise ValueError('PDS_PAIR_ORIGINAL_RECORD')
  folder=self.root/digest([c.request_id,c.attempt_id]);folder.mkdir(exist_ok=True)
  if folder.is_symlink()or folder.resolve().parent!=self.root:raise ValueError('PDS_PAIR_STAGING_PATH')
  artifacts=folder/'artifacts';artifacts.mkdir(exist_ok=True)
  if artifacts.is_symlink():raise ValueError('PDS_PAIR_STAGING_PATH')
  def save(path,raw):
   if path.is_symlink():raise ValueError('PDS_PAIR_STAGING_PATH')
   if path.exists():
    if path.read_bytes()!=raw:raise ValueError('PDS_PAIR_STAGING_CONFLICT')
   else:
    import os,tempfile
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
     with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
     os.replace(tmp,path)
     d=os.open(path.parent,os.O_RDONLY)
     try:os.fsync(d)
     finally:os.close(d)
    finally:
     if os.path.exists(tmp):os.unlink(tmp)
  save(folder/'claim.json',json.dumps(claim,sort_keys=True).encode())
  save(artifacts/'pair-manifest.json',json.dumps(packet['manifest'],sort_keys=True).encode())
  for name,raw in files.items():
   if not isinstance(raw,bytes):raise ValueError('PDS_PAIR_BYTES_REQUIRED')
   save(artifacts/name,raw)
  def reference(ref):
   if ref['sha256']==sha(files['output.png']):return files['output.png']
   return self.reference_asset(guard,ref['mainPortraitAssetId'])
  value=self.validate_bundle(c,folder,record,pairs=self.pairs,validate_alpha=self.validate_alpha,reference_reader=reference)
  output={'sha256':sha(value['portrait']),'size':len(value['portrait'])}
  provenance={'runId':c.request_id,'registrationIntentId':c.request_id,'compositionHash':digest(record),'promptHash':record['promptHash'],'sourceRefsHash':digest(record['context']),'providerProofHash':digest(value['portraitProof']),'output':output}
  with self.scope(guard,c.request_id):
   assets=self.assets(guard,c.request_id);port=self.selection(guard,c.request_id)
   asset=assets.register(value['portrait'],'binari',c.request_id,provenance);assets.verify(asset,output)
   pixel_output={'sha256':sha(value['pixel']),'size':len(value['pixel'])}
   pp={'runId':c.request_id+'.pixel','registrationIntentId':c.request_id,'compositionHash':digest(record),'promptHash':value['pixelPromptHash'],'sourceRefsHash':digest(record['context']),'providerProofHash':digest(value['pixelProof']),'postprocess':value['pixelPostprocess'],'pixelSourceSha256':sha(value['pixelSource']),'output':pixel_output}
   pixel=assets.register(value['pixel'],'binari',c.request_id+'.pixel',pp);assets.verify(pixel,pixel_output)
   for a,o in ((asset,output),(pixel,pixel_output)):
    if any(a.get(k)!=x for k,x in {'projectId':'ProjectBS','kind':'portrait','mimeType':'image/png','status':'available',**o}.items())or not a.get('id')or a.get('publicationScope')=='unpublished-worker-staging':raise ValueError('PAIR_CANONICAL_ASSET_REQUIRED')
   draft={'schemaVersion':'npc-workflow-portrait-draft.v1','requestId':c.request_id,'state':'completed','published':True,'publicationScope':'request-draft-file-asset','candidateCount':1,'outputMode':'request-draft.single.v1','sourceApplied':False,'representativeChanged':False,'asset':asset,'promptHash':record['promptHash'],'sourceTrace':record['context'],'providerProof':value['portraitProof'],'posePreset':record['posePreset'],'pixelAsset':pixel,'pixelProviderProof':value['pixelProof'],'referencePortraitAssetId':record.get('referencePortraitAssetId'),'pixelReferencePortraitSha256':sha(value['portrait']),'pairReady':True,'pixelPipelineProof':value['pixelPipelineProof'],'pixelPostprocess':value['pixelPostprocess']}
   result=self.finalize(SimpleNamespace(selection_port=port,original_parent_generation_id=guard['generationId']),{**record,'generationRequest':record.get('generationRequest',record['request'])},draft,artifacts,value['portrait'])
   state=json.loads((artifacts/'pair-publication.json').read_text())
   saved=port.publish_portrait(state['plan'],c.request_id,record.get('generationRequest',record['request'])['characterRef']['npcId']);canonical=saved.get('publicationReceipt')
   if not isinstance(canonical,dict)or canonical.get('status')!='committed'or canonical.get('inputHash')!=saved.get('inputHash')or canonical.get('resultMetadata',{}).get('scopedPortrait',{}).get('requestId')!=c.request_id:raise ValueError('PAIR_CANONICAL_RECEIPT_REQUIRED')
  if result.get('sourceApplied')is not True or result.get('publicationScope')!='registered-npc-portrait-pair'or result.get('publication',{}).get('verified')is not True or result.get('publication',{}).get('authority')!='filex'or result.get('portraitAssetId')!=sha(value['portrait'])+'.png'or result.get('pixelAssetId')!=sha(value['pixel'])+'.png':raise ValueError('PAIR_CANONICAL_PUBLICATION_UNVERIFIED')
  receipt={'requestId':c.request_id,'attemptId':c.attempt_id,'fencingToken':c.fencing_token,'packetHash':digest(packet),'parentReceipt':result,'canonicalReceipt':canonical}
  raw=json.dumps(receipt,sort_keys=True).encode();save(folder/'receipt.json',raw)
  import os
  with (folder/'receipt.json').open('rb')as f:os.fsync(f.fileno())
  fd=os.open(folder,os.O_RDONLY)
  try:os.fsync(fd)
  finally:os.close(fd)
  return receipt
 def readback(self,claim,packet_hash):
  folder=self.root/digest([claim['request_id'],claim['attempt_id']]);path=folder/'receipt.json'
  if folder.is_symlink()or path.is_symlink()or not path.is_file():raise ValueError('PDS_PAIR_RECEIPT_NOT_READY')
  value=json.loads(path.read_text())
  if value.get('packetHash')!=packet_hash or any(value.get(k)!=claim.get(v)for k,v in (('requestId','request_id'),('attemptId','attempt_id'),('fencingToken','fencing_token'))):raise ValueError('PDS_PAIR_RECEIPT_SCOPE')
  binding=self.binding_reader(claim,packet_hash)
  if binding['record']!=claim['parameters']:raise ValueError('PDS_PAIR_RECORD_CHANGED')
  canonical=self.assets(binding['parentGuard'],claim['request_id']).publication_receipt(value['canonicalReceipt']['requestId'])
  if canonical!=value['canonicalReceipt']:raise ValueError('PDS_PAIR_CANONICAL_READBACK_CHANGED')
  return value

 def _retire_inputs(self,key,claim,packet,files,manifest_name='pair-manifest.json'):
  """Recoverable, attempt-local cache retirement after canonical readback."""
  import os
  folder=self.root/key;retired=self.root/'.retired';retired.mkdir(exist_ok=True)
  if retired.is_symlink()or retired.resolve().parent!=self.root:raise ValueError('PDS_PAIR_RETIRE_PATH')
  target=retired/key;target.mkdir(exist_ok=True)
  if target.is_symlink()or target.resolve().parent!=retired:raise ValueError('PDS_PAIR_RETIRE_PATH')
  expected={'claim.json':json.dumps(claim,sort_keys=True).encode(),'artifacts/'+manifest_name:json.dumps(packet['manifest'],sort_keys=True).encode(),**{'artifacts/'+k:v for k,v in files.items()}}
  for name,raw in expected.items():
   source=folder/name;destination=target/name;destination.parent.mkdir(exist_ok=True)
   if source.is_symlink()or destination.is_symlink()or destination.parent.is_symlink():raise ValueError('PDS_PAIR_RETIRE_PATH')
   if source.read_bytes()!=raw:raise ValueError('PDS_PAIR_RETIRE_CHANGED')
   if destination.exists():
    if destination.read_bytes()!=raw:raise ValueError('PDS_PAIR_RETIRED_CONFLICT')
   else:os.link(source,destination,follow_symlinks=False)
   # The exact bytes remain recoverable at destination; never remove receipts.
   source.unlink()
  for p in (folder,folder/'artifacts',target,target/'artifacts',retired):
   fd=os.open(p,os.O_RDONLY)
   try:os.fsync(fd)
   finally:os.close(fd)
