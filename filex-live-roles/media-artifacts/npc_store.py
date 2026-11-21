"""NPC-only multi-file commit. Cooperating readers are isolated by the same lock.
No claim of multi-file atomicity for direct filesystem/Filex readers.
"""
import base64
import hashlib
import json
import os
import re
import shutil
from contextlib import contextmanager
from pathlib import Path
from server import Store, Conflict

NPC_PREFIX='canonical-files/binari/npcs/'
def encoded(obj): return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def digest(obj): return hashlib.sha256(encoded(obj)).hexdigest()

from npc_assets import NPCAssets

class NPCStore(NPCAssets, Store):
    def path(self,key):
        if not isinstance(key,str) or not key.startswith(NPC_PREFIX): raise ValueError('namespace')
        relative=key[len(NPC_PREFIX):]
        if relative!='metadata.json' and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}/(?:metadata\.json|planning/[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json|animation-experiments/eight-up-run-4x2(?:-matched-reference)?\.json|pose-views/(?:representative|combat|idle)/(?:pixel|portrait)/(?:n|ne|e|se|s|sw|w|nw)\.json|animation-frames/(?:representative|combat|idle)/[A-Za-z0-9][A-Za-z0-9._-]{0,127}(?:/half)?\.json|animations/(?:representative|combat|idle)/[A-Za-z0-9][A-Za-z0-9._-]{0,127}/(?:n|ne|e|se|s|sw|w|nw)\.json)',relative): raise ValueError('path')
        path=self.root
        for part in relative.split('/'):
            path=path/part
            if path.is_symlink(): raise ValueError('symlink')
        return path
    def internal(self,name):
        path=self.root/name
        if path.is_symlink(): raise ValueError('symlink')
        return path
    @contextmanager
    def lock(self):
        with super().lock():
            self.recover_batch()
            self.recover_delete()
            yield
    def recover_batch(self):
        journal=self.internal('.batch.json')
        if not journal.exists(): return
        record=json.loads(journal.read_bytes())
        stage=self.internal('.stage')
        for index,item in enumerate(record['files']):
            path=self.path(item['key'])
            actual=digest(json.loads(path.read_bytes())) if path.exists() else None
            if actual==item['afterHash']: continue
            if actual!=item['expectedHash']: raise Conflict('external change during recovery')
            content=(stage/str(index)).read_bytes()
            if digest(json.loads(content))!=item['afterHash']: raise Conflict('staged data changed')
            self.write(path,content)
        receipt={k:v for k,v in record.items() if k!='files'}
        receipt.update(status='committed',hashes={x['key']:x['afterHash'] for x in record['files']})
        self.write(self.receipt_path(record['requestId']),encoded(receipt))
        journal.unlink()
        fd=os.open(self.root,os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
        shutil.rmtree(stage)
    def batch(self,request_id,input_hash,files,read_set):
        if not request_id or not input_hash or not isinstance(files,list) or not files or not isinstance(read_set,dict): raise ValueError('batch contract')
        plan=[]
        for item in files:
            key=item['path']; self.path(key)
            if key not in read_set or item['expectedHash']!=read_set[key] or not isinstance(item['body'],dict): raise ValueError('write basis')
            plan.append({'key':key,'expectedHash':item['expectedHash'],'afterHash':digest(item['body'])})
        if len({p['key'] for p in plan})!=len(plan): raise ValueError('duplicate path')
        payload_hash=digest({'files':files,'readSet':read_set})
        with self.lock():
            receipt_path=self.receipt_path(request_id)
            if receipt_path.exists():
                record=json.loads(receipt_path.read_bytes())
                if record['inputHash']!=input_hash or record['payloadHash']!=payload_hash: raise Conflict('request reused')
                return record
            for key,expected in read_set.items():
                path=self.path(key)
                actual=digest(json.loads(path.read_bytes())) if path.exists() else None
                if actual!=expected: raise Conflict('read set changed')
            stage=self.internal('.stage')
            if stage.exists(): shutil.rmtree(stage) # abandoned pre-journal new bytes only
            stage.mkdir()
            for index,item in enumerate(files): self.write(stage/str(index),encoded(item['body']))
            record={'requestId':request_id,'inputHash':input_hash,'payloadHash':payload_hash,'status':'prepared','files':plan,'resultMetadata':next((x['body'].get('_pdsCurrent') for x in files if x['path']==NPC_PREFIX+'metadata.json'),None)}
            self.write(self.internal('.batch.json'),encoded(record))
            self.recover_batch()
            return json.loads(receipt_path.read_bytes())
    def snapshot_unlocked(self):
        documents={}
        for path in sorted(self.root.rglob('*.json')):
            relative=path.relative_to(self.root).as_posix()
            if any(p.startswith('.') for p in relative.split('/')): continue
            self.path(NPC_PREFIX+relative)
            documents[relative]=json.loads(path.read_bytes())
        hashes={p:digest(v) for p,v in documents.items()}
        assets=[]
        for asset_id in sorted(set(re.findall(r'[0-9a-f]{64}\.(?:png|jpg|webp|mp4)',encoded(documents).decode()))):
            data=self.asset_path(asset_id).read_bytes()
            if hashlib.sha256(data).hexdigest()!=asset_id.split('.')[0]: raise Conflict('asset integrity')
            assets.append({'assetId':asset_id,'sha256':asset_id.split('.')[0],'size':len(data),'mimeType':{'png':'image/png','jpg':'image/jpeg','webp':'image/webp','mp4':'video/mp4'}[asset_id.split('.')[-1]]})
        return {'sourceVersion':digest(hashes),'manifestHash':digest(hashes),'documents':documents,'hashes':hashes,'assets':assets,'hashMode':'canonical-json'}
    def cas(self,*args,**kwargs): raise ValueError('NPC requires batch/readSet')
