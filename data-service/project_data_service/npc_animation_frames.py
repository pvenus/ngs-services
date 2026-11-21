"""Atomic full eight-direction frame set publication, preserving video originals."""
import copy, hashlib, json, re
from .filex_read import digest, require
from .filex_npc_authority import FilexCurrentNPC
from .npc_pose_views import png_artifact
DIRECTIONS=('n','ne','e','se','s','sw','w','nw')


def validate_manifest(m, artifacts):
    require(isinstance(m,dict) and m.get('schemaVersion')=='ngs.npc-animation-frame-set.v1','NPC_FRAMES_SCHEMA')
    require(isinstance(m.get('posePreset'),str) and isinstance(m.get('presetId'),str),'NPC_FRAMES_SLOT')
    key=m['posePreset']+'/'+m['presetId']
    require(re.fullmatch(r'(?:representative|combat|idle)/[A-Za-z0-9][A-Za-z0-9._-]{0,127}',key),'NPC_FRAMES_SLOT')
    require(m.get('scalePreset','full')in('full','half'),'NPC_FRAMES_SCALE')
    if m.get('scalePreset')=='half':
        require(m.get('pixelScale')==0.5 and m.get('canvas',{}).get('scale')==0.5,'NPC_FRAMES_HALF_SCALE');key+='/half'
    canvas=m.get('canvas',{}); dims=(canvas.get('width'),canvas.get('height'))
    require(all(type(n)is int and 16<=n<=4096 for n in dims),'NPC_FRAMES_CANVAS')
    directions=m.get('directions',{});require(isinstance(directions,dict) and set(directions)==set(DIRECTIONS),'NPC_FRAMES_DIRECTIONS')
    referenced=set()
    for direction,v in directions.items():
        count=v.get('frameCount');fps=v.get('fps');frames=v.get('frames')
        require(type(count)is int and 2<=count<=1000 and type(fps)in(int,float) and 0<fps<=120,'NPC_FRAMES_TIMING')
        require(isinstance(frames,list) and len(frames)==count and [f.get('index')for f in frames]==list(range(count)),'NPC_FRAMES_COMPLETE_SEQUENCE')
        require(re.fullmatch(r'[0-9a-f]{64}\.mp4',str(v.get('sourceAssetId',''))),'NPC_FRAMES_SOURCE')
        for f in frames:
            aid=f.get('assetId');raw=f.get('rawAssetId')
            require(aid in artifacts and raw in artifacts,'NPC_FRAMES_ASSET_MISSING')
            require(artifacts[aid][1]==dims,'NPC_FRAMES_CANVAS_MISMATCH')
            require(f.get('sha256',aid[:-4])==aid[:-4],'NPC_FRAMES_HASH_MISMATCH')
            referenced.update((aid,raw))
            if 'alphaAssetId' in f:
                alpha=f['alphaAssetId']
                require(alpha in artifacts,'NPC_FRAMES_ALPHA_ASSET_MISSING')
                require(artifacts[alpha][1]==artifacts[raw][1],'NPC_FRAMES_ALPHA_DIMENSIONS')
                referenced.add(alpha)
    require(referenced==set(artifacts),'NPC_FRAMES_UNREFERENCED_ASSET')
    return key


class AnimationFramePublisher:
    def __init__(self,domains):
        self.domains=domains;self.source=domains.npcs;self.db=domains.npc_receipts.connection
        with domains.lock,self.db:self.db.execute('CREATE TABLE IF NOT EXISTS npc_animation_frame_receipts(request_id TEXT PRIMARY KEY,input_hash TEXT NOT NULL,receipt TEXT NOT NULL)')
    def receipt(self,key):
        with self.domains.lock:r=self.db.execute('SELECT receipt FROM npc_animation_frame_receipts WHERE request_id=?',(key,)).fetchone()
        return json.loads(r[0]) if r else None
    def publish(self,p):
        require(isinstance(p,dict) and set(p)=={'requestId','projectId','npcId','expectedSourceVersion','expectedCharacterHash','manifest','artifacts','trace'},'NPC_FRAMES_REQUEST')
        require(p['projectId']=='binari' and re.fullmatch(r'[A-Za-z0-9._:-]{8,180}',str(p['requestId'])) and isinstance(p['trace'],dict),'NPC_FRAMES_BINDING')
        require(isinstance(self.source,FilexCurrentNPC),'NPC_FILEX_AUTHORITY_REQUIRED')
        require(isinstance(p['artifacts'],list) and len(p['artifacts'])<=16000,'NPC_FRAMES_ARTIFACTS')
        artifacts={}
        for a in p['artifacts']:
            data,dims=png_artifact(a);aid=a['sha256']+'.png'
            require(aid not in artifacts,'NPC_FRAMES_DUPLICATE_ASSET');artifacts[aid]=(data,dims)
        key=validate_manifest(p['manifest'],artifacts)
        ih=digest({k:v for k,v in p.items()if k not in('expectedSourceVersion','expectedCharacterHash','artifacts')})
        value={**copy.deepcopy(p['manifest']),'requestId':p['requestId'],'inputHash':ih,'trace':copy.deepcopy(p['trace'])}
        with self.domains.lock,self.db:
            old=self.db.execute('SELECT input_hash,receipt FROM npc_animation_frame_receipts WHERE request_id=?',(p['requestId'],)).fetchone()
            if old:
                require(old[0]==ih,'NPC_FRAMES_IDEMPOTENCY_CONFLICT');return json.loads(old[1])
            current=self.source.current();doc=current['document'];chars=[c for c in doc['characters']if c['id']==p['npcId']]
            require(len(chars)==1,'NPC_FRAMES_NPC_MISSING');character=chars[0]
            if character.get('animationFrameSets',{}).get(key)!=value:
                require(current['sourceVersion']==p['expectedSourceVersion'] and digest(character)==p['expectedCharacterHash'],'NPC_FRAMES_SOURCE_CHANGED')
                full_key=str(value['posePreset'])+'/'+str(value['presetId'])
                half=value.get('scalePreset')=='half'
                if half:
                    pin=value.get('sourceFrameSet',{});full=character.get('animationFrameSets',{}).get(full_key)
                    require(isinstance(full,dict) and pin=={'slotKey':full_key,'requestId':full.get('requestId'),'inputHash':full.get('inputHash'),'contentHash':digest(full)},'NPC_FRAMES_SOURCE_SET_CHANGED')
                stale=[]
                for direction,v in value['directions'].items():
                    slot=character.get('animationSlots',{}).get(full_key+'/'+direction,{})
                    if half:
                        source=full.get('directions',{}).get(direction,{})
                        require(all(source.get(k)==v.get(k) for k in ('sourceAssetId','frameCount','fps')) and [(f['index'],f['rawAssetId'],f.get('alphaAssetId')) for f in source.get('frames',[])]==[(f['index'],f['rawAssetId'],f.get('alphaAssetId')) for f in v['frames']],'NPC_FRAMES_DERIVED_LINEAGE')
                        if slot.get('assetId')!=v['sourceAssetId']:stale.append(direction)
                    else:require(slot.get('assetId')==v['sourceAssetId'] and slot.get('frameCount')==v['frameCount'] and slot.get('fps')==v['fps'],'NPC_FRAMES_VIDEO_CHANGED')
                    # Half reprocessing consumes the pinned full RGBA frames, not an obsolete video.
                    # Its sourceAssetId remains provenance; only full extraction requires video bytes.
                    if not half:
                        video=self.source.asset_get(v['sourceAssetId']);require(hashlib.sha256(video).hexdigest()==v['sourceAssetId'][:-4],'NPC_FRAMES_VIDEO_HASH')
                if half:require(value.get('sourceVideoStatus',{}).get('staleDirections')==[d for d in DIRECTIONS if d in stale],'NPC_FRAMES_STALE_DISCLOSURE')
                for aid,(data,dims) in artifacts.items():self.source.asset_put(aid,data)
                after=copy.deepcopy(doc);target=next(c for c in after['characters']if c['id']==p['npcId']);target.setdefault('animationFrameSets',{})[key]=value
                require(self.source.save(current['revision'],'NGS animation frames '+p['requestId'],after)is not None,'NPC_FRAMES_SOURCE_CHANGED')
            latest=self.source.current();target=next(c for c in latest['document']['characters']if c['id']==p['npcId'])
            require(target.get('animationFrameSets',{}).get(key)==value,'NPC_FRAMES_READBACK')
            for aid,(data,dims) in artifacts.items():require(self.source.asset_get(aid)==data,'NPC_FRAMES_ASSET_READBACK')
            receipt={'requestId':p['requestId'],'inputHash':ih,'npcId':p['npcId'],'slotKey':key,'frameCount':sum(v['frameCount']for v in value['directions'].values()),'assetCount':len(artifacts),'revision':latest['revision'],'sourceVersion':latest['sourceVersion'],'documentHash':digest(latest['document']),'verified':True,'source':'filex','ngsReplicaPulled':False}
            self.db.execute('INSERT INTO npc_animation_frame_receipts VALUES(?,?,?)',(p['requestId'],ih,json.dumps(receipt)));return receipt
