"""NPC binary originals and whole-character deletion only; no edit history."""
import copy
import base64
import hashlib
import json
import re
from pathlib import Path
from server import Conflict

def sha(data): return hashlib.sha256(data).hexdigest()
def wire(value): return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()

class NPCAssets:
    def asset_path(self, asset_id):
        if not isinstance(asset_id,str) or not re.fullmatch(r'[0-9a-f]{64}\.(png|jpg|webp|mp4)',asset_id): raise ValueError('asset id')
        parent=self.internal('assets')
        path=parent/asset_id
        if path.is_symlink(): raise ValueError('symlink')
        return path
    def asset(self,asset_id):
        with self.lock():
            data=self.asset_path(asset_id).read_bytes()
            if sha(data)!=asset_id.split('.')[0]: raise Conflict('asset integrity')
            return {'assetId':asset_id,'base64':base64.b64encode(data).decode(),'sha256':sha(data)}
    def put_asset(self,asset_id,data):
        with self.lock():
            path=self.asset_path(asset_id)
            if sha(data)!=asset_id.split('.')[0]: raise ValueError('asset hash')
            if path.exists() and path.read_bytes()!=data: raise Conflict('asset collision')
            if not path.exists(): self.write(path,data)
            if path.read_bytes()!=data: raise Conflict('asset verify')
            return {'assetId':asset_id,'sha256':sha(data),'bytes':len(data)}
    def recover_delete(self):
        journal=self.internal('.delete.json')
        if not journal.exists(): return
        r=json.loads(journal.read_bytes()); backup=self.internal('.deleted')/r['backupId']
        manifest=json.loads((backup/'manifest.json').read_bytes())
        for key,expected in manifest['files'].items():
            if sha((backup/'files'/key).read_bytes())!=expected: raise Conflict('backup integrity')
        # Backup is verified before every destructive step, including recovery.
        for key in r['remove']:
            path=self.path(r['prefix']+key)
            if path.exists():
                if sha(path.read_bytes())!=manifest['files'][key]: raise Conflict('delete source changed')
                path.unlink()
                import os
                fd=os.open(path.parent,os.O_RDONLY)
                try: os.fsync(fd)
                finally: os.close(fd)
        self.write(self.path(r['prefix']+'metadata.json'),wire(r['metadataAfter']))
        receipt={k:r[k] for k in ('requestId','inputHash','payloadHash','backupId')}
        receipt.update(status='committed',backupVerified=True,backupManifestHash=sha(wire(manifest)),backupFileCount=len(manifest['files']),deletedCharacter=r['characterId'],resultMetadata=r['metadataAfter'].get('_pdsCurrent'))
        self.write(self.receipt_path(r['requestId']),wire(receipt));journal.unlink()
    def delete_character(self,request_id,input_hash,character_id,expected_source,metadata_after):
        if not isinstance(character_id,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',character_id): raise ValueError('character id')
        if not isinstance(request_id,str) or not request_id or not isinstance(input_hash,str) or not input_hash or not isinstance(metadata_after,dict): raise ValueError('request binding')
        payload_hash=sha(wire([character_id,expected_source,metadata_after]))
        with self.lock():
            receipt=self.receipt_path(request_id)
            if receipt.exists():
                r=json.loads(receipt.read_bytes())
                if r['inputHash']!=input_hash or r.get('payloadHash')!=payload_hash: raise Conflict('request reused')
                return r
            s=self.snapshot_unlocked()
            if s['sourceVersion']!=expected_source: raise Conflict('stale delete')
            old=s['documents']['metadata.json']; order=old['characterOrder']
            if character_id not in order: raise ValueError('unknown character')
            expected={**old,'characterOrder':[x for x in order if x!=character_id],'generationEpoch':request_id}
            # Caller may advance receipt metadata, never alter other root content.
            check=dict(metadata_after);check['_pdsCurrent']=expected.get('_pdsCurrent')
            if check!=expected: raise ValueError('delete metadata contract')
            # Derive the output hash independently from the locked canonical files.
            result=copy.deepcopy(metadata_after);result.pop('_pdsCurrent',None)
            remaining=result.pop('characterOrder');result['characters']=[]
            for npc_id in remaining:
                npc=copy.deepcopy(s['documents'][npc_id+'/metadata.json'])
                if 'planningCategoryOrder' in npc:
                    category_ids=npc.pop('planningCategoryOrder')
                    npc['planningCategories']=[copy.deepcopy(s['documents'][npc_id+'/planning/'+cid+'.json']) for cid in category_ids]
                if 'animationExperimentKeys' in npc:
                    keys=npc.pop('animationExperimentKeys');npc['animationExperiments']={key:copy.deepcopy(s['documents'][npc_id+'/animation-experiments/'+key+'.json'])for key in keys}
                if 'poseViewKeys' in npc:
                    keys=npc.pop('poseViewKeys');npc['poseViews']={}
                    for key in keys:
                        pose,kind,direction=key.split('/')
                        npc['poseViews'].setdefault(pose,{}).setdefault(kind,{})[direction]=copy.deepcopy(s['documents'][npc_id+'/pose-views/'+key+'.json'])
                if 'animationFrameSetKeys' in npc:
                    keys=npc.pop('animationFrameSetKeys');npc['animationFrameSets']={key:copy.deepcopy(s['documents'][npc_id+'/animation-frames/'+key+'.json'])for key in keys}
                if 'animationSlotKeys' in npc:
                    keys=npc.pop('animationSlotKeys');npc['animationSlots']={key:copy.deepcopy(s['documents'][npc_id+'/animations/'+key+'.json'])for key in keys}
                result['characters'].append(npc)
            actual_hash=sha(wire(result))
            if metadata_after.get('_pdsCurrent',{}).get('documentHash') not in (None,actual_hash): raise ValueError('delete result hash')
            metadata_after=copy.deepcopy(metadata_after)
            metadata_after.setdefault('_pdsCurrent',{})['documentHash']=actual_hash
            keys=[k for k in s['documents'] if k.startswith(character_id+'/')]
            payload={k:self.path('canonical-files/binari/npcs/'+k).read_bytes() for k in keys}
            payload['metadata.json']=self.path('canonical-files/binari/npcs/metadata.json').read_bytes()
            refs=set(re.findall(r'[0-9a-f]{64}\.(?:png|jpg|webp|mp4)',wire({k:s['documents'][k] for k in keys}).decode()))
            for asset_id in refs:
                data=self.asset_path(asset_id).read_bytes()
                if sha(data)!=asset_id.split('.')[0]: raise Conflict('asset integrity')
                payload['assets/'+asset_id]=data
            backup_id=sha(request_id.encode());backup=self.internal('.deleted')/backup_id
            if backup.is_symlink(): raise ValueError('symlink')
            for key,data in payload.items():
                target=backup/'files'/key
                for parent in (target,*target.parents):
                    if parent.is_symlink(): raise ValueError('backup symlink')
                    if parent==self.root: break
                self.write(target,data)
            self.write(backup/'manifest.json',wire({'characterId':character_id,'files':{k:sha(v) for k,v in payload.items()}}))
            r={'requestId':request_id,'inputHash':input_hash,'payloadHash':payload_hash,'backupId':backup_id,'characterId':character_id,'remove':keys,'metadataAfter':metadata_after,'prefix':'canonical-files/binari/npcs/'}
            self.write(self.internal('.delete.json'),wire(r));self.recover_delete()
            # Content-addressed shared assets intentionally remain, including unreferenced history.
            return json.loads(receipt.read_bytes())
