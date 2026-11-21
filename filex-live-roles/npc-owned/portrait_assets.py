"""Request-bound aliases for existing NPC originals; no second byte store."""
import hashlib,json,re
from server import Conflict
from npc_store import encoded,digest

class PortraitAssets:
    def portrait_alias_path(self,aid):
        if not isinstance(aid,str) or not re.fullmatch(r'asset\.[a-f0-9]{48}',aid):raise ValueError('portrait alias id')
        return self.safe('.portrait-assets/'+aid+'.json')
    def portrait_asset(self,aid):
        with self.lock():
            r=json.loads(self.portrait_alias_path(aid).read_bytes())
            data=self.asset_path(r['asset']['canonicalAssetId']).read_bytes()
            if hashlib.sha256(data).hexdigest()!=r['asset']['sha256'] or len(data)!=r['asset']['size']:raise Conflict('portrait alias integrity')
            return r
    def register_portrait_asset(self,meta,data,guard):
        if not isinstance(meta,dict) or set(meta)!={'runId','projectId','sha256','size','provenance'}:raise ValueError('portrait metadata')
        run=meta['runId']
        if meta['projectId']!='ProjectBS' or not isinstance(run,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,190}',run):raise ValueError('portrait target')
        if not isinstance(meta['provenance'],dict) or len(encoded(meta['provenance']))>8192:raise ValueError('portrait provenance')
        sha=hashlib.sha256(data).hexdigest()
        if type(meta['size']) is not int or not 0<len(data)<=32*1024*1024 or meta['size']!=len(data) or meta['sha256']!=sha:raise ValueError('portrait bytes')
        # Full PNG structure/pixel stream validation, not just the magic bytes.
        from media_validation import png
        png(data)
        aid='asset.'+hashlib.sha256(('binari:'+run).encode()).hexdigest()[:48]
        with self.lock():
            self.checked(guard)
            stage=guard.get('stageRequestId',guard['requestId'])
            if run not in (stage,stage+'.pixel',stage+'.pixel-source'):raise ValueError('portrait stage binding')
            binding={'metadata':meta,'npcId':guard['npcId'],'generationId':guard['generationId'],'parentRequestId':guard['requestId'],'stageRequestId':stage}
            ih=digest(binding);path=self.portrait_alias_path(aid)
            if path.exists():
                old=self.portrait_asset(aid)
                if old['inputHash']!=ih:raise Conflict('portrait run reused')
                return old
            # Bind run before byte write, so a crash cannot reuse it with different bytes.
            intent=self.safe('.portrait-assets/'+aid+'.intent')
            if intent.exists() and intent.read_bytes()!=encoded(binding):raise Conflict('portrait intent reused')
            if not intent.exists():self.write(intent,encoded(binding))
            self.put_asset(sha+'.png',data,guard)
            if self.asset_path(sha+'.png').read_bytes()!=data:raise Conflict('portrait readback')
            asset={'id':aid,'projectId':'ProjectBS','kind':'portrait','mimeType':'image/png','size':len(data),'sha256':sha,'origin':'generated','status':'available','revision':1,'canonicalAssetId':sha+'.png','downloadPath':'/v1/portrait-assets/'+aid+'/content','provenance':meta['provenance']}
            receipt={'schemaVersion':'pds.portrait-asset.v1','inputHash':ih,'asset':asset,'verified':True,'authority':'filex',**{k:v for k,v in binding.items()if k!='metadata'}}
            receipt['receiptHash']=digest(receipt)
            self.write(path,encoded(receipt));return self.portrait_asset(aid)
