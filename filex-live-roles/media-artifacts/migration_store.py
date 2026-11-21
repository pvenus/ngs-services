"""Authenticated, explicitly selected NPC migration. Never deletes legacy assets."""
import copy,hashlib,json,re,secrets
from npc_store import encoded,digest,NPC_PREFIX
from npc_document import decode
from ownership_plan import plan as ownership_plan
from owned_store import OwnedNPCStore
from server import Conflict

class MigrationMixin:
    def require_no_migration(self):
        p=self.safe('.migration-fence.json')
        if p.exists() and json.loads(p.read_bytes()).get('state')=='prepared':raise Conflict('global migration fence')
    def migration_path(self,rid):
        if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,180}',rid):raise ValueError('migration request')
        return '.migrations/'+hashlib.sha256(rid.encode()).hexdigest()+'/plan.json'
    def migration_state(self,rid,token=None):
        s=json.loads(self.safe(self.migration_path(rid)).read_bytes())
        if token is not None and not secrets.compare_digest(s['token'],token):raise Conflict('migration token')
        return s
    def migration_status(self,request_id):
        with self.lock():
            s=self.migration_state(request_id)
            return {k:v for k,v in s.items() if k not in ('token','plan')} | {'requestId':request_id}
    def migration_prepare(self,plan):
        required={'npcId','requestId','expectedSourceVersion','planHash','changedDocuments','importAssets'}
        if not isinstance(plan,dict) or not required<=set(plan) or set(plan)-required-{'reviewedPlanHash'}:raise ValueError('migration plan')
        if plan['planHash']!=digest({k:v for k,v in plan.items() if k!='planHash'}):raise ValueError('migration plan hash')
        owner=plan['npcId'];self.state_path(owner);rid=plan['requestId'];path=self.migration_path(rid)
        with self.lock():
            if self.safe(path).exists():
                s=self.migration_state(rid)
                if s['plan']!=plan:raise Conflict('migration request reused')
                return {'requestId':rid,'token':s['token'],'planHash':plan['planHash'],'state':s['state']}
            self.require_no_migration()
            for p in self.safe('.generations').glob('*.json'):
                self.safe(p.relative_to(self.root).as_posix());g=json.loads(p.read_bytes())
                if g['state'] not in ('active','deleted') or g.get('leases') or g.get('cleanupReservations') or g.get('frameMaintenance'):raise Conflict('migration requires global idle')
            snap=self.snapshot_unlocked()
            if snap['sourceVersion']!=plan['expectedSourceVersion']:raise Conflict('stale migration')
            if owner not in snap['documents']['metadata.json']['characterOrder']:raise Conflict('unknown migration owner')
            docs={k[len(owner)+1:]:copy.deepcopy(v) for k,v in snap['documents'].items() if k.startswith(owner+'/')}
            if not isinstance(plan['changedDocuments'],dict):raise ValueError('changed documents')
            for rel,body in plan['changedDocuments'].items():
                self.path(NPC_PREFIX+owner+'/'+rel)
                if not isinstance(body,dict):raise ValueError('changed document body')
                docs[rel]=body
            if docs.get('metadata.json',{}).get('id')!=owner:raise ValueError('migration owner identity')
            manifest=ownership_plan(owner,docs);imports={}
            for item in plan['importAssets']:
                if set(item)!={'assetId','sha256'} or not re.fullmatch(r'[0-9a-f]{64}\.(png|jpg|webp|mp4)',item['assetId']) or item['sha256']!=item['assetId'].split('.')[0] or item['assetId'] in imports:raise ValueError('migration imports')
                imports[item['assetId']]=item['sha256']
            if not set(imports)<={x['assetId'] for x in manifest['slots'].values()}:raise ValueError('unreferenced import')
            s={'state':'prepared','token':secrets.token_hex(32),'plan':copy.deepcopy(plan),'npcId':owner,'planHash':plan['planHash']}
            # Reservation and plan are one recoverable transaction. No source mutation.
            OwnedNPCStore.commit_owned(self,'migration.prepare.'+rid,{'status':'prepared','requestId':'migration.prepare.'+rid}, {path:encoded(s),'.migration-fence.json':encoded({'state':'prepared','requestId':rid})})
            return {'requestId':rid,'token':s['token'],'planHash':plan['planHash'],'state':'prepared'}
    def migration_asset(self,request_id,token,asset_id,data):
        with self.lock():
            s=self.migration_state(request_id,token)
            allowed={x['assetId']:x['sha256'] for x in s['plan']['importAssets']}
            if s['state']!='prepared' or asset_id not in allowed or hashlib.sha256(data).hexdigest()!=allowed[asset_id]:raise Conflict('migration import mismatch')
            p=self.safe(self.migration_path(request_id).rsplit('/',1)[0]+'/imports/'+asset_id)
            if p.exists() and p.read_bytes()!=data:raise Conflict('migration import collision')
            if not p.exists():self.write(p,data)
            return {'assetId':asset_id,'sha256':allowed[asset_id],'verified':True}
    def migration_activate(self,request_id,expected_source,plan_hash,backup_manifest_hash):
        """Explicit administrative bridge; never called by producer admission."""
        with self.lock():
            self.require_no_migration()
            migration=self.migration_state(request_id)
            if migration['state']!='committed':raise Conflict('migration not committed')
            receipt=migration['receipt'];owner=migration['npcId']
            if receipt['planHash']!=plan_hash or receipt['backupManifestHash']!=backup_manifest_hash:raise Conflict('activation receipt binding')
            prior=migration.get('activation')
            if prior:
                if prior['sourceVersion']!=expected_source:raise Conflict('activation request reused')
                state=self.state(owner)
                if state['generationId']!=prior['generationId'] or state['state']!='active':raise Conflict('activation generation retired')
                return prior
            snapshot=self.snapshot_unlocked()
            if snapshot['sourceVersion']!=expected_source:raise Conflict('stale activation source')
            if owner not in snapshot['documents']['metadata.json']['characterOrder']:raise Conflict('activation owner absent')
            backup,_=self.verified_backup(receipt['backupId'])
            if digest(backup)!=backup_manifest_hash:raise Conflict('activation backup changed')
            manifest=self.manifests().get(owner)
            if manifest is None or len(manifest['slots'])!=receipt['slotCount']:raise Conflict('activation ownership missing')
            expected_manifest=ownership_plan(owner,{k[len(owner)+1:]:v for k,v in snapshot['documents'].items() if k.startswith(owner+'/')})
            if manifest!=expected_manifest:raise Conflict('activation manifest lineage changed')
            for slot,item in manifest['slots'].items():
                if hashlib.sha256(self.safe(owner+'/'+slot).read_bytes()).hexdigest()!=item['sha256']:raise Conflict('activation slot integrity')
            state_path=self.safe(self.state_path(owner))
            if state_path.exists():
                state=self.state(owner)
                if state['state']!='active' or state.get('leases') or state.get('cleanupReservations') or state.get('frameMaintenance'):raise Conflict('activation owner busy or retired')
            else:state={'npcId':owner,'generationId':secrets.token_hex(24),'state':'active','leases':{}}
            result={'requestId':request_id,'npcId':owner,'generationId':state['generationId'],'state':'active','activeLeases':0,'sourceVersion':expected_source,'planHash':plan_hash,'backupManifestHash':backup_manifest_hash,'status':'committed','adminActivated':True}
            migration['activation']=result
            return OwnedNPCStore.commit_owned(self,'migration.activate.'+request_id,result,{self.state_path(owner):encoded(state),self.migration_path(request_id):encoded(migration)})
    def migration_commit(self,request_id,token):
        with self.lock():
            s=self.migration_state(request_id,token)
            if s['state']=='committed':return s['receipt']
            fence=json.loads(self.safe('.migration-fence.json').read_bytes())
            if fence!={'state':'prepared','requestId':request_id}:raise Conflict('migration fence changed')
            p=s['plan'];owner=p['npcId'];snap=self.snapshot_unlocked()
            if snap['sourceVersion']!=p['expectedSourceVersion']:raise Conflict('stale migration')
            after=copy.deepcopy(snap['documents']);writes={}
            for rel,body in p['changedDocuments'].items():after[owner+'/'+rel]=copy.deepcopy(body)
            docs={k[len(owner)+1:]:v for k,v in after.items() if k.startswith(owner+'/')};manifest=ownership_plan(owner,docs)
            imported={}
            for item in p['importAssets']:
                b=self.safe(self.migration_path(request_id).rsplit('/',1)[0]+'/imports/'+item['assetId']).read_bytes()
                if hashlib.sha256(b).hexdigest()!=item['sha256']:raise Conflict('migration import integrity')
                imported[item['assetId']]=b
            backup=self.owner_files(owner);backup['metadata.json']=self.safe('metadata.json').read_bytes()
            # Include old referenced legacy bytes AND imported source originals.
            oldrefs=set(re.findall(r'[0-9a-f]{64}\.(?:png|jpg|webp|mp4)',encoded({k:v for k,v in snap['documents'].items() if k.startswith(owner+'/')}).decode()))
            for aid in oldrefs:
                b=self.asset_path(aid).read_bytes()
                if hashlib.sha256(b).hexdigest()!=aid.split('.')[0]:raise Conflict('old source integrity')
                backup['assets/'+aid]=b
            for aid,b in imported.items():backup['imports/'+aid]=b
            for slot,item in manifest['slots'].items():
                b=imported[item['assetId']] if item['assetId'] in imported else self.asset_path(item['assetId']).read_bytes()
                if hashlib.sha256(b).hexdigest()!=item['sha256']:raise Conflict('migration slot integrity')
                writes[owner+'/'+slot]=b
            root=copy.deepcopy(after['metadata.json']);marker=dict(root.get('_pdsCurrent',{}));marker.update(revision=marker.get('revision',0)+1,note='NPC owned migration '+request_id)
            root['_pdsCurrent']=marker;after['metadata.json']=root;decoded=decode(after);decoded.pop('_pdsCurrent',None);marker['documentHash']=digest(decoded)
            self.check_document_hash(after)
            writes['metadata.json']=encoded(root)
            for rel in p['changedDocuments']:writes[owner+'/'+rel]=encoded(after[owner+'/'+rel])
            writes[owner+'/.owned-assets.json']=encoded(manifest)
            bid=hashlib.sha256(('migration:'+request_id).encode()).hexdigest()
            bm={'schemaVersion':'npc-pre-migration-backup.v1','characterId':owner,'sourceVersion':p['expectedSourceVersion'],'planHash':p['planHash'],'files':{k:hashlib.sha256(b).hexdigest() for k,b in backup.items()}}
            for rel,b in backup.items():self.write(self.safe('.deleted/'+bid+'/files/'+rel),b)
            self.write(self.safe('.deleted/'+bid+'/manifest.json'),encoded(bm));self.verified_backup(bid)
            receipt={'requestId':request_id,'status':'committed','npcId':owner,'planHash':p['planHash'],'backupId':bid,'backupVerified':True,'backupManifestHash':digest(bm),'slotCount':len(manifest['slots']),'legacyAssetsPreserved':True,'resultMetadata':marker}
            writes[self.migration_path(request_id)]=encoded(dict(s,state='committed',receipt=receipt));writes['.migration-fence.json']=encoded({'state':'committed','requestId':request_id})
            return OwnedNPCStore.commit_owned(self,request_id,receipt,writes)
