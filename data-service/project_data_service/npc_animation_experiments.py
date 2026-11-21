"""Isolated experimental animation publication, never production slots."""
import base64,copy,hashlib,json,re
from .npc_animations import validate_slots
from .filex_read import digest,require
from .filex_npc_authority import FilexCurrentNPC
def validate_experiments(character,asset_reader=None):
 values=character.get('animationExperiments',{})
 require(isinstance(values,dict),'NPC_ANIMATION_EXPERIMENT_INVALID')
 for key,v in values.items():
  require(key in ('eight-up-run-4x2','eight-up-run-4x2-matched-reference') and isinstance(v,dict) and v.get('experimentId')==key and v.get('schemaVersion')=='ngs.npc-animation-experiment.v1','NPC_ANIMATION_EXPERIMENT_BINDING')
  proxy={**v,'schemaVersion':'ngs.npc-animation-slot.v1','posePreset':'idle','presetId':'run','direction':'s'}
  validate_slots({'animationSlots':{'idle/run/s':proxy}},asset_reader)
 return character

class AnimationExperimentPublisher:
 def __init__(self,domains):
  self.domains=domains;self.source=domains.npcs;self.db=domains.npc_receipts.connection
  with domains.lock,self.db:self.db.execute('CREATE TABLE IF NOT EXISTS npc_animation_experiment_receipts(request_id TEXT PRIMARY KEY,input_hash TEXT NOT NULL,receipt TEXT NOT NULL)')
 def receipt(self,key):
  with self.domains.lock:r=self.db.execute('SELECT receipt FROM npc_animation_experiment_receipts WHERE request_id=?',(key,)).fetchone()
  return json.loads(r[0])if r else None
 def publish(self,p):
  require(isinstance(p,dict)and set(p)=={'requestId','projectId','npcId','expectedSourceVersion','expectedCharacterHash','experiment','artifact','trace'},'NPC_ANIMATION_REQUEST_INVALID')
  require(p['projectId']=='binari'and isinstance(p['requestId'],str)and re.fullmatch(r'[A-Za-z0-9._:-]{8,180}',p['requestId'])and isinstance(p['trace'],dict),'NPC_ANIMATION_REQUEST_BINDING')
  require(isinstance(self.source,FilexCurrentNPC),'NPC_FILEX_AUTHORITY_REQUIRED')
  slot=p['experiment'];a=p['artifact'];require(isinstance(slot,dict)and set(slot)=={'experimentId','referenceType','referenceAssetId','frameCount','fps','cycleCount','presetHash'},'NPC_ANIMATION_SLOT_INVALID')
  require(isinstance(a,dict)and set(a)=={'sha256','bytes','mimeType','base64'}and a['mimeType']=='video/mp4','NPC_ANIMATION_ARTIFACT_INVALID')
  data=base64.b64decode(a['base64'],validate=True);require(len(data)==a['bytes']and len(data)>12 and hashlib.sha256(data).hexdigest()==a['sha256']and data[4:8]==b'ftyp','NPC_ANIMATION_MP4_INVALID')
  key=slot['experimentId'];aid=a['sha256']+'.mp4'
  value={**slot,'schemaVersion':'ngs.npc-animation-experiment.v1','assetId':aid,'mimeType':'video/mp4','bytes':len(data),'requestId':p['requestId'],'trace':copy.deepcopy(p['trace'])}
  validate_experiments({'animationExperiments':{key:value}})
  ih=digest({**p,'artifact':{k:v for k,v in a.items()if k!='base64'}});value['inputHash']=ih
  with self.domains.lock,self.db:
   old=self.db.execute('SELECT input_hash,receipt FROM npc_animation_experiment_receipts WHERE request_id=?',(p['requestId'],)).fetchone()
   if old:
    require(old[0]==ih,'NPC_ANIMATION_IDEMPOTENCY_CONFLICT');return json.loads(old[1])
   current=self.source.current();document=current['document'];chars=[c for c in document['characters']if c['id']==p['npcId']];require(len(chars)==1,'NPC_ANIMATION_NPC_MISSING');character=chars[0]
   # Lost response recovery before stale-input check, never overwrite a newer slot.
   existing=character.get('animationExperiments',{}).get(key)
   if existing!=value:
    require(current['sourceVersion']==p['expectedSourceVersion']and digest(character)==p['expectedCharacterHash'],'NPC_ANIMATION_SOURCE_CHANGED')
    pair=character.get('portraitSets',{}).get('idle',{});ref=pair.get('pixelAssetId'if slot['referenceType']=='pixel'else'portraitAssetId')
    require(ref==slot['referenceAssetId'],'NPC_ANIMATION_REFERENCE_CHANGED')
    self.source.asset_get(ref)
    self.source.asset_put(aid,data)
    after=copy.deepcopy(document);target=next(c for c in after['characters']if c['id']==p['npcId']);target.setdefault('animationExperiments',{})[key]=value
    validate_experiments(target,self.source.asset_get)
    saved=self.source.save(current['revision'],'NGS animation slot '+p['requestId'],after)
    require(saved is not None,'NPC_ANIMATION_SOURCE_CHANGED')
   latest=self.source.current();target=next(c for c in latest['document']['characters']if c['id']==p['npcId']);require(target.get('animationExperiments',{}).get(key)==value,'NPC_ANIMATION_READBACK_MISMATCH')
   require(self.source.asset_get(aid)==data,'NPC_ANIMATION_READBACK_MISMATCH')
   receipt={'requestId':p['requestId'],'inputHash':ih,'npcId':p['npcId'],'experimentId':key,'assetId':aid,'sha256':a['sha256'],'bytes':len(data),'mimeType':'video/mp4','revision':latest['revision'],'sourceVersion':latest['sourceVersion'],'documentHash':digest(latest['document']),'verified':True,'source':'filex','ngsReplicaPulled':False}
   self.db.execute('INSERT INTO npc_animation_experiment_receipts VALUES(?,?,?)',(p['requestId'],ih,json.dumps(receipt,ensure_ascii=False)))
   return receipt
