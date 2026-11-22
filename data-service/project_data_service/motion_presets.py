"""PDS mediated Filex motion references. No local path fallback or model prompt override."""
import base64,copy,hashlib,json,re,urllib.parse
from pathlib import Path
from .filex_read import require,private_read,digest
from .filex_cas_transport import FilexCASTransport
PREFIX='canonical-files/binari/motion-presets/'
CONFIG=Path.home()/'Library/Application Support/NPCGen/pds-filex-motion-presets-cas.json'
ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')
DIRECTION_ORDER=('n','ne','e','se','s','sw','w','nw')
DIRECTIONS=set(DIRECTION_ORDER)
WORKFLOW_CAS_TIMEOUT=1.5
def factory():
 try:c=json.loads(private_read(CONFIG))
 except FileNotFoundError:raise ValueError('MOTION_PRESETS_FILEX_NOT_CONFIGURED')from None
 return FilexCASTransport(c)
def validate(p):
 require(isinstance(p,dict)and set(p)-{'variant'}=={'schemaVersion','presetId','posePreset','label','policy','directions'},'MOTION_PRESET_SCHEMA')
 if 'variant'in p:
  v=p['variant'];require(type(v)is dict and set(v)=={'kind','sourcePresetId','sourcePresetHash'}and v['kind']=='2d'
   and type(v['sourcePresetId'])is str and ID.fullmatch(v['sourcePresetId'])and v['sourcePresetId']!=p['presetId']
   and type(v['sourcePresetHash'])is str and re.fullmatch('[a-f0-9]{64}',v['sourcePresetHash']),'MOTION_PRESET_VARIANT')
 require(p['schemaVersion']=='ngs.motion-preset.v1'and ID.fullmatch(str(p['presetId']))and ID.fullmatch(str(p['posePreset']))and isinstance(p['label'],str)and bool(p['label']),'MOTION_PRESET_ID')
 policy=p['policy'];require(isinstance(policy,dict)and set(policy)=={'framingPolicy','equipmentPolicy','motionInstruction'},'MOTION_PRESET_POLICY')
 require(isinstance(policy['framingPolicy'],dict)and isinstance(policy['equipmentPolicy'],dict)and isinstance(policy['motionInstruction'],str)and 0<len(policy['motionInstruction'])<=4000,'MOTION_PRESET_POLICY')
 frame=policy['framingPolicy'];equipment=policy['equipmentPolicy']
 require(set(frame)=={'schemaVersion','maxHeightRatio','maxWidthRatio','offsetY','allowUpscale'}and frame['schemaVersion']=='ngs.animation-motion-safe-frame.v1'and all(type(frame[k])in(int,float)and 0<frame[k]<=1 for k in ('maxHeightRatio','maxWidthRatio'))and type(frame['offsetY'])in(int,float)and -1<=frame['offsetY']<=1 and frame['allowUpscale']is False,'MOTION_PRESET_FRAMING')
 require(set(equipment)=={'heldItems','weaponState','excludedItems'}and all(isinstance(equipment[k],str)and len(equipment[k])<=1000 for k in ('heldItems','weaponState'))and isinstance(equipment['excludedItems'],list)and len(equipment['excludedItems'])<=64 and all(isinstance(x,str)and len(x)<=200 for x in equipment['excludedItems']),'MOTION_PRESET_EQUIPMENT')
 rows=p['directions'];require(isinstance(rows,list)and len(rows)==8 and {x.get('direction')for x in rows if isinstance(x,dict)}==DIRECTIONS,'MOTION_PRESET_DIRECTIONS')
 for v in rows:
  require(set(v)-{'width','height','durationSeconds'}=={'direction','assetId','sha256','bytes','frameCount','fps','cycleCount'},'MOTION_PRESET_DIRECTION_SCHEMA')
  require(all(type(v[k])is int and 1<=v[k]<=8192 for k in ('width','height')if k in v)and ('durationSeconds'not in v or type(v['durationSeconds'])in(int,float)and 0<v['durationSeconds']<=600),'MOTION_PRESET_VIDEO_METADATA')
  require(re.fullmatch('[a-f0-9]{64}',str(v['sha256']))and v['assetId']==v['sha256']+'.mp4'and type(v['bytes'])is int and v['bytes']>12,'MOTION_PRESET_ASSET')
  require(type(v['frameCount'])is int and v['frameCount']>1 and type(v['fps'])in(int,float)and 0<v['fps']<=120 and v['cycleCount']==1,'MOTION_PRESET_TIMING')
 return p
class MotionPresets:
 def __init__(self,transport_factory=factory):self.factory=transport_factory
 def snapshot(self):
  with self.factory()as t:
   cap=t.capabilities();require(cap.get('namespace')==PREFIX and cap.get('lockedBatch')is True,'MOTION_PRESET_SCOPED_WRITER_REQUIRED');s=t.snapshot()
  hashes={k:digest(v)for k,v in s['documents'].items()};require(hashes==s['hashes']and digest(hashes)==s['sourceVersion'],'MOTION_PRESET_SNAPSHOT_HASH')
  for key,p in s['documents'].items():validate(p);require(key=='presets/'+p['presetId']+'.json','MOTION_PRESET_BINDING')
  return s
 def list(self):
  s=self.snapshot();return {'schemaVersion':'ngs.motion-presets.v2','sourceVersion':s['sourceVersion'],'presets':[{**p,'presetHash':digest(p)}for p in s['documents'].values()]}
 def selected_asset_sets(self,selection):
  """Resolve all frozen selections from one authenticated catalog snapshot."""
  docs=self.snapshot()['documents']
  selected={}
  for pick in selection:
   pid,pose=pick['presetId'],pick['posePreset']
   p=docs.get('presets/'+pid+'.json')
   require(p is not None and (p['presetId'],p['posePreset'])==(pid,pose),'MOTION_PRESET_SELECTION_SCOPE')
   hashes={row['direction']:row['sha256'] for row in p['directions']}
   fingerprint=digest(['ngs.motion-assets.v1',pid,pose,[[d,hashes[d]] for d in DIRECTION_ORDER]])
   require(fingerprint==pick['assetSetSha256'],'MOTION_PRESET_SELECTION_CHANGED')
   selected[pid]=(digest(p),hashes)
  return selected
 def get(self,preset_id,direction,*,expected_preset_hash=None,max_asset_bytes=None):
  require(ID.fullmatch(preset_id)and direction in DIRECTIONS,'MOTION_PRESET_SELECTOR')
  s=self.snapshot();p=s['documents'].get('presets/'+preset_id+'.json');require(p is not None,'MOTION_PRESET_NOT_FOUND');row=next(x for x in p['directions']if x['direction']==direction)
  require((expected_preset_hash is None or digest(p)==expected_preset_hash)
   and(max_asset_bytes is None or row['bytes']<=max_asset_bytes),'MOTION_PRESET_ASSET_BASIS')
  with self.factory()as t:a=t.call('GET','/v1/asset?'+urllib.parse.urlencode({'id':row['assetId']}))
  raw=base64.b64decode(a['base64'],validate=True);require(len(raw)==row['bytes']and hashlib.sha256(raw).hexdigest()==row['sha256']and raw[4:8]==b'ftyp','MOTION_PRESET_ASSET_MISMATCH')
  return {**{k:v for k,v in p.items()if k!='directions'},**row,'sourceVersion':s['sourceVersion'],'presetHash':digest(p),'base64':a['base64']}
 def publish_2d(self,request_id,source,variant,asset_read,guarded_commit):
  # Scoped workflow entry, not exposed through the general registration API.
  # The catalog becomes visible only in Filex's locked batch; assets use
  # immutable hash names and are uploaded/read-verified individually.
  p=validate(copy.deepcopy(variant)); source=validate(source); source_hash=digest(source)
  require('variant'not in source and p.get('variant')=={'kind':'2d','sourcePresetId':source['presetId'],
   'sourcePresetHash':source_hash}and p['posePreset']==source['posePreset']and p['policy']==source['policy'],
   'MOTION_PRESET_VARIANT_BINDING')
  require(type(request_id)is str and re.fullmatch(r'request\.official\.[a-f0-9]{64}',request_id),
   'MOTION_PRESET_VARIANT_REQUEST')
  original={v['direction']:v for v in source['directions']}
  timing={'frameCount','fps','cycleCount','durationSeconds'}
  require(all(v['sha256']!=original[v['direction']]['sha256'] and
   {k:v[k]for k in timing&v.keys()}=={k:original[v['direction']][k]for k in timing&original[v['direction']].keys()}
   for v in p['directions']),'MOTION_PRESET_VARIANT_TIMING')
  key='presets/'+p['presetId']+'.json';sk='presets/'+source['presetId']+'.json';ih=digest([source_hash,p])
  basis={PREFIX+sk:source_hash,PREFIX+key:None}
  files=[{'path':PREFIX+key,'body':p,'expectedHash':None}]
  s=self.snapshot()
  present=s['documents'].get(key)
  require(s['documents'].get(sk)==source,'MOTION_PRESET_SOURCE_CHANGED')
  def receipt(t):
   old=t.receipt(request_id)
   if old is not None:
    require(old.get('requestId')==request_id and old.get('status')=='committed' and old.get('inputHash')==ih
     and old.get('payloadHash')==digest({'files':files,'readSet':basis})
     and old.get('hashes')=={PREFIX+key:digest(p)},'MOTION_PRESET_VARIANT_RECEIPT')
   return old
  with self.factory()as t:
   old=receipt(t)
   require((old is not None and present==p)or(old is None and present is None),'MOTION_PRESET_VARIANT_EXISTS')
   for row in p['directions']:
    aid=row['assetId'];raw=asset_read(row['direction'])
    require(len(raw)==row['bytes']and hashlib.sha256(raw).hexdigest()==row['sha256']and raw[4:8]==b'ftyp','MOTION_PRESET_ASSET_MISMATCH')
    if old is None:t.call('POST','/v1/asset',{'assetId':aid,'base64':base64.b64encode(raw).decode()})
    sealed=t.call('GET','/v1/asset?'+urllib.parse.urlencode({'id':aid}))
    require(sealed['assetId']==aid and sealed['sha256']==row['sha256']and sealed['bytes']==row['bytes']
     and base64.b64decode(sealed['base64'],validate=True)==raw,'MOTION_PRESET_ASSET_READBACK')
   if old is None:
    # Keep the fresh authorization fence only across this small catalog CAS;
    # asset bytes/upload/readback never hold the Planner reader transaction.
    with guarded_commit():
     t.call('POST','/v1/batch',{'requestId':request_id,'inputHash':ih,'files':files,'readSet':basis},
            timeout=WORKFLOW_CAS_TIMEOUT)
   require(receipt(t)is not None,'MOTION_PRESET_VARIANT_RECEIPT')
  check=self.snapshot()
  require(check['documents'].get(key)==p,'MOTION_PRESET_READBACK')
  return PREFIX+key
 def register(self,request):
  require(isinstance(request,dict)and set(request)=={'requestId','expectedHash','preset','assets'},'MOTION_PRESET_REGISTER_SCHEMA')
  p=validate(copy.deepcopy(request['preset']));require('variant'not in p,'MOTION_PRESET_VARIANT_WORKFLOW_REQUIRED');key='presets/'+p['presetId']+'.json';ih=digest(request)
  require(isinstance(request['requestId'],str)and re.fullmatch(r'[A-Za-z0-9._:-]{8,180}',request['requestId']),'MOTION_PRESET_REQUEST_ID')
  assets=request['assets'];require(isinstance(assets,dict)and set(assets)=={v['assetId']for v in p['directions']},'MOTION_PRESET_REGISTER_ASSETS')
  decoded={}
  for row in p['directions']:
   raw=base64.b64decode(assets[row['assetId']],validate=True);require(len(raw)==row['bytes']and hashlib.sha256(raw).hexdigest()==row['sha256']and raw[4:8]==b'ftyp','MOTION_PRESET_ASSET_MISMATCH');decoded[row['assetId']]=raw
  s=self.snapshot();actual=s['hashes'].get(key)
  require('variant'not in s['documents'].get(key,{}),'MOTION_PRESET_VARIANT_WORKFLOW_REQUIRED')
  with self.factory()as t:
   old=t.receipt(request['requestId'])
   if old:
    require(old['inputHash']==ih,'MOTION_PRESET_REQUEST_CONFLICT')
    if old.get('status')=='committed':
     require(s['documents'].get(key)==p,'MOTION_PRESET_PRIOR_RESULT_SUPERSEDED')
     return {**old,'verified':True,'presetId':p['presetId'],'presetHash':digest(p),'sourceVersion':s['sourceVersion']}
   require(actual==request['expectedHash'],'MOTION_PRESET_SOURCE_CHANGED')
   for aid,raw in decoded.items():t.call('POST','/v1/asset',{'assetId':aid,'base64':base64.b64encode(raw).decode()})
   result=t.call('POST','/v1/batch',{'requestId':request['requestId'],'inputHash':ih,'files':[{'path':PREFIX+key,'body':p,'expectedHash':actual}],'readSet':{PREFIX+key:actual}})
  check=self.snapshot();require(check['documents'].get(key)==p,'MOTION_PRESET_READBACK')
  return {**result,'verified':True,'presetId':p['presetId'],'presetHash':digest(p),'sourceVersion':check['sourceVersion']}
