"""PDS-only direction validation and original pose-slot CAS, without inference."""
import base64,hashlib,json,math,os,fcntl,tempfile
from pathlib import Path
from .portrait_pair_processor import digest,sha,PortraitPairProcessor
FILES=('source.png','output.png','provider-claim.json','result.json')

def immutable(path,raw):
 if path.is_symlink():raise ValueError('POSE_PDS_STAGE_PATH')
 if path.exists():
  if path.read_bytes()!=raw:raise ValueError('POSE_PDS_STAGE_CHANGED')
  return
 fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
 try:
  with os.fdopen(fd,'wb')as f:f.write(raw);f.flush();os.fsync(f.fileno())
  os.replace(name,path);fd=os.open(path.parent,os.O_RDONLY)
  try:os.fsync(fd)
  finally:os.close(fd)
 finally:
  if os.path.exists(name):os.unlink(name)

class PoseProcessor:
 def __init__(self,root,*,compile_direction,pairs,pose_worker,canonical_claim,manifest_reader,validate_alpha,client,workflow,reference_asset,binding_reader):
  self.root=Path(root)
  if not self.root.is_absolute()or self.root.is_symlink()or self.root.resolve()!=self.root or not self.root.is_dir():raise ValueError('POSE_PDS_STAGE_ROOT')
  if not all(callable(p)for p in (compile_direction,canonical_claim,manifest_reader,validate_alpha,client,workflow,reference_asset,binding_reader)):raise ValueError('POSE_PDS_PORTS')
  self.compile,self.pairs,self.pose_worker=compile_direction,pairs,pose_worker
  self.canonical,self.manifest,self.alpha=canonical_claim,manifest_reader,validate_alpha
  self.client,self.workflow,self.reference_asset,self.binding_reader=client,workflow,reference_asset,binding_reader
 def __call__(self,claim,packet,files,binding):
  key=digest([claim['request_id'],claim['attempt_id']]);fd=os.open(self.root/('.'+key+'.lock'),os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
  try:
   fcntl.flock(fd,fcntl.LOCK_EX);value=self.process(claim,packet,files,binding)
   if self.readback(claim,digest(packet))!=value:raise ValueError('POSE_PDS_FINAL_READBACK')
   PortraitPairProcessor._retire_inputs(self,key,claim,packet,files,manifest_name='pose-manifest.json')
   return value
  finally:os.close(fd)
 def process(self,claim,packet,files,binding):
  from workers.resident.contracts import Claim,PROFILES
  if set(packet)!={'manifest'}or set(files)!=set(FILES)or set(binding)!={'reservation','record','parentGuard'}or set(binding['record'])!={'request','state','step'}:raise ValueError('POSE_PDS_BINDING')
  v=dict(claim);v['input_refs']=tuple(v['input_refs']);c=Claim(**v);c.validate(PROFILES['image'])
  request,state,step=(binding['record'][n]for n in ('request','state','step'));guard=binding['parentGuard'];body=dict(c.parameters)
  if body.get('schemaVersion')!='ngs.pose-direction-request.v1'or body.get('parentRequestId')!=request['requestId']or body.get('parentStateHash')!=digest(state)or step!={'requestId':c.request_id,'direction':body.get('direction')}or c.request_id!=request['requestId']+'.'+step['direction']or guard.get('requestId')!=request['requestId']or guard.get('npcId')!=request['npcId']:raise ValueError('POSE_PDS_ORIGINAL_BINDING')
  folder=self.root/digest([c.request_id,c.attempt_id]);folder.mkdir(exist_ok=True)
  if folder.is_symlink()or folder.resolve().parent!=self.root:raise ValueError('POSE_PDS_STAGE_PATH')
  artifacts=folder/'artifacts';artifacts.mkdir(exist_ok=True)
  if artifacts.is_symlink():raise ValueError('POSE_PDS_STAGE_PATH')
  meta=packet['manifest'];expected={'schemaVersion':'resident.pose-direction.v1','requestId':c.request_id,'attemptId':c.attempt_id,'fencingToken':c.fencing_token,'parentRequestId':request['requestId'],'direction':step['direction'],'parentStateHash':digest(state)}
  if set(meta)!=set(expected)|{'files'}or any(meta.get(k)!=v for k,v in expected.items())or set(meta['files'])!=set(FILES):raise ValueError('POSE_PDS_MANIFEST')
  for name,raw in files.items():
   if not isinstance(raw,bytes)or meta['files'][name]!={'sha256':sha(raw),'bytes':len(raw)}:raise ValueError('POSE_PDS_FILE_HASH')
   immutable(artifacts/name,raw)
  immutable(folder/'claim.json',json.dumps(claim,sort_keys=True).encode());immutable(artifacts/'pose-manifest.json',json.dumps(meta,sort_keys=True).encode())
  refs={}
  for ref in c.input_refs:
   if not ref.startswith('pds:asset:'):raise ValueError('POSE_PDS_INPUT_REF')
   aid=ref[len('pds:asset:'):];raw=self.reference_asset(guard,aid)
   if sha(raw)+'.png'!=aid:raise ValueError('POSE_PDS_INPUT_BYTES')
   refs[aid]=raw
  def reference(ref):
   if ref['mainPortraitAssetId']not in refs:raise ValueError('POSE_PDS_REFERENCE_SCOPE')
   return refs[ref['mainPortraitAssetId']]
  native=json.loads(files['provider-claim.json']);result=json.loads(files['result.json']);manifest=self.manifest(native['referenceManifest'],folder,request['npcId'],reference_reader=reference)
  if native.get('providerRequestHash')!=digest(native['providerRequest'])or native['providerRequest'].get('metadata',{}).get('referenceManifest')!=manifest or native['providerRequest']['arguments'].get('referenced_image_paths')!=[r['localPath']for r in manifest['references']]:raise ValueError('POSE_PDS_NATIVE_REFERENCE')
  with tempfile.TemporaryDirectory(dir=folder)as tmp:
   expected_native,desc,_=self.compile(request,state,step,Path(tmp),[refs[r[len('pds:asset:'):]]for r in c.input_refs],self.pairs,self.pose_worker)
   if self.canonical(native)!=self.canonical(expected_native):raise ValueError('POSE_PDS_NATIVE_CLAIM')
  proof=result['providerProof'];raw=files['source.png'];output=files['output.png']
  if proof.get('actualToolOutput')is not True or proof.get('sha256')!=sha(raw)or any(proof.get(k)!=native[k]for k in ('index','providerClaim','providerRequestHash','requestHash')):raise ValueError('POSE_PDS_NATIVE_PROOF')
  self.alpha(raw,desc['outputContract']);normalized,post=self.pairs.postprocess_pixel(raw)if request['referenceType']=='pixel'else(raw,None)
  fields={'sha256':sha(output),'sourceSha256':sha(raw),'postprocess':post,'promptHash':digest(desc['arguments']['prompt']),'actualPrompt':desc['arguments']['prompt'],'references':native['referenceManifest'],'modelCalls':1,'technicalRetries':0}
  if output!=normalized or any(result.get(k)!=v for k,v in fields.items())or type(result.get('elapsedSeconds'))not in(int,float)or not math.isfinite(result['elapsedSeconds'])or result['elapsedSeconds']<0:raise ValueError('POSE_PDS_NORMALIZATION')
  client=self.client(guard,c.request_id);intent=folder/'intent.json'
  def api(method,path,payload=None,raw=False):
   if method=='POST':
    if path!='/v1/npcs/pose-views/publish'or payload.get('requestId')!=c.request_id:raise ValueError('POSE_PDS_PUBLISH_SCOPE')
    wire={**payload,'artifact':{k:v for k,v in payload['artifact'].items()if k!='base64'},'sourceArtifact':{k:v for k,v in payload['sourceArtifact'].items()if k!='base64'}}
    immutable(intent,json.dumps({'packetHash':digest(packet),'wire':wire},sort_keys=True).encode())
   return client.api(method,path,payload,raw)
  workflow=self.workflow(guard,c.request_id,api)
  if step['direction']in('ne','nw')and not self.pose_worker.is_regeneration(request):
   rear=workflow.published_rear(request,state,workflow.npc(request))
   if tuple(c.input_refs)[-1]!='pds:asset:'+sha(rear)+'.png':raise ValueError('POSE_REAR_REFERENCE_CHANGED')
  if intent.exists():
   stored=json.loads(intent.read_text())
   if stored['packetHash']!=digest(packet):raise ValueError('POSE_PDS_INTENT_CHANGED')
   wire=stored['wire'];receipt=client.receipt(c.request_id)
   if receipt is None:receipt=client.api('POST','/v1/npcs/pose-views/publish',{**wire,'artifact':{**wire['artifact'],'base64':base64.b64encode(output).decode()},'sourceArtifact':{**wire['sourceArtifact'],'base64':base64.b64encode(raw).decode()}})
  else:
   receipt=workflow.publish_prepared(request,state,step,result,raw,output);wire=json.loads(intent.read_text())['wire']
  client.verify(wire,receipt)
  if client.receipt(c.request_id)!=receipt:raise ValueError('POSE_PDS_READBACK_CHANGED')
  value={'requestId':c.request_id,'attemptId':c.attempt_id,'fencingToken':c.fencing_token,'packetHash':digest(packet),'sourceReceipt':receipt,'wire':wire}
  immutable(folder/'receipt.json',json.dumps(value,sort_keys=True).encode());return value
 def readback(self,claim,packet_hash):
  folder=self.root/digest([claim['request_id'],claim['attempt_id']]);path=folder/'receipt.json'
  if folder.is_symlink()or path.is_symlink()or not path.is_file():raise ValueError('POSE_PDS_RECEIPT_NOT_READY')
  value=json.loads(path.read_text())
  if value.get('packetHash')!=packet_hash or any(value.get(k)!=claim.get(v)for k,v in (('requestId','request_id'),('attemptId','attempt_id'),('fencingToken','fencing_token'))):raise ValueError('POSE_PDS_RECEIPT_SCOPE')
  binding=self.binding_reader(claim,packet_hash);client=self.client(binding['parentGuard'],claim['request_id']);client.verify(value['wire'],value['sourceReceipt'])
  if client.receipt(claim['request_id'])!=value['sourceReceipt']:raise ValueError('POSE_PDS_READBACK_CHANGED')
  return value
