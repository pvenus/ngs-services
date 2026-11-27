"""Opt-in NPC current-slot writer. Readers MUST take the inherited lock.
JSON, binary slots and manifests recover through one roll-forward journal.
No existing deployment selects this implementation automatically.
"""
import base64,copy,hashlib,json,os,re,shutil
from pathlib import Path
from contextlib import contextmanager
from npc_store import NPCStore,NPC_PREFIX,encoded,digest
from server import Store,Conflict
from ownership_plan import plan as ownership_plan
from npc_document import decode
import native_pixel_direction as directional
from npc_planning_markdown import planning_projection,PLANNING,markdown_path

class OwnedNPCStore(NPCStore):
    def safe(self,relative):
        if not isinstance(relative,str) or not relative or relative.startswith('/') or any(x in ('','..','.') for x in relative.split('/')):raise ValueError('relative path')
        p=self.root
        for part in relative.split('/'):
            p=p/part
            if p.is_symlink():raise ValueError('symlink')
        return p
    @contextmanager
    def lock(self):
        with Store.lock(self):
            self.recover_batch();self.recover_delete();self.recover_owned()
            yield
    def manifests(self):
        out={}
        for p in self.root.glob('*/.owned-assets.json'):
            self.safe(p.relative_to(self.root).as_posix())
            out[p.parent.name]=json.loads(p.read_bytes())
        return out
    def asset_path(self,asset_id, *, slot_paths=None):
        if not isinstance(asset_id,str) or not re.fullmatch(r'[0-9a-f]{64}\.(png|jpg|webp|mp4)',asset_id):raise ValueError('asset id')
        if slot_paths is None:
            slot_paths=self.asset_slot_paths(self.manifests())
        if asset_id in slot_paths:return self.safe(slot_paths[asset_id])
        # Staged bytes are accepted only for publication validation. They are
        # excluded from snapshot/publication. Legacy source remains read-only
        # until an explicit migration cleanup separately authorizes removal.
        for prefix in ('.pending-assets/','assets/'):
            p=self.safe(prefix+asset_id)
            if p.exists():return p
        raise FileNotFoundError(asset_id)
    @staticmethod
    def asset_slot_paths(manifests):
        # Request-local index only: preserve first-owner/slot lookup semantics.
        # Never cache across snapshots, writes, restores or generation changes.
        paths={}
        for owner,manifest in manifests.items():
            for path,item in manifest['slots'].items():
                paths.setdefault(item['assetId'],owner+'/'+path)
        return paths
    def put_asset(self,asset_id,data):
        if not re.fullmatch(r'[0-9a-f]{64}\.(png|jpg|webp|mp4)',str(asset_id)) or hashlib.sha256(data).hexdigest()!=asset_id.split('.')[0]:raise ValueError('asset hash')
        with self.lock():
            p=self.safe('.pending-assets/'+asset_id)
            if p.exists() and p.read_bytes()!=data:raise Conflict('asset collision')
            if not p.exists():self.write(p,data)
            return {'assetId':asset_id,'sha256':asset_id.split('.')[0],'bytes':len(data),'staged':True}
    def documents_unlocked(self):
        documents={}
        for p in sorted(self.root.rglob('*.json')):
            rel=p.relative_to(self.root).as_posix()
            if any(x.startswith('.') for x in rel.split('/')):continue
            self.path(NPC_PREFIX+rel);value=json.loads(p.read_bytes())
            if PLANNING.fullmatch(rel) and 'contentFormat' not in value and self.safe(markdown_path(rel)).exists():
                raise Conflict('NPC_PLANNING_UNBOUND_MARKDOWN')
            documents[rel]=planning_projection(rel,value,lambda key:self.safe(key).read_bytes())
        return documents
    def documents_snapshot_unlocked(self):
        documents=self.documents_unlocked()
        hashes={k:digest(v) for k,v in documents.items()}
        version=digest(hashes)
        return {'sourceVersion':version,'manifestHash':version,'documents':documents,
                'hashes':hashes,'hashMode':'canonical-json'}
    def documents_snapshot(self):
        with self.lock():return self.documents_snapshot_unlocked()
    def snapshot_unlocked(self):
        snapshot=self.documents_snapshot_unlocked()
        documents=snapshot['documents'];refs=set();manifests=self.manifests()
        for owner in documents.get('metadata.json',{}).get('characterOrder',[]):
            if owner in manifests:refs.update(x['assetId']for x in manifests[owner]['slots'].values())
            else:refs.update(re.findall(r'[0-9a-f]{64}\.(?:png|jpg|webp|mp4)',encoded({k:v for k,v in documents.items()if k.startswith(owner+'/')}).decode()))
        assets=[];slot_paths=self.asset_slot_paths(manifests)
        for aid in sorted(refs):
            b=self.asset_path(aid,slot_paths=slot_paths).read_bytes()
            if hashlib.sha256(b).hexdigest()!=aid.split('.')[0]:raise Conflict('asset integrity')
            assets.append({'assetId':aid,'sha256':aid.split('.')[0],'size':len(b),'mimeType':{'png':'image/png','jpg':'image/jpeg','webp':'image/webp','mp4':'video/mp4'}[aid.split('.')[-1]]})
        return {**snapshot,'assets':assets}
    def recover_owned(self):
        journal=self.safe('.owned-tx.json')
        if not journal.exists():return
        tx=json.loads(journal.read_bytes());stage=self.safe('.owned-stage')
        # Verify the complete staged set BEFORE the first write or deletion.
        for i,item in enumerate(tx['writes']):
            if hashlib.sha256((stage/str(i)).read_bytes()).hexdigest()!=item['sha256']:raise Conflict('staged bytes corrupt')
        receipt=tx['receipt']
        if receipt is None:
            if tx['requestId'] is not None or tx['removes']:raise Conflict('invalid current-write journal')
        elif receipt.get('backupId'):
            self.verified_backup(receipt['backupId'])
        if tx.get('move') is not None:
            self.recover_owner_folder_move(tx)
        for i,item in enumerate(tx['writes']):
            p=self.safe(item['path']);actual=hashlib.sha256(p.read_bytes()).hexdigest()if p.exists()else None
            if actual==item['sha256']:continue
            if actual!=item['before']:raise Conflict('concurrent file change')
            self.write(p,(stage/str(i)).read_bytes())
        for item in tx['removes']:
            p=self.safe(item['path'])
            if p.exists():
                if hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:raise Conflict('concurrent removal change')
                p.unlink()
        # Receipts become visible only after all bytes and documents committed.
        if receipt is not None:self.write(self.receipt_path(tx['requestId']),encoded(receipt))
        journal.unlink()
        if tx.get('move') is not None:
            # The committed receipt was durable before journal removal; never
            # discard stage bytes ahead of the removal becoming durable.
            fd=os.open(self.root,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
        shutil.rmtree(stage)
    def commit_owned(self,request_id,receipt,writes,removes=(),*,move=None):
        # Called only under writer lock. This private method is not an RPC.
        # Receiptless current writes retain the same integrity journal but cannot remove files.
        if (receipt is None) != (request_id is None) or (receipt is None and removes) or (move is not None and (receipt is None or removes)):
            raise ValueError('invalid current-write transaction')
        stage=self.safe('.owned-stage')
        if stage.exists():shutil.rmtree(stage) # no journal means never committed
        stage.mkdir();entries=[]
        for rel,b in sorted(writes.items()):
            p=self.safe(rel);before=p.read_bytes()if p.exists()else None
            if before==b:continue # unchanged current assets are not rewritten or versioned
            i=len(entries);self.write(stage/str(i),b)
            entries.append({'path':rel,'before':hashlib.sha256(before).hexdigest()if before is not None else None,'sha256':hashlib.sha256(b).hexdigest()})
        removal=[]
        for rel in sorted(set(removes)-set(writes)):
            p=self.safe(rel)
            if p.exists():removal.append({'path':rel,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
        self.write(self.safe('.owned-tx.json'),encoded({'requestId':request_id,'receipt':receipt,'writes':entries,'removes':removal,**({'move':move} if move is not None else {})}))
        self.recover_owned();return receipt
    def batch(self,request_id,input_hash,files,read_set):
        if not request_id or not input_hash or not files or not isinstance(read_set,dict):raise ValueError('batch contract')
        payload_hash=digest({'files':files,'readSet':read_set})
        with self.lock():
            rp=self.receipt_path(request_id)
            if rp.exists():
                r=json.loads(rp.read_bytes())
                if r['inputHash']!=input_hash or r['payloadHash']!=payload_hash:raise Conflict('request reused')
                return r
            before=self.snapshot_unlocked();after=copy.deepcopy(before['documents']);writes={};keys=set()
            directional_canvas=None
            for key,expected in read_set.items():
                self.path(key);rel=key.removeprefix(NPC_PREFIX)
                if before['hashes'].get(rel)!=expected:raise Conflict('read set changed')
            for item in files:
                key=item['path'];self.path(key)
                if key in keys or key not in read_set or item['expectedHash']!=read_set[key] or not isinstance(item['body'],dict):raise ValueError('batch contract')
                rel=key.removeprefix(NPC_PREFIX);owner,_,target=rel.partition('/')
                body=item['body']
                if PLANNING.fullmatch(rel) and (self.safe(markdown_path(rel)).exists() or
                   'contentFormat' in body or 'contentSha256' in body):
                    raise Conflict('NPC_PLANNING_MARKDOWN_WORKFLOW_REQUIRED')
                if body.get('schemaVersion')==directional.WORKFLOW_SCHEMA:
                    raise ValueError('workflow frames require their exact owner route')
                if target.startswith(directional.NAMESPACE):
                    guard=getattr(getattr(self,'_local',None),'publication',None)
                    directional.require(len(files)==1 and guard and
                        (owner,request_id,body.get('requestId'),body.get('generationId'))==
                        (guard['npcId'],guard.get('stageRequestId'),guard.get('stageRequestId'),guard['generationId']),
                        'guarded single stage')
                    directional.validate_basis(owner,target,body,before['documents'],digest)
                    directional_canvas=(owner,'assets/'+target.removesuffix('.json')+'/',body['canvas'])
                elif body.get('schemaVersion')==directional.SCHEMA:
                    raise ValueError('direction pixel scope')
                keys.add(key);after[rel]=body;writes[rel]=encoded(body)
            owners={k.removeprefix(NPC_PREFIX).split('/')[0]for k in keys if k!=NPC_PREFIX+'metadata.json'};removes=[]
            for owner in owners:
                docs={k[len(owner)+1:]:v for k,v in after.items()if k.startswith(owner+'/')}
                manifest=ownership_plan(owner,docs)
                # Fetch ALL required bytes before touching existing stable slots.
                for slot,item in manifest['slots'].items():
                    b=self.asset_path(item['assetId']).read_bytes()
                    if hashlib.sha256(b).hexdigest()!=item['sha256']:raise Conflict('asset integrity')
                    if directional_canvas and owner==directional_canvas[0] and slot.startswith(directional_canvas[1]):
                        directional.validate_png_canvas(b,directional_canvas[2])
                    writes[owner+'/'+slot]=b
                    if self.safe('.pending-assets/'+item['assetId']).exists():removes.append('.pending-assets/'+item['assetId'])
                previous=self.manifests().get(owner,{'slots':{}})
                removes.extend(owner+'/'+slot for slot in previous['slots']if slot not in manifest['slots'])
                writes[owner+'/.owned-assets.json']=encoded(manifest)
            receipt={'requestId':request_id,'inputHash':input_hash,'payloadHash':payload_hash,'status':'committed','hashes':{i['path']:digest(i['body'])for i in files},'resultMetadata':next((i['body'].get('_pdsCurrent')for i in files if i['path']==NPC_PREFIX+'metadata.json'),None)}
            return self.commit_owned(request_id,receipt,writes,removes)
    def owner_files(self,owner):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',str(owner)):raise ValueError('owner')
        return self.scoped_owner_files(self.safe(owner),owner)
    def scoped_owner_files(self,root,owner,*,single_volume=False):
        out={}
        if not root.exists():return out
        if single_volume:
            import stat
            root_stat=root.stat(follow_symlinks=False)
            if not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_dev!=self.root.stat().st_dev:
                raise Conflict('owner folder type')
        for p in sorted(root.rglob('*')):
            self.safe(p.relative_to(self.root).as_posix())
            if single_volume:
                st=p.stat(follow_symlinks=False)
                if st.st_dev!=root_stat.st_dev or not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode) and st.st_nlink==1):
                    raise Conflict('owner folder entry type')
            if p.is_file():out[owner+'/'+p.relative_to(root).as_posix()]=p.read_bytes()
        return out
    def recover_owner_folder_move(self,tx):
        # Private legacy-compatible owner journal, never callable as a generic
        # arbitrary-path RPC. Catalog and generation writes follow the move;
        # every lock user runs this recovery before observing the canonical tree.
        from single_target_delete import SingleTargetDelete
        receipt=tx['receipt'];command=tx['move'];owner=receipt['deletedCharacter']
        expected={'operationId':receipt['requestId'],'target':{'root':NPC_PREFIX.rstrip('/'),'path':owner,'kind':'directory'}}
        if command!=expected or tx['removes'] or tx['requestId']!=command['operationId'] or not receipt.get('workflowDeleteBindingHash'):
            raise Conflict('owner folder journal binding')
        manifest,_=self.verified_restorable_backup(receipt['backupId'])
        if manifest['characterId']!=owner or digest(manifest)!=receipt['backupManifestHash']:
            raise Conflict('owner folder backup changed')
        wanted={path:sha for path,sha in manifest['files'].items() if path!='metadata.json'}
        def verified(root):
            return {path:hashlib.sha256(value).hexdigest() for path,value in
                    self.scoped_owner_files(root,owner,single_volume=True).items()}==wanted
        original=self.safe(owner)
        if original.exists() and not verified(original):
            raise Conflict('owner folder changed before quarantine')
        applied=SingleTargetDelete(self,NPC_PREFIX.rstrip('/')).remove_locked(command)
        if applied['recoveryId']!=receipt['storageRecoveryId'] or not verified(self.safe('.storage-quarantine/'+applied['recoveryId']+'/target')):
            raise Conflict('owner folder quarantine unverified')
    def verified_backup(self,backup_id):
        if not re.fullmatch('[0-9a-f]{64}',str(backup_id)):raise ValueError('backup id')
        base=self.safe('.deleted/'+backup_id);m=json.loads((base/'manifest.json').read_bytes());data={}
        for rel,h in m['files'].items():
            p=self.safe('.deleted/'+backup_id+'/files/'+rel);b=p.read_bytes()
            if hashlib.sha256(b).hexdigest()!=h:raise Conflict('backup integrity')
            data[rel]=b
        return m,data
    def verify_owner_closure(self,owner,files):
        """Verify declared current dependencies, not historic provenance/shared data."""
        docs={k[len(owner)+1:]:planning_projection(k,json.loads(v),files.__getitem__) for k,v in files.items() if k.startswith(owner+'/') and k.endswith('.json') and not any(x.startswith('.') for x in k.split('/'))}
        expected=ownership_plan(owner,docs)
        manifest_path=owner+'/.owned-assets.json'
        if manifest_path not in files or json.loads(files[manifest_path])!=expected:raise Conflict('owner closure manifest mismatch')
        for slot,item in expected['slots'].items():
            data=files.get(owner+'/'+slot)
            if data is None or hashlib.sha256(data).hexdigest()!=item['sha256']:raise Conflict('owner closure missing or corrupt asset')
        return {'slotCount':len(expected['slots']),'ownerManifestHash':digest(expected),'nonSlotReferences':expected['nonSlotReferences'],'scope':'declared-current-owner-dependencies'}
    def verified_restorable_backup(self,backup_id):
        manifest,data=self.verified_backup(backup_id)
        owner=manifest.get('characterId')
        if manifest.get('schemaVersion')!='npc-owned-backup.v1':raise Conflict('backup not restorable owned schema')
        if not isinstance(owner,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',owner):raise Conflict('backup owner identity')
        if not {'metadata.json',owner+'/metadata.json'}<=set(data):raise Conflict('incomplete backup owner')
        if any(path!='metadata.json' and not path.startswith(owner+'/') for path in data):raise Conflict('backup outside owner scope')
        if json.loads(data[owner+'/metadata.json']).get('id')!=owner:raise Conflict('backup owner identity')
        self.verify_owner_closure(owner,data)
        return manifest,data
    def stage_owner_backup(self,request_id,owner,expected_source,*,single_volume=False):
        files=self.scoped_owner_files(self.safe(owner),owner,single_volume=True) if single_volume else self.owner_files(owner)
        closure=self.verify_owner_closure(owner,files)
        files['metadata.json']=self.safe('metadata.json').read_bytes()
        bid=hashlib.sha256(request_id.encode()).hexdigest()
        manifest={'schemaVersion':'npc-owned-backup.v1','characterId':owner,'sourceVersion':expected_source,'files':{k:hashlib.sha256(v).hexdigest() for k,v in files.items()}}
        base=self.safe('.deleted/'+bid)
        if (base/'manifest.json').exists():
            prior,data=self.verified_restorable_backup(bid)
            if prior!=manifest or data!=files:raise Conflict('staged backup changed')
        else:
            for rel,b in files.items():self.write(self.safe('.deleted/'+bid+'/files/'+rel),b)
            self.write(base/'manifest.json',encoded(manifest));self.verified_restorable_backup(bid)
        return bid,manifest,files,closure
    def delete_character(self,request_id,input_hash,character_id,expected_source,metadata_after,*,move=None):
        payload_hash=digest([character_id,expected_source,metadata_after])
        with self.lock():
            rp=self.receipt_path(request_id)
            if rp.exists():
                r=json.loads(rp.read_bytes())
                if r['inputHash']!=input_hash or r['payloadHash']!=payload_hash:raise Conflict('request reused')
                return r
            s=self.snapshot_unlocked()
            if s['sourceVersion']!=expected_source:raise Conflict('stale delete')
            old=s['documents']['metadata.json'];expected=copy.deepcopy(old)
            if character_id not in old['characterOrder']:raise ValueError('unknown owner')
            if character_id not in self.manifests():raise ValueError('ownership migration required')
            expected['characterOrder']=[x for x in old['characterOrder']if x!=character_id];expected['generationEpoch']=request_id
            check=copy.deepcopy(metadata_after);check['_pdsCurrent']=expected.get('_pdsCurrent');expected.setdefault('_pdsCurrent',None)
            if check!=expected:raise ValueError('delete metadata contract')
            checkdocs={k:v for k,v in s['documents'].items()if not k.startswith(character_id+'/')};checkdocs['metadata.json']=metadata_after
            self.check_document_hash(checkdocs)
            backup_id,manifest,files,closure=(self.stage_owner_backup(request_id,character_id,expected_source,single_volume=True)
                                          if move is not None else self.stage_owner_backup(request_id,character_id,expected_source))
            receipt={'requestId':request_id,'inputHash':input_hash,'payloadHash':payload_hash,'status':'committed','backupVerified':True,'backupId':backup_id,'backupManifestHash':digest(manifest),'backupFileCount':len(files),'deletedCharacter':character_id,'resultMetadata':metadata_after.get('_pdsCurrent'),'ownerManifestHash':digest(self.manifests()[character_id]),'dependencyClosure':closure}
            if move is not None:
                storage_id=hashlib.sha256(json.dumps([NPC_PREFIX.rstrip('/'),request_id],separators=(',',':')).encode()).hexdigest()
                return self.commit_owned(request_id,{**receipt,'storageRecoveryId':storage_id},
                                         {'metadata.json':encoded(metadata_after)},move=move)
            return self.commit_owned(request_id,receipt,{'metadata.json':encoded(metadata_after)},[k for k in files if k!='metadata.json'])
    def restore_character(self,request_id,input_hash,backup_id,expected_source,metadata_after):
        # Candidate endpoint deliberately NOT exposed until PDS CAS routing is integrated.
        payload_hash=digest([backup_id,expected_source,metadata_after])
        with self.lock():
            self.verified_restorable_backup(backup_id)
            rp=self.receipt_path(request_id)
            if rp.exists():
                r=json.loads(rp.read_bytes())
                if r['inputHash']!=input_hash or r['payloadHash']!=payload_hash:raise Conflict('request reused')
                return r
            s=self.snapshot_unlocked()
            if s['sourceVersion']!=expected_source:raise Conflict('stale restore')
            m,files=self.verified_restorable_backup(backup_id);owner=m['characterId'];old=s['documents']['metadata.json']
            if owner in old['characterOrder'] or self.owner_files(owner):raise Conflict('restore owner exists')
            expected=copy.deepcopy(old);expected['characterOrder']=[*old['characterOrder'],owner];expected['generationEpoch']=request_id
            check=copy.deepcopy(metadata_after);check['_pdsCurrent']=expected.get('_pdsCurrent');expected.setdefault('_pdsCurrent',None)
            if check!=expected:raise ValueError('restore metadata contract')
            writes={k:v for k,v in files.items()if k!='metadata.json'};writes['metadata.json']=encoded(metadata_after)
            checkdocs=copy.deepcopy(s['documents'])
            checkdocs.update({k:planning_projection(k,json.loads(v),writes.__getitem__)for k,v in writes.items()if k.endswith('.json') and not any(x.startswith('.')for x in k.split('/'))})
            self.check_document_hash(checkdocs)
            receipt={'requestId':request_id,'inputHash':input_hash,'payloadHash':payload_hash,'status':'committed','restoredCharacter':owner,'backupId':backup_id,'backupVerified':True,'backupManifestHash':digest(m)}
            return self.commit_owned(request_id,receipt,writes)

    def check_document_hash(self,docs):
        expected=docs['metadata.json'].get('_pdsCurrent',{}).get('documentHash')
        value=decode(docs);value.pop('_pdsCurrent',None)
        if expected is not None and digest(value)!=expected:raise Conflict('document hash')
