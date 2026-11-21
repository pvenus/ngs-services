"""Read-only PDS ownership planner. Never treats trace references as current slots."""
import hashlib,json,re
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
  if path.startswith('pose-views/'):
   add(stem+'/image',obj.get('assetId'));add(stem+'/source',obj.get('sourceArtifactId'))
  elif path.startswith('animations/') or path.startswith('animation-experiments/'):
   add(stem+'/video',obj.get('assetId'))
  elif path.startswith('animation-frames/'):
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
