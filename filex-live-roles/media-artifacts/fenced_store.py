"""Opt-in authoritative cross-host generation/lease fence. No TTL steal or purge.
Only authenticated PDS adapters may call these methods. Legacy mutation clients
fail closed; read and hash-download contracts remain compatible.
"""
import json, re, secrets, threading
from contextlib import contextmanager
from owned_store import OwnedNPCStore
from npc_store import NPC_PREFIX, encoded, digest
from server import Conflict

from migration_store import MigrationMixin
from frame_maintenance import FrameMaintenanceMixin

from portrait_assets import PortraitAssets

class FencedOwnedNPCStore(PortraitAssets,FrameMaintenanceMixin,MigrationMixin,OwnedNPCStore):
    def __init__(self, root):
        super().__init__(root); self._local=threading.local()
    @contextmanager
    def lock(self):
        # Inherited transactions reenter on the same thread, never across hosts.
        if getattr(self._local,'depth',0):
            self._local.depth+=1
            try: yield
            finally: self._local.depth-=1
        else:
            with super().lock():
                self._local.depth=1
                try: yield
                finally: self._local.depth=0
    def state_path(self,owner):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',str(owner)): raise ValueError('owner')
        return '.generations/'+owner+'.json'
    def state(self,owner):
        return json.loads(self.safe(self.state_path(owner)).read_bytes())
    def generation(self,owner):
        with self.lock():
            s=self.state(owner)
            return {k:s[k] for k in ('npcId','generationId','state')} | {'activeLeases':len(s['leases']),'cleanupReservations':len(s.get('cleanupReservations',{}))+len(s.get('frameMaintenance',{}).get('reservations',{})),'activeMaintenance':int(bool(s.get('frameMaintenance')))}
    def publication_receipts(self,owner,generation_id,parent_id):
        # Read-only authoritative lineage; no filesystem paths supplied by clients.
        with self.lock():
            state=self.state(owner)
            if state['generationId']!=generation_id:raise Conflict('publication generation changed')
            if parent_id not in state['leases'] and parent_id not in state.get('finished',{}):raise Conflict('unknown admitted parent')
            receipts=[]
            for path in sorted(self.safe('.requests').glob('*.json')):
                if path.is_symlink():raise ValueError('receipt symlink')
                r=json.loads(path.read_bytes())
                if r.get('generationId')==generation_id and r.get('parentRequestId')==parent_id:
                    if r.get('status')!='committed':raise Conflict('publication not committed')
                    receipts.append(dict(r,npcId=owner,verified=True))
            return {'npcId':owner,'generationId':generation_id,'parentRequestId':parent_id,'receipts':receipts}
    def current_equivalent(self,owner,generation_id,parent_id,sha256):
        import hashlib,base64
        if not re.fullmatch('[0-9a-f]{64}',str(sha256)):raise ValueError('equivalent hash')
        with self.lock():
            state=self.state(owner)
            if state['state']!='active' or state['generationId']!=generation_id:raise Conflict('equivalent generation changed')
            receipts=self.publication_receipts(owner,generation_id,parent_id)['receipts']
            if not receipts:raise Conflict('no publication receipt')
            manifest=self.manifests().get(owner,{'slots':{}})
            for slot,item in manifest['slots'].items():
                if item['assetId'].split('.')[0]!=sha256:continue
                data=self.safe(owner+'/'+slot).read_bytes()
                if hashlib.sha256(data).hexdigest()!=sha256:raise Conflict('equivalent bytes changed')
                return {'npcId':owner,'generationId':generation_id,'sha256':sha256,'currentSlot':True,'slotPath':owner+'/'+slot,'publicationReceiptId':receipts[-1]['requestId'],'base64':base64.b64encode(data).decode()}
            return {'npcId':owner,'generationId':generation_id,'sha256':sha256,'currentSlot':False}
    def reserve_equivalent(self,owner,generation_id,parent_id,sha256,reservation_id):
        if not re.fullmatch('[0-9a-f]{64}',str(reservation_id)):raise ValueError('reservation id')
        with self.lock():
            state=self.state(owner)
            if state['state']!='active' or state['generationId']!=generation_id:raise Conflict('reservation generation changed')
            if parent_id not in state['leases']:raise Conflict('reservation requires active cleanup parent lease')
            if reservation_id in state.get('releasedCleanups',{}):raise Conflict('reservation already released')
            entries=state.setdefault('cleanupReservations',{})
            old=entries.get(reservation_id)
            if old and (old['parentRequestId']!=parent_id or old['sha256']!=sha256):raise Conflict('reservation reused')
            proof=self.current_equivalent(owner,generation_id,parent_id,sha256)
            if proof['currentSlot'] is not True:return proof
            if not old:
                old={'parentRequestId':parent_id,'sha256':sha256,'token':secrets.token_hex(32)}
                entries[reservation_id]=old
                self.write(self.safe(self.state_path(owner)),encoded(state))
            return dict(proof,reservationId=reservation_id,reservationToken=old['token'])
    def release_equivalent(self,owner,generation_id,parent_id,reservation_id,token):
        with self.lock():
            state=self.state(owner)
            if state['generationId']!=generation_id:raise Conflict('release generation changed')
            entries=state.get('cleanupReservations',{})
            prior=state.get('releasedCleanups',{}).get(reservation_id)
            if prior:
                if prior['parentRequestId']!=parent_id or prior['token']!=token:raise Conflict('release token mismatch')
                return {'released':True}
            old=entries.get(reservation_id)
            if not old or old['parentRequestId']!=parent_id or old['token']!=token:raise Conflict('release token mismatch')
            del entries[reservation_id];state.setdefault('releasedCleanups',{})[reservation_id]=old
            self.write(self.safe(self.state_path(owner)),encoded(state));return {'released':True}
    def require_no_cleanup(self,state):
        if state.get('frameMaintenance'):raise Conflict('frame maintenance active')
        if state.get('cleanupReservations'):raise Conflict('current-equivalent cleanup reserved')
    def bootstrap(self,owner,expected_source):
        with self.lock():
            self.require_no_migration()
            if self.safe(self.state_path(owner)).exists():return self.generation(owner)
            snap=self.snapshot_unlocked()
            if snap['sourceVersion']!=expected_source:raise Conflict('stale bootstrap')
            if owner not in snap['documents']['metadata.json']['characterOrder']:raise Conflict('unknown owner')
            s={'npcId':owner,'generationId':secrets.token_hex(24),'state':'active','leases':{}}
            self.write(self.safe(self.state_path(owner)),encoded(s));return self.generation(owner)
    def admit(self,owner,generation_id,request_id):
        if not isinstance(request_id,str) or not request_id:raise ValueError('request')
        with self.lock():
            self.require_no_migration()
            s=self.state(owner);self.require_no_cleanup(s)
            if s['state']!='active' or s['generationId']!=generation_id:raise Conflict('generation fenced')
            if request_id in s.get('finished',{}) or request_id in s.get('retiredRequestIds',[]):raise Conflict('parent already finished or retired')
            lease=s['leases'].get(request_id)
            if lease is None:
                lease=secrets.token_hex(32);s['leases'][request_id]=lease;self.write(self.safe(self.state_path(owner)),encoded(s))
            return {'npcId':owner,'generationId':generation_id,'requestId':request_id,'leaseToken':lease}
    def checked(self,guard,request_id=None):
        self.require_no_migration()
        if not isinstance(guard,dict):raise ValueError('generation lease required')
        s=self.state(guard['npcId']);self.require_no_cleanup(s)
        if s['state']!='active' or s['generationId']!=guard['generationId'] or s['leases'].get(guard['requestId'])!=guard['leaseToken']:raise Conflict('stale generation or lease')
        if request_id is not None and guard['requestId']!=request_id:raise Conflict('request lease mismatch')
        return s
    def finish(self,guard):
        with self.lock():
            if not isinstance(guard,dict):raise ValueError('guard')
            s=self.state(guard['npcId']);self.require_no_cleanup(s)
            if s['generationId']==guard['generationId'] and s.get('finished',{}).get(guard['requestId'])==guard['leaseToken']:return {'released':True}
            s=self.checked(guard);del s['leases'][guard['requestId']];s.setdefault('finished',{})[guard['requestId']]=guard['leaseToken'];self.write(self.safe(self.state_path(s['npcId'])),encoded(s))
            return {'released':True}
    def put_asset(self,asset_id,data,guard=None):
        with self.lock():self.checked(guard);return super().put_asset(asset_id,data)
    def batch(self,request_id,input_hash,files,read_set,guard=None):
        with self.lock():
            self.checked(guard)
            owners={x['path'].removeprefix(NPC_PREFIX).split('/')[0] for x in files if x['path']!=NPC_PREFIX+'metadata.json'}
            if owners not in ({guard['npcId']},set()):raise ValueError('single owner publication required')
            # Root metadata cannot introduce/remove another owner through publication.
            for item in files:
                if item['path']==NPC_PREFIX+'metadata.json':
                    old=self.snapshot_unlocked()['documents']['metadata.json']
                    if {k:v for k,v in item['body'].items() if k!='_pdsCurrent'}!={k:v for k,v in old.items() if k!='_pdsCurrent'}:raise Conflict('lifecycle mutation forbidden')
            self._local.publication=guard
            try:
                result=super().batch(request_id,input_hash,files,read_set)
                if result.get('generationId')!=guard['generationId'] or result.get('parentRequestId')!=guard['requestId']:raise Conflict('operation parent changed')
                return result
            finally:self._local.publication=None
    def staged_delete_fence(self,owner,generation_id,request_id):
        """Read-only request-bound fence proof; never prepares or repairs state."""
        with self.lock():
            self.require_no_migration();s=self.state(owner);self.require_no_cleanup(s)
            staged=s.get('stagedDelete',{})
            if s['generationId']!=generation_id or s['state']!='delete-staged' or s['leases'] or staged.get('requestId')!=request_id:raise Conflict('staged fence binding')
            source=staged.get('expectedSourceVersion')
            if not source or self.snapshot_unlocked()['sourceVersion']!=source:raise Conflict('staged fence source changed')
            binding={'npcId':owner,'generationId':generation_id,'requestId':request_id,'sourceVersion':source}
            fence_id=digest({'schema':'npc-staged-delete-fence.v1',**binding})
            return {**binding,'state':'delete-staged','activeLeases':0,'admissionBlocked':True,'fenceId':fence_id,'fenceToken':fence_id}
    def stage_delete(self,owner,generation_id,request_id,expected_source):
        if not isinstance(request_id,str) or not request_id:raise ValueError('deletion request')
        with self.lock():
            self.require_no_migration();state=self.state(owner);self.require_no_cleanup(state)
            binding={'requestId':request_id,'expectedSourceVersion':expected_source}
            if state['generationId']!=generation_id:raise Conflict('generation changed')
            if state['state'] not in ('active','delete-staged','deleting') or state['leases']:raise Conflict('active lease or fenced')
            if state['state']!='active' and state.get('stagedDelete')!=binding:raise Conflict('staged deletion changed')
            snap=self.snapshot_unlocked()
            if snap['sourceVersion']!=expected_source:raise Conflict('stale staged deletion')
            # Persist admission fence before backup. Any failure keeps source and fence.
            if state['state']=='active':
                state.update(state='delete-staged',stagedDelete=binding);self.write(self.safe(self.state_path(owner)),encoded(state))
            bid,manifest,files,closure=self.stage_owner_backup(request_id,owner,expected_source)
            import re as _re
            owner_docs={k[len(owner)+1:]:json.loads(v) for k,v in files.items() if k.startswith(owner+'/') and k.endswith('.json') and not any(x.startswith('.') for x in k.split('/'))}
            unsupported=[k for k in owner_docs if k!='metadata.json' and not k.startswith(('planning/','pose-views/','animations/','animation-frames/','animation-experiments/'))]
            provenance=set()
            for path,doc in owner_docs.items():
                if path.startswith('animation-frames/'):
                    provenance.update(row.get('sourceAssetId') for row in doc.get('directions',{}).values())
                provenance.update(_re.findall(r'[0-9a-f]{64}\.(?:png|jpg|webp|mp4)',json.dumps(doc.get('trace',{}))))
            unsupported.extend(aid for aid in closure['nonSlotReferences'] if aid not in provenance)
            nodes={k:{'sha256':manifest['files'][k],'verifiedBytes':True,'referenceScanComplete':True,'references':[]} for k in files}
            graph={'npcId':owner,'generationId':generation_id,'sourceVersion':expected_source,'complete':not unsupported,'unsupportedReferences':sorted(unsupported),'roots':sorted(nodes),'nodes':nodes,'scope':'declared-current-owner-dependencies','nonSlotProvenance':closure['nonSlotReferences']}
            archive={'npcId':owner,'generationId':generation_id,'sourceVersion':expected_source,'verified':True,'backupId':bid,'backupManifestHash':digest(manifest),'entries':manifest['files']}
            fence={'npcId':owner,'generationId':generation_id,'admissionBlocked':True,'activeLeases':0,'legacyResumeBlocked':False,'legacyResumeEvidenceRequired':True}
            return {'npcId':owner,'generationId':generation_id,'requestId':request_id,'graph':graph,'archive':archive,'fence':fence,'backupId':bid,'backupManifestHash':digest(manifest),'sourceVersion':expected_source,'ownerManifestHash':closure['ownerManifestHash']}
    def prepare_delete(self,owner,generation_id,request_id,external_hash):
        if not re.fullmatch('[0-9a-f]{64}',str(external_hash)) or not request_id:raise ValueError('deletion binding')
        with self.lock():
            self.require_no_migration()
            s=self.state(owner);self.require_no_cleanup(s);binding={'requestId':request_id,'externalOwnerManifestHash':external_hash}
            if s['generationId']!=generation_id:raise Conflict('generation changed')
            if s['state']=='deleting' and s.get('delete')==binding:return self.generation(owner)
            if s['state'] not in ('active','delete-staged') or s['leases']:raise Conflict('active lease or fenced')
            if s['state']=='delete-staged':
                if s['stagedDelete']['requestId']!=request_id:raise Conflict('staged deletion request mismatch')
                self.stage_delete(owner,generation_id,request_id,s['stagedDelete']['expectedSourceVersion'])
            s.update(state='deleting',delete=binding);self.write(self.safe(self.state_path(owner)),encoded(s));return self.generation(owner)
    def delete_character(self,request_id,input_hash,character_id,expected_source,metadata_after,guard=None):
        with self.lock():
            self.require_no_migration()
            s=self.state(character_id);self.require_no_cleanup(s)
            if not isinstance(guard,dict) or guard.get('generationId')!=s['generationId']:raise Conflict('deletion generation required')
            if s['state']=='deleted':
                r=self.receipt(request_id)
                if r['inputHash']!=input_hash or r['payloadHash']!=digest([character_id,expected_source,metadata_after]):raise Conflict('request reused')
                return r
            if s['state']!='deleting' or s['leases'] or s['delete']['requestId']!=request_id:raise Conflict('deletion not prepared')
            if s.get('stagedDelete'):
                if s['stagedDelete']!={'requestId':request_id,'expectedSourceVersion':expected_source}:raise Conflict('staged deletion source mismatch')
                self.stage_delete(character_id,s['generationId'],request_id,expected_source)
            self._local.deletion=s
            try:return super().delete_character(request_id,input_hash,character_id,expected_source,metadata_after)
            finally:self._local.deletion=None
    def commit_owned(self,request_id,receipt,writes,removes=()):
        publication=getattr(self._local,'publication',None)
        if publication:receipt=dict(receipt,generationId=publication['generationId'],parentRequestId=publication['requestId'],stageRequestId=publication.get('stageRequestId',publication['requestId']))
        s=getattr(self._local,'deletion',None)
        if s:
            s=dict(s,state='deleted');writes=dict(writes);writes[self.state_path(s['npcId'])]=encoded(s)
            receipt=dict(receipt,generationId=s['generationId'],externalOwnerManifestHash=s['delete']['externalOwnerManifestHash'])
        lifecycle=getattr(self._local,'lifecycle',None)
        if lifecycle:
            state=dict(lifecycle,state='active');writes=dict(writes)
            writes[self.state_path(state['npcId'])]=encoded(state)
            receipt=dict(receipt,generationId=state['generationId'],parentRequestId=state['lifecycle']['requestId'],lifecycleOperation=state['lifecycle']['operation'])
        return super().commit_owned(request_id,receipt,writes,removes)
    def prepare_lifecycle(self,owner,request_id,expected_source,operation,backup_id=None):
        if operation not in ('create','restore') or not request_id:raise ValueError('lifecycle operation')
        with self.lock():
            self.require_no_migration()
            if operation=='restore':self.verified_restorable_backup(backup_id)
            path=self.safe(self.state_path(owner));old=json.loads(path.read_bytes()) if path.exists() else None
            if old:self.require_no_cleanup(old)
            binding={'requestId':request_id,'sourceVersion':expected_source,'operation':operation,'backupId':backup_id}
            if old and old.get('lifecycle') and all(old['lifecycle'].get(k)==v for k,v in binding.items()):
                if old['state'] in ('creating','restoring','active'):
                    return self.lifecycle_guard(old)
            snapshot=self.snapshot_unlocked()
            if snapshot['sourceVersion']!=expected_source:raise Conflict('stale lifecycle preparation')
            if owner in snapshot['documents']['metadata.json']['characterOrder'] or self.owner_files(owner):raise Conflict('owner already exists')
            if old and (old['state']!='deleted' or old['leases']):raise Conflict('owner not deleted')
            if operation=='restore':
                manifest,_=self.verified_restorable_backup(backup_id)
                if manifest['characterId']!=owner:raise Conflict('restore owner mismatch')
            elif backup_id is not None:raise ValueError('create backup binding')
            retired=set(old.get('retiredRequestIds',[])) if old else set()
            if old:
                retired.update(old.get('finished',{}));retired.update(old.get('leases',{}))
                for key in ('lifecycle','delete'):
                    if old.get(key):retired.add(old[key]['requestId'])
            if request_id in retired:raise Conflict('retired lifecycle request')
            state={'npcId':owner,'generationId':secrets.token_hex(24),'state':'creating' if operation=='create' else 'restoring','leases':{},'finished':{},'retiredRequestIds':sorted(retired),'previousGenerationId':old.get('generationId') if old else None,'lifecycle':dict(binding,token=secrets.token_hex(32))}
            self.write(path,encoded(state));return self.lifecycle_guard(state)
    def lifecycle_guard(self,state):
        return {'npcId':state['npcId'],'generationId':state['generationId'],'requestId':state['lifecycle']['requestId'],'operation':state['lifecycle']['operation'],'lifecycleToken':state['lifecycle']['token'],'expectedSourceVersion':state['lifecycle']['sourceVersion']}
    def checked_lifecycle(self,guard,operation,request_id,expected_source):
        if not isinstance(guard,dict):raise ValueError('lifecycle guard required')
        state=self.state(guard['npcId']);self.require_no_cleanup(state)
        if not state.get('lifecycle') or self.lifecycle_guard(state)!=guard:raise Conflict('stale lifecycle guard')
        binding=state['lifecycle']
        if binding['operation']!=operation or binding['requestId']!=request_id or binding['sourceVersion']!=expected_source:raise Conflict('lifecycle binding mismatch')
        if state['state'] not in ('creating' if operation=='create' else 'restoring','active'):raise Conflict('lifecycle state')
        return state
    def prepare_create(self,owner,request_id,expected_source):
        return self.prepare_lifecycle(owner,request_id,expected_source,'create')
    def prepare_restore(self,owner,request_id,backup_id,expected_source):
        return self.prepare_lifecycle(owner,request_id,expected_source,'restore',backup_id)
    def create_character(self,request_id,input_hash,files,read_set,expected_source,guard=None):
        with self.lock():
            self.require_no_migration()
            state=self.checked_lifecycle(guard,'create',request_id,expected_source);owner=state['npcId']
            existing=self.receipt_path(request_id)
            if not existing.exists():
                snapshot=self.snapshot_unlocked()
                if snapshot['sourceVersion']!=expected_source:raise Conflict('stale create')
                if owner in snapshot['documents']['metadata.json']['characterOrder'] or self.owner_files(owner):raise Conflict('owner exists')
                owners={x['path'].removeprefix(NPC_PREFIX).split('/')[0] for x in files if x['path']!=NPC_PREFIX+'metadata.json'}
                roots=[x['body']for x in files if x['path']==NPC_PREFIX+'metadata.json']
                if owners!={owner} or len(roots)!=1:raise ValueError('create scope')
                old=snapshot['documents']['metadata.json'];expected=dict(old,characterOrder=old['characterOrder']+[owner],generationEpoch=request_id)
                check=dict(roots[0]);check['_pdsCurrent']=expected.get('_pdsCurrent');expected.setdefault('_pdsCurrent',None)
                if check!=expected:raise Conflict('create metadata contract')
                docs=dict(snapshot['documents']);docs.update({x['path'].removeprefix(NPC_PREFIX):x['body']for x in files});self.check_document_hash(docs)
            self._local.lifecycle=state
            try:
                receipt=super().batch(request_id,input_hash,files,read_set)
                if receipt.get('generationId')!=state['generationId']:raise Conflict('create replay generation')
                return receipt
            finally:self._local.lifecycle=None
    def restore_character(self,request_id=None,input_hash=None,backup_id=None,expected_source=None,metadata_after=None,guard=None):
        with self.lock():
            self.require_no_migration()
            state=self.checked_lifecycle(guard,'restore',request_id,expected_source)
            if state['lifecycle']['backupId']!=backup_id:raise Conflict('restore backup binding')
            self._local.lifecycle=state
            try:
                receipt=super().restore_character(request_id,input_hash,backup_id,expected_source,metadata_after)
                if receipt.get('generationId')!=state['generationId']:raise Conflict('restore replay generation')
                return receipt
            finally:self._local.lifecycle=None
    def backup_preview(self,backup_id):
        """Authenticated route only: verify ALL backup bytes, return owner JSON."""
        with self.lock():
            manifest,data=self.verified_restorable_backup(backup_id);owner=manifest['characterId']
            self.state_path(owner) # strict owner validation
            documents={}
            for path,raw in data.items():
                if path.startswith(owner+'/') and path.endswith('.json') and not any(x.startswith('.') for x in path.split('/')):
                    self.path(NPC_PREFIX+path);documents[path]=json.loads(raw)
            if owner+'/metadata.json' not in documents:raise Conflict('incomplete backup owner')
            return {'backupId':backup_id,'characterId':owner,'backupManifestHash':digest(manifest),'backupVerified':True,'documents':documents}
