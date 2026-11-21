"""Opt-in composition; no credentials are created or exported to a worker."""
import copy,hashlib,importlib,json,re
from contextlib import contextmanager
from pathlib import Path
from .filex_read import private_read,require,digest
from .npc_ownership import GUARD
from .portrait_pair_direct import DirectPortraitPairs
from .portrait_pair_processor import PortraitPairProcessor

@contextmanager
def owned_scope(guard,rid):
 require(set(guard)=={'npcId','generationId','requestId','leaseToken'},'PAIR_SERVER_GUARD')
 token=GUARD.set({**guard,'stageRequestId':rid})
 try:yield
 finally:GUARD.reset(token)

class Assets:
 def __init__(self,registrar,guard,rid):self.registrar,self.guard,self.rid=registrar,guard,rid
 def register(self,raw,project,run_id,provenance):
  require(project=='binari'and run_id in (self.rid,self.rid+'.pixel'),'PAIR_ASSET_SCOPE')
  meta={'runId':run_id,'projectId':'ProjectBS','sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw),'provenance':provenance}
  with owned_scope(self.guard,self.rid):r=self.registrar.register(meta,raw)
  require(r['asset']['id']=='asset.'+hashlib.sha256(('binari:'+run_id).encode()).hexdigest()[:48],'PAIR_ASSET_ID')
  return r['asset']
 def verify(self,asset,output):
  r=self.registrar.get(asset['id']);require(r is not None and r['asset']==asset and asset['sha256']==output['sha256']and asset['size']==output['size'],'PAIR_ASSET_READBACK');return True
 def publication_receipt(self,cid):
  require(isinstance(cid,str)and re.fullmatch(r'npc\.portrait\.[a-f0-9]{64}',cid),'PAIR_COMMIT_ID')
  with self.registrar.source.store.factory()as t:return t.receipt(cid)

class Selection:
 def __init__(self,source,planning=None):self.source,self.planning=source,planning
 def current(self):return self.source.current()
 def revision(self,n):return self.source.revision(n)
 def portrait_context(self,current,npc,pose):
  require(self.planning is not None,'PAIR_PLAN_CONTEXT_UNAVAILABLE')
  catalog=self.planning.catalog('binari',npc,'npc-portrait')
  require(catalog.get('npcRevision')==current['revision'],'PAIR_PLAN_CONTEXT_CHANGED')
  return self.planning.read({'projectId':'binari','npcId':npc,'npcRevision':current['revision'],
      'sourceVersion':catalog['sourceVersion'],'posePreset':pose,
      'categories':['common.portrait-rendering','unique.npc-identity','unique.npc-design']})
 def read_portrait(self,aid):
  require(isinstance(aid,str)and re.fullmatch(r'[a-f0-9]{64}\.png',aid),'PAIR_REFERENCE_ID')
  raw=self.source.asset_get(aid);require(hashlib.sha256(raw).hexdigest()+'.png'==aid,'PAIR_REFERENCE_HASH');return raw
 def register_portrait(self,raw):
  from .media_validation import png
  png(raw);aid=hashlib.sha256(raw).hexdigest()+'.png';self.source.asset_put(aid,raw)
  require(self.read_portrait(aid)==raw,'PAIR_UPLOAD_READBACK');return aid
 def publish_portrait(self,plan,rid,npc):
  from .npc_scoped_portrait import publish
  rows=[n for n in plan['document']['characters']if n['id']==npc];require(len(rows)==1,'PAIR_TARGET')
  payload={'requestId':rid,'npcId':npc,'posePreset':plan.get('posePreset'),'expectedRevision':plan['expectedRevision'],'beforeDocumentHash':plan['beforeDocumentHash'],'documentHash':plan['documentHash'],'note':plan['note'],'patch':{k:rows[0][k]for k in ('portraits','activePortraitId','portraitSets')if k in rows[0]}}
  payload['inputHash']=digest(payload);r=publish(self.source,payload)
  return {**r,'document':copy.deepcopy(plan['document'])}

MODULES=('local.services.npc_workflow.portrait_pairs','local.services.portrait.png_alpha','local.services.npc_workflow.portrait_pose_stage','workers.resident.deployment.portrait_pair_service')
def configured(path,*,media,registrar,planning=None):
 c=json.loads(private_read(path));require(set(c)=={'schemaVersion','enabled','stagingRoot','sourcePins'}and c['schemaVersion']=='pds.direct-portrait-pair.v1'and c['enabled']is True,'PAIR_CONFIG')
 require(media is not None and registrar is not None,'PAIR_EXISTING_SERVICES_REQUIRED')
 require(set(c['sourcePins'])==set(MODULES),'PAIR_SOURCE_PINS')
 modules=[]
 for name in MODULES:
  pin=c['sourcePins'][name];require(set(pin)=={'path','sha256'},'PAIR_SOURCE_PIN')
  p=Path(pin['path']);require(p.is_absolute()and p.resolve()==p and not p.is_symlink()and hashlib.sha256(p.read_bytes()).hexdigest()==pin['sha256'],'PAIR_SOURCE_HASH')
  m=importlib.import_module(name);require(Path(m.__file__).resolve()==p,'PAIR_IMPORTED_SOURCE');modules.append(m)
 pairs,alpha,stage,validation=modules
 def binding(claim,h):return service.reservation(claim,h)
 processor=PortraitPairProcessor(c['stagingRoot'],pairs=pairs,validate_alpha=alpha.validate_stage_output,finalize_pair=stage.finalize_pair,validate_bundle=validation.validate_pair_bundle,assets=lambda g,r:Assets(registrar,g,r),selection=lambda g,r:Selection(registrar.source,planning),scope=owned_scope,reference_asset=lambda g,a:Selection(registrar.source).read_portrait(a),binding_reader=binding)
 service=DirectPortraitPairs(media.authority,processor,processor.readback,media.authenticated)
 service.guard_ingress=True
 # Existing health may attest only this actually wired and source-pinned port.
 if all(callable(getattr(planning,name,None)) for name in ('catalog','read')):
  from local.services.npc_workflow import v5_portrait_basis
  if all(callable(getattr(v5_portrait_basis,name,None)) for name in ('verify_inputs','scoped_plan','verify_saved','published')):
   service.representative_first_source=c['sourcePins'][MODULES[2]]['sha256']
 return service
