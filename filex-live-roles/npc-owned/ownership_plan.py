"""Read-only PDS ownership planner. Never treats trace references as current slots."""
import hashlib,json,re
import native_pixel_direction as directional
ASSET=re.compile(r'^[0-9a-f]{64}\.(png|jpg|webp|mp4)$')
SAFE=re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def plan(character_id,documents):
 if not SAFE.fullmatch(character_id):raise ValueError('owner')
 slots={}
 def add(key,aid):
  if not aid:return
  if not isinstance(aid,str)or not ASSET.fullmatch(aid):raise ValueError('asset id')
  if any(not SAFE.fullmatch(p)for p in key.split('/')):raise ValueError('slot')
  path='assets/'+key+'.'+aid.rsplit('.',1)[1]
  if path in slots and slots[path]['assetId']!=aid:raise ValueError('slot collision')
  slots[path]={'assetId':aid,'sha256':aid.split('.')[0]}
 meta=documents['metadata.json']
 for pose,pair in meta.get('portraitSets',{}).items():
  for key,kind in [('portraitAssetId','portrait'),('pixelAssetId','pixel')]:add('portraits/'+pose+'/'+kind,pair.get(key))
  pipeline=pair.get('pixelProvenance',{}).get('pipeline',{})
  raw=pipeline.get('pixelSourceSha256')
  explicit=pair.get('pixelSourceAssetId')
  if explicit and raw and explicit!=raw+'.png':raise ValueError('pixel source lineage')
  if explicit or raw:add('portraits/'+pose+'/pixel-source',explicit or raw+'.png')
 for path,obj in documents.items():
  stem=path.removesuffix('.json')
  if isinstance(obj,dict) and obj.get('schemaVersion') in (directional.SCHEMA,directional.WORKFLOW_SCHEMA) and not path.startswith(directional.NAMESPACE):
   raise ValueError('direction pixel scope')
  if path.startswith('pose-views/'):
   add(stem+'/image',obj.get('assetId'));add(stem+'/source',obj.get('sourceArtifactId'))
  elif path.startswith('animations/') or path.startswith('animation-experiments/'):
   add(stem+'/video',obj.get('assetId'))
  elif path.startswith('animation-alpha/'):
   if obj.get('schemaVersion')!='ngs.animation-alpha-output.v1':raise ValueError('alpha sequence schema')
   frames=obj.get('frames')
   if not isinstance(frames,list)or type(obj.get('frameCount'))is not int or len(frames)!=obj['frameCount']or not frames:raise ValueError('alpha sequence count')
   for i,frame in enumerate(frames):
    if not isinstance(frame,dict)or type(frame.get('index'))is not int or frame['index']!=i:raise ValueError('alpha sequence order')
    for field,slot in (('rawAssetId','source'),('alphaAssetId','alpha')):
     aid=frame.get(field)
     if not isinstance(aid,str)or not re.fullmatch('[0-9a-f]{64}\\.png',aid):raise ValueError('alpha sequence asset')
     if field=='alphaAssetId':add(stem+'/'+slot+'-'+str(i),aid) # raw remains owned by its sequence manifest
  elif path.startswith('animation-sequences/'):
   # Explicit raw slots; not a video or a completed processed frame set.
   if obj.get('schemaVersion')!='ngs.animation-native-png-sequence.v1':raise ValueError('raw sequence schema')
   frames=obj.get('frames')
   if not isinstance(frames,list)or type(obj.get('frameCount'))is not int or len(frames)!=obj['frameCount']or not frames:raise ValueError('raw sequence count')
   for i,frame in enumerate(frames):
    if not isinstance(frame,dict)or type(frame.get('index'))is not int or frame['index']!=i:raise ValueError('raw sequence order')
    aid=frame.get('assetId')
    if not isinstance(aid,str)or not re.fullmatch('[0-9a-f]{64}\\.png',aid)or frame.get('sha256')!=aid[:-4]or frame.get('mimeType')!='image/png':raise ValueError('raw sequence asset')
    add(stem+'/raw-'+str(i),aid)
  elif path.startswith(directional.NAMESPACE):
   for i,frame in enumerate(directional.validate(character_id,path,obj,workflow=obj.get('schemaVersion')==directional.WORKFLOW_SCHEMA)):
    add(stem+'/pixel-'+str(i),frame['assetId'])
  elif path.startswith('animation-processed/'):
   # Native processed pixels own independent slots. Native raw/alpha inputs
   # already have their own immutable asset slots and remain lineage refs.
   if obj.get('schemaVersion')!='ngs.animation-pixel-set-candidate.v1':raise ValueError('processed sequence schema')
   directions=obj.get('directions')
   if not isinstance(directions,dict)or set(directions)!={'n','ne','e','se','s','sw','w','nw'}:raise ValueError('processed sequence directions')
   for direction,row in directions.items():
    frames=row.get('frames')if isinstance(row,dict)else None
    if not isinstance(frames,list)or not frames or type(row.get('frameCount'))is not int or len(frames)!=row['frameCount']:raise ValueError('processed sequence count')
    for i,frame in enumerate(frames):
     if not isinstance(frame,dict)or type(frame.get('index'))is not int or frame['index']!=i:raise ValueError('processed sequence order')
     if any(not isinstance(frame.get(k),str)or not re.fullmatch('[0-9a-f]{64}\\.png',frame[k])for k in ('assetId','rawAssetId','alphaAssetId')):raise ValueError('processed sequence asset')
     add(stem+'/'+direction+'/pixel-'+str(i),frame['assetId'])
  elif path.startswith('animation-frames/'):
   if obj.get('schemaVersion')=='ngs.animation-pixel-set-candidate.v1':raise ValueError('native processed scope')
   for direction,d in obj.get('directions',{}).items():
    for frame in d['frames']:
     i=frame['index']
     if type(i)is not int or i<0:raise ValueError('frame index')
     add(stem+'/'+direction+'/pixel-'+str(i),frame.get('assetId'))
     # Raw source belongs to the full frameset, not duplicated in half output.
     rawstem=stem.removesuffix('/half')
     add(rawstem+'/'+direction+'/rgba-'+str(i),frame.get('rawAssetId'))
     add(rawstem+'/'+direction+'/alpha-'+str(i),frame.get('alphaAssetId'))
 refs=set(re.findall(r'[0-9a-f]{64}\.(?:png|jpg|webp|mp4)',json.dumps(documents)))
 owned={x['assetId']for x in slots.values()}
 result={'schemaVersion':'npc-owned-assets.plan.v1','characterId':character_id,'basisHash':digest(documents),'slots':slots,'nonSlotReferences':sorted(refs-owned),'policy':'Non-slot references require explicit provenance/dependency classification; never silently discard.'}
 result['planHash']=digest(result)
 return result
