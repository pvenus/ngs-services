"""Scoped rotation-image publication. Canonical pose originals are never replaced."""
import base64, copy, hashlib, json, re, struct, zlib
from .filex_read import digest, require
from .filex_npc_authority import FilexCurrentNPC
from .filex_npc_files import POSE_VIEW_KEY


def png_artifact(a):
    require(isinstance(a,dict) and set(a)=={'sha256','bytes','mimeType','base64'} and a['mimeType']=='image/png','NPC_POSE_ARTIFACT_INVALID')
    b=base64.b64decode(a['base64'],validate=True)
    require(type(a['bytes'])is int and len(b)==a['bytes'] and 33<len(b)<=32*1024*1024 and hashlib.sha256(b).hexdigest()==a['sha256'] and b[:8]==b'\x89PNG\r\n\x1a\n','NPC_POSE_ARTIFACT_BYTES')
    offset=8; dims=None; compressed=bytearray(); ended=False;channels=0
    while offset+12<=len(b):
        n=struct.unpack('>I',b[offset:offset+4])[0]; end=offset+n+12
        require(end<=len(b),'NPC_POSE_PNG_CHUNK')
        kind=b[offset+4:offset+8];data=b[offset+8:offset+8+n]
        require(zlib.crc32(kind+data)&0xffffffff==struct.unpack('>I',b[offset+8+n:end])[0],'NPC_POSE_PNG_CRC')
        if offset==8:require(kind==b'IHDR','NPC_POSE_PNG_HEADER')
        if kind==b'IHDR':
            require(dims is None and n==13,'NPC_POSE_PNG_HEADER')
            w,h,depth,color,comp,filt,interlace=struct.unpack('>IIBBBBB',data);channels={2:3,6:4}.get(color,0)
            require(16<=w<=4096 and 16<=h<=4096 and depth==8 and channels and comp==filt==interlace==0,'NPC_POSE_PNG_DIMENSIONS');dims=(w,h)
        elif kind==b'IDAT':compressed.extend(data)
        elif kind==b'IEND':require(n==0 and end==len(b),'NPC_POSE_PNG_END');ended=True;break
        elif kind in (b'acTL',b'fcTL',b'fdAT') or kind[:1].isupper():raise ValueError('NPC_POSE_PNG_UNSUPPORTED')
        offset=end
    require(dims and ended and compressed,'NPC_POSE_PNG_INCOMPLETE')
    maximum=(dims[0]*channels+1)*dims[1];decoder=zlib.decompressobj();raw=decoder.decompress(compressed,maximum+1)
    require(len(raw)==maximum and decoder.eof and not decoder.unused_data and all(raw[y*(dims[0]*channels+1)]<=4 for y in range(dims[1])),'NPC_POSE_PNG_PIXELS')
    return b,dims


class PoseViewPublisher:
    def __init__(self,domains):
        self.domains=domains;self.source=domains.npcs;self.db=domains.npc_receipts.connection
        with domains.lock,self.db:self.db.execute('CREATE TABLE IF NOT EXISTS npc_pose_view_receipts(request_id TEXT PRIMARY KEY,input_hash TEXT NOT NULL,receipt TEXT NOT NULL)')
    def receipt(self,key):
        with self.domains.lock:r=self.db.execute('SELECT receipt FROM npc_pose_view_receipts WHERE request_id=?',(key,)).fetchone()
        return json.loads(r[0]) if r else None
    def publish(self,p):
        require(isinstance(p,dict) and set(p)-{'expectedSlotHash'}=={'requestId','projectId','npcId','expectedSourceVersion','expectedCharacterHash','slot','artifact','sourceArtifact','trace'},'NPC_POSE_REQUEST_INVALID')
        require(p['projectId']=='binari' and isinstance(p['requestId'],str) and re.fullmatch(r'[A-Za-z0-9._:-]{8,180}',p['requestId']) and isinstance(p['trace'],dict),'NPC_POSE_REQUEST_BINDING')
        require(isinstance(self.source,FilexCurrentNPC),'NPC_FILEX_AUTHORITY_REQUIRED')
        if 'expectedSlotHash'in p:require(isinstance(p['expectedSlotHash'],str)and re.fullmatch('[0-9a-f]{64}',p['expectedSlotHash']),'NPC_POSE_SLOT_HASH_INVALID')
        slot=p['slot'];require(isinstance(slot,dict) and set(slot)=={'posePreset','referenceType','direction','sourceAssetId'},'NPC_POSE_SLOT_INVALID')
        key='/'.join(str(slot[k]) for k in ('posePreset','referenceType','direction'));require(POSE_VIEW_KEY.fullmatch(key),'NPC_POSE_SLOT_INVALID')
        data,dims=png_artifact(p['artifact']);raw,raw_dims=png_artifact(p['sourceArtifact'])
        trace=p['trace'];proof=trace.get('providerProof',{})
        require(isinstance(proof,dict) and proof.get('actualToolOutput')is True and proof.get('sha256')==p['sourceArtifact']['sha256'],'NPC_POSE_PROVIDER_PROOF')
        if slot['referenceType']=='pixel':
            post=trace.get('postprocess',{})
            require(dims==(384,384) and isinstance(post,dict) and post.get('profileId')=='npc.pose-pixel.384.v1' and post.get('sourceSha256')==p['sourceArtifact']['sha256'],'NPC_POSE_POSTPROCESS')
        else:require(data==raw,'NPC_POSE_PORTRAIT_SOURCE_MISMATCH')
        aid=p['artifact']['sha256']+'.png';rawid=p['sourceArtifact']['sha256']+'.png'
        ih=digest({**{k:v for k,v in p.items()if k not in ('expectedSourceVersion','expectedCharacterHash')},'artifact':{k:v for k,v in p['artifact'].items()if k!='base64'},'sourceArtifact':{k:v for k,v in p['sourceArtifact'].items()if k!='base64'}})
        value={**slot,'schemaVersion':'ngs.npc-pose-view.v1','assetId':aid,'sourceArtifactId':rawid,'sourceHash':str(slot['sourceAssetId']).split('.')[0],'requestId':p['requestId'],'mimeType':'image/png','bytes':len(data),'width':dims[0],'height':dims[1],'trace':copy.deepcopy(trace),'inputHash':ih}
        with self.domains.lock,self.db:
            old=self.db.execute('SELECT input_hash,receipt FROM npc_pose_view_receipts WHERE request_id=?',(p['requestId'],)).fetchone()
            if old:require(old[0]==ih,'NPC_POSE_IDEMPOTENCY_CONFLICT');return json.loads(old[1])
            current=self.source.current();document=current['document'];chars=[c for c in document['characters']if c['id']==p['npcId']];require(len(chars)==1,'NPC_POSE_NPC_MISSING');character=chars[0]
            existing=character.get('poseViews',{}).get(slot['posePreset'],{}).get(slot['referenceType'],{}).get(slot['direction'])
            if existing!=value:
                if 'expectedSlotHash'in p:require(existing is not None and digest(existing)==p['expectedSlotHash'],'NPC_POSE_TARGET_CHANGED')
                require(current['sourceVersion']==p['expectedSourceVersion'] and digest(character)==p['expectedCharacterHash'],'NPC_POSE_SOURCE_CHANGED')
                pair=character.get('portraitSets',{}).get(slot['posePreset'],{});ref=pair.get('pixelAssetId'if slot['referenceType']=='pixel'else'portraitAssetId')
                require(ref==slot['sourceAssetId'],'NPC_POSE_REFERENCE_CHANGED');self.source.asset_get(ref)
                self.source.asset_put(rawid,raw);self.source.asset_put(aid,data)
                after=copy.deepcopy(document);target=next(c for c in after['characters']if c['id']==p['npcId'])
                target.setdefault('poseViews',{}).setdefault(slot['posePreset'],{}).setdefault(slot['referenceType'],{})[slot['direction']]=value
                require(self.source.save(current['revision'],'NGS pose view '+p['requestId'],after)is not None,'NPC_POSE_SOURCE_CHANGED')
            latest=self.source.current();target=next(c for c in latest['document']['characters']if c['id']==p['npcId'])
            require(target.get('poseViews',{}).get(slot['posePreset'],{}).get(slot['referenceType'],{}).get(slot['direction'])==value and self.source.asset_get(aid)==data and self.source.asset_get(rawid)==raw,'NPC_POSE_READBACK_MISMATCH')
            receipt={'requestId':p['requestId'],'inputHash':ih,'npcId':p['npcId'],'slotKey':key,'assetId':aid,'sourceArtifactId':rawid,'sha256':p['artifact']['sha256'],'bytes':len(data),'revision':latest['revision'],'sourceVersion':latest['sourceVersion'],'documentHash':digest(latest['document']),'verified':True,'source':'filex','ngsReplicaPulled':False}
            self.db.execute('INSERT INTO npc_pose_view_receipts VALUES(?,?,?)',(p['requestId'],ih,json.dumps(receipt,ensure_ascii=False)));return receipt
