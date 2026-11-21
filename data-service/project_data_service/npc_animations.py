"""One formal animation publication: scoped current slot + verified MP4 bytes."""
import base64,copy,hashlib,json,re
from .filex_read import digest,require
from .filex_npc_authority import FilexCurrentNPC
from .filex_npc_files import ANIMATION_KEY
ASSET=re.compile(r'[0-9a-f]{64}\.mp4\Z')
def validate_slots(character,asset_reader=None):
 slots=character.get('animationSlots',{})
 require(isinstance(slots,dict),'NPC_ANIMATION_SLOTS_INVALID')
 for key,v in slots.items():
  require(isinstance(key,str)and ANIMATION_KEY.fullmatch(key)and isinstance(v,dict),'NPC_ANIMATION_SLOT_INVALID')
  require(v.get('schemaVersion')=='ngs.npc-animation-slot.v1'and key=='/'.join(str(v.get(k,''))for k in ('posePreset','presetId','direction')),'NPC_ANIMATION_SLOT_BINDING')
  require(ASSET.fullmatch(str(v.get('assetId','')))and v.get('mimeType')=='video/mp4'and type(v.get('bytes'))is int and v['bytes']>12,'NPC_ANIMATION_ASSET_INVALID')
  require(v.get('referenceType')in ('pixel','portrait')and re.fullmatch(r'[0-9a-f]{64}\.(png|jpg|webp)',str(v.get('referenceAssetId',''))),'NPC_ANIMATION_REFERENCE_INVALID')
  require(type(v.get('frameCount'))is int and v['frameCount']>0 and type(v.get('fps'))in (int,float)and 0<v['fps']<=120 and type(v.get('cycleCount'))is int and v['cycleCount']==1,'NPC_ANIMATION_TIMING_INVALID')
  require(re.fullmatch('[0-9a-f]{64}',str(v.get('presetHash','')))and isinstance(v.get('requestId'),str)and v['requestId'],'NPC_ANIMATION_PROVENANCE_INVALID')
  if asset_reader:
   data=asset_reader(v['assetId']);require(len(data)==v['bytes']and hashlib.sha256(data).hexdigest()==v['assetId'][:-4]and data[4:8]==b'ftyp','NPC_ANIMATION_ASSET_MISMATCH')
 return character

class AnimationPublisher:
 def __init__(self,domains):
  self.domains=domains;self.source=domains.npcs;self.db=domains.npc_receipts.connection
  with domains.lock,self.db:self.db.execute('CREATE TABLE IF NOT EXISTS npc_animation_receipts(request_id TEXT PRIMARY KEY,input_hash TEXT NOT NULL,receipt TEXT NOT NULL)')
 def receipt(self,key):
  with self.domains.lock:r=self.db.execute('SELECT receipt FROM npc_animation_receipts WHERE request_id=?',(key,)).fetchone()
  return json.loads(r[0])if r else None
 def publish(self,p):
  require(isinstance(p,dict)and set(p)-{'derivedReference'}=={'requestId','projectId','npcId','expectedSourceVersion','expectedCharacterHash','slot','artifact','trace'},'NPC_ANIMATION_REQUEST_INVALID')
  require(p['projectId']=='binari'and isinstance(p['requestId'],str)and re.fullmatch(r'[A-Za-z0-9._:-]{8,180}',p['requestId'])and isinstance(p['trace'],dict),'NPC_ANIMATION_REQUEST_BINDING')
  require(isinstance(self.source,FilexCurrentNPC),'NPC_FILEX_AUTHORITY_REQUIRED')
  slot=p['slot'];a=p['artifact'];require(isinstance(slot,dict)and set(slot)=={'posePreset','presetId','direction','referenceType','referenceAssetId','frameCount','fps','cycleCount','presetHash'},'NPC_ANIMATION_SLOT_INVALID')
  require(isinstance(a,dict)and set(a)=={'sha256','bytes','mimeType','base64'}and a['mimeType']=='video/mp4','NPC_ANIMATION_ARTIFACT_INVALID')
  data=base64.b64decode(a['base64'],validate=True);require(len(data)==a['bytes']and len(data)>12 and hashlib.sha256(data).hexdigest()==a['sha256']and data[4:8]==b'ftyp','NPC_ANIMATION_MP4_INVALID')
  key='/'.join(str(slot[k])for k in ('posePreset','presetId','direction'));aid=a['sha256']+'.mp4'
  value={**slot,'schemaVersion':'ngs.npc-animation-slot.v1','assetId':aid,'mimeType':'video/mp4','bytes':len(data),'requestId':p['requestId'],'trace':copy.deepcopy(p['trace'])}
  validate_slots({'animationSlots':{key:value}})
  ih=digest({**p,'artifact':{k:v for k,v in a.items()if k!='base64'}});value['inputHash']=ih
  with self.domains.lock,self.db:
   old=self.db.execute('SELECT input_hash,receipt FROM npc_animation_receipts WHERE request_id=?',(p['requestId'],)).fetchone()
   if old:
    require(old[0]==ih,'NPC_ANIMATION_IDEMPOTENCY_CONFLICT');return json.loads(old[1])
   # Registry validation precedes every new publication; completed receipts remain replayable.
   from .motion_presets import MotionPresets
   catalog=MotionPresets().list();preset=next((v for v in catalog['presets']if v['presetId']==slot['presetId']),None)
   require(preset is not None and preset['posePreset']==slot['posePreset'],'NPC_ANIMATION_PRESET_UNREGISTERED')
   direction=next((v for v in preset['directions']if v['direction']==slot['direction']),None)
   require(direction is not None and direction['sha256']==slot['presetHash']and all(direction[k]==slot[k]for k in ('frameCount','fps','cycleCount')),'NPC_ANIMATION_PRESET_CHANGED')
   current=self.source.current();document=current['document'];chars=[c for c in document['characters']if c['id']==p['npcId']];require(len(chars)==1,'NPC_ANIMATION_NPC_MISSING');character=chars[0]
   # Lost response recovery before stale-input check, never overwrite a newer slot.
   existing=character.get('animationSlots',{}).get(key)
   if existing!=value:
    # New writes are pixel pose-view only. Exact old receipts/files remain recoverable.
    legacy=legacy_reference_allowed(p)
    require(slot['referenceType']=='pixel'or legacy,'NPC_ANIMATION_PIXEL_REFERENCE_REQUIRED')
    require(p.get('derivedReference')is None or legacy,'NPC_ANIMATION_DIRECTION_PIXEL_REQUIRED')
    require(current['sourceVersion']==p['expectedSourceVersion']and digest(character)==p['expectedCharacterHash'],'NPC_ANIMATION_SOURCE_CHANGED')
    pair=character.get('portraitSets',{}).get(slot['posePreset'],{});ref=pair.get('pixelAssetId'if slot['referenceType']=='pixel'else'portraitAssetId')
    self.source.asset_get(ref)
    derived=p.get('derivedReference')
    if derived is None:
     view=character.get('poseViews',{}).get(slot['posePreset'],{}).get(slot['referenceType'],{}).get(slot['direction'],{})
     if not legacy or p['trace'].get('poseReference')is not None:validate_pose_reference(p['trace'].get('poseReference'),view,slot,ref)
     view_matches=isinstance(view,dict)and view.get('assetId')==slot['referenceAssetId']and view.get('sourceAssetId')==ref
     require(view_matches or legacy and ref==slot['referenceAssetId'],'NPC_ANIMATION_REFERENCE_CHANGED')
     pixel_bytes=self.source.asset_get(slot['referenceAssetId'])
     if not legacy:
      from .project_display import validate_png
      import struct
      validate_png(pixel_bytes)
      require(struct.unpack('>II',pixel_bytes[16:24])==(384,384),'NPC_ANIMATION_PIXEL_DIMENSIONS')
    else:
     derived_bytes=validate_derived_reference(derived,slot,ref,p['requestId'])
     self.source.asset_put(slot['referenceAssetId'],derived_bytes)
    self.source.asset_put(aid,data)
    after=copy.deepcopy(document);target=next(c for c in after['characters']if c['id']==p['npcId']);target.setdefault('animationSlots',{})[key]=value
    validate_slots(target,self.source.asset_get)
    saved=self.source.save(current['revision'],'NGS animation slot '+p['requestId'],after)
    require(saved is not None,'NPC_ANIMATION_SOURCE_CHANGED')
   latest=self.source.current();target=next(c for c in latest['document']['characters']if c['id']==p['npcId']);require(target.get('animationSlots',{}).get(key)==value,'NPC_ANIMATION_READBACK_MISMATCH')
   require(self.source.asset_get(aid)==data,'NPC_ANIMATION_READBACK_MISMATCH')
   receipt={'requestId':p['requestId'],'inputHash':ih,'npcId':p['npcId'],'slotKey':key,'assetId':aid,'sha256':a['sha256'],'bytes':len(data),'mimeType':'video/mp4','revision':latest['revision'],'sourceVersion':latest['sourceVersion'],'documentHash':digest(latest['document']),'verified':True,'source':'filex','ngsReplicaPulled':False}
   self.db.execute('INSERT INTO npc_animation_receipts VALUES(?,?,?)',(p['requestId'],ih,json.dumps(receipt,ensure_ascii=False)))
   return receipt


def validate_derived_reference(v,slot,source_asset,request_id):
 require(isinstance(v,dict)and set(v)=={'schemaVersion','sourceAssetId','sha256','bytes','direction','providerProof','postprocess','promptHash','contextHash','requestId','base64'},'NPC_ANIMATION_DERIVED_SCHEMA')
 require(v['schemaVersion']=='ngs.animation-derived-reference.v1'and slot['referenceType']=='pixel'and slot['direction']in ('n','ne','nw')and v['direction']==slot['direction']and v['requestId']==request_id and v['sourceAssetId']==source_asset,'NPC_ANIMATION_DERIVED_BINDING')
 require(slot['referenceAssetId']==v['sha256']+'.png'and all(re.fullmatch('[a-f0-9]{64}',str(v[k]))for k in ('sha256','promptHash','contextHash')),'NPC_ANIMATION_DERIVED_HASH')
 raw=base64.b64decode(v['base64'],validate=True)
 require(len(raw)==v['bytes']and hashlib.sha256(raw).hexdigest()==v['sha256']and raw[:8]==b'\x89PNG\r\n\x1a\n','NPC_ANIMATION_DERIVED_BYTES')
 from .project_display import validate_png
 validate_png(raw)
 import struct
 require(len(raw)>24 and struct.unpack('>II',raw[16:24])==(384,384),'NPC_ANIMATION_DERIVED_DIMENSIONS')
 proof=v['providerProof'];post=v['postprocess']
 require(isinstance(proof,dict)and proof.get('actualToolOutput')is True and isinstance(post,dict)and post.get('profileId')=='npc.pose-pixel.384.v1'and post.get('sourceSha256')==proof.get('sha256'),'NPC_ANIMATION_DERIVED_PROVENANCE')
 return raw


def validate_pose_reference(binding,view,slot,source):
 require(isinstance(binding,dict)and isinstance(view,dict),'NPC_ANIMATION_POSE_REFERENCE_INVALID')
 require(view.get('schemaVersion')=='ngs.npc-pose-view.v1'and all(view.get(k)==slot[k]for k in ('posePreset','referenceType','direction')),'NPC_ANIMATION_POSE_REFERENCE_SLOT')
 require(view.get('sourceAssetId')==source and view.get('sourceHash')==str(source).split('.')[0],'NPC_ANIMATION_POSE_REFERENCE_STALE')
 require(view.get('assetId')==slot['referenceAssetId'],'NPC_ANIMATION_POSE_REFERENCE_ASSET')
 require(slot['referenceType']!='pixel'or(view.get('width'),view.get('height'))==(384,384),'NPC_ANIMATION_POSE_REFERENCE_DIMENSIONS')
 expected={'policy':'ngs.animation-pose-view-reference.v1','posePreset':slot['posePreset'],'referenceType':slot['referenceType'],'direction':slot['direction'],'sourceAssetId':source,'referenceAssetId':slot['referenceAssetId'],'poseViewHash':digest(view)}
 require(binding==expected,'NPC_ANIMATION_POSE_REFERENCE_CHANGED')


def legacy_reference_allowed(payload):
 """Deployment-created frozen records only; clients cannot opt into legacy policy."""
 from pathlib import Path
 from .filex_read import private_read
 path=Path.home()/'Library/Application Support/NPCGen/pds-animation-reference-legacy.json'
 try:config=json.loads(private_read(path))
 except FileNotFoundError:return False
 require(config.get('schemaVersion')=='pds-animation-reference-legacy.v1'and isinstance(config.get('requests'),dict),'NPC_ANIMATION_LEGACY_CONFIG_INVALID')
 record=config['requests'].get(payload['requestId'])
 if record is None:return False
 expected={'projectId':payload['projectId'],'npcId':payload['npcId'],'slot':payload['slot']}
 require(record==expected,'NPC_ANIMATION_LEGACY_BINDING_CHANGED')
 return True
