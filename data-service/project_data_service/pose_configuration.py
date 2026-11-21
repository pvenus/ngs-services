"""Server-owned pose domain ports. No worker paths or new authentication."""
import re
from .filex_read import require
from .portrait_pair_configuration import Selection
from .npc_pose_views import PoseViewPublisher
class PoseAPI:
 def __init__(self,domains):self.domains=domains
 def __call__(self,method,path,payload=None,raw=False):
  if raw:
   require(method=='GET'and re.fullmatch(r'/assets/portraits/[a-f0-9]{64}\.png',path),'POSE_RAW_PATH')
   return Selection(self.domains.npcs).read_portrait(path.rsplit('/',1)[1])
  if method=='GET'and path=='/api/npcs':return self.domains.npcs.current()
  if method=='GET'and path=='/v1/npcs/files':return self.domains.npc_files()
  # Build in the current request thread, never cache a SQLite connection.
  if method=='POST'and path=='/v1/npcs/pose-views/publish':return PoseViewPublisher(self.domains).publish(payload)
  if method=='GET'and re.fullmatch(r'/v1/npcs/pose-views/requests/[A-Za-z0-9._:-]{8,180}',path):return PoseViewPublisher(self.domains).receipt(path.rsplit('/',1)[1])
  raise ValueError('POSE_DOMAIN_PATH_DENIED')

class PoseClient:
 def __init__(self,domains,planning,verify,guard,rid):
  self.domain_api=PoseAPI(domains);self.domains,self.planning,self.verifier,self.guard,self.rid=domains,planning,verify,guard,rid
 def api(self,method,path,payload=None,raw=False):
  from .portrait_pair_configuration import owned_scope
  with owned_scope(self.guard,self.rid):return self.domain_api(method,path,payload,raw)
 def receipt(self,rid):return self.api('GET','/v1/npcs/pose-views/requests/'+rid)
 def verify(self,wire,receipt):return self.verifier(self,wire,receipt)
 def context(self,npc,incarnation,*,posePreset):
  from .filex_read import digest
  require(npc==self.guard['npcId'],'POSE_CONTEXT_TARGET');current=self.domains.npcs.current();rows=[n for n in current['document']['characters']if n['id']==npc]
  require(len(rows)==1 and rows[0].get('creationBasis',{}).get('requestId')==incarnation,'POSE_INCARNATION_CHANGED')
  catalog=self.planning.catalog('binari',npc)
  value=self.planning.read({'projectId':'binari','npcId':npc,'npcRevision':catalog['npcRevision'],'sourceVersion':catalog['sourceVersion'],'posePreset':posePreset,'categories':['common.portrait-rendering','unique.npc-identity','unique.npc-design']})
  require(value.get('schemaVersion')=='pds-worker-context-read.v2'and value.get('characterHash')==digest(rows[0]),'POSE_CONTEXT_CHANGED');return {**value,'incarnationId':incarnation}

MODULES=('local.services.npc_workflow.portrait_pairs','local.services.pose_requests.worker','local.services.portrait.png_alpha','workers.resident.backends.pose_direction','workers.resident.deployment.portrait_pair_service','workers.resident.adapters.pose_direction_publication')
def configured(path,*,media,domains,planning):
 import json,hashlib,importlib
 from pathlib import Path
 from .filex_read import private_read
 from .portrait_pair_configuration import owned_scope
 from .pose_direct import DirectPoseDirections
 from .pose_processor import PoseProcessor
 c=json.loads(private_read(path));require(set(c)=={'schemaVersion','enabled','stagingRoot','sourcePins'}and c['schemaVersion']=='pds.direct-pose-direction.v1'and c['enabled']is True,'POSE_CONFIG')
 require(media is not None and planning is not None and domains.npcs is not None,'POSE_EXISTING_PORTS')
 require(set(c['sourcePins'])==set(MODULES),'POSE_SOURCE_PINS');modules=[]
 for name in MODULES:
  pin=c['sourcePins'][name];require(set(pin)=={'path','sha256'},'POSE_SOURCE_PIN');p=Path(pin['path'])
  require(p.is_absolute()and p.resolve()==p and not p.is_symlink()and hashlib.sha256(p.read_bytes()).hexdigest()==pin['sha256'],'POSE_SOURCE_HASH')
  m=importlib.import_module(name);require(Path(m.__file__).resolve()==p,'POSE_IMPORTED_SOURCE');modules.append(m)
 pairs,worker,alpha,backend,validation,publication=modules
 def client(g,r):return PoseClient(domains,planning,publication.PosePublicationClient.verify,g,r)
 def workflow(g,r,api):
  def scoped(method,path,payload=None,raw=False):
   with owned_scope(g,r):return api(method,path,payload,raw=raw)
  def deny(*a,**kw):raise ValueError('POSE_PDS_GENERATION_FORBIDDEN')
  return worker.PoseWorkflow(c['stagingRoot'],api=scoped,ports=deny,context_reader=client(g,r).context)
 def binding(claim,h):return service.reservation(claim,h)
 processor=PoseProcessor(c['stagingRoot'],compile_direction=backend.compile_direction,pairs=pairs,pose_worker=worker,canonical_claim=validation._canonical_claim,manifest_reader=validation._manifest,validate_alpha=alpha.validate_output_contract,client=client,workflow=workflow,reference_asset=lambda g,a:Selection(domains.npcs).read_portrait(a),binding_reader=binding)
 service=DirectPoseDirections(media.authority,processor,processor.readback,media.authenticated);service.guard_ingress=True;return service
