"""Opt-in authoritative cross-host generation/lease fence. No TTL steal or purge.
Only authenticated PDS adapters may call these methods. Legacy mutation clients
fail closed; read and hash-download contracts remain compatible.
"""
import hashlib, json, os, re, secrets, threading
from contextlib import contextmanager
from owned_store import OwnedNPCStore
from npc_store import NPC_PREFIX, encoded, digest
from server import Conflict

from migration_store import MigrationMixin
from frame_maintenance import FrameMaintenanceMixin

from portrait_assets import PortraitAssets
from workflow_native_media import WorkflowNativeMedia
from npc_planning_markdown import NPCPlanningMarkdown,planning_projection,markdown_path,planning_write_bytes

class FencedOwnedNPCStore(NPCPlanningMarkdown,WorkflowNativeMedia,PortraitAssets,FrameMaintenanceMixin,MigrationMixin,OwnedNPCStore):
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
    def replacement_evidence(self,owner,generation_id,request_id,expected_source,evidence):
        keys={'schemaVersion','npcId','generationId','requestId','expectedSourceVersion','parents','snapshotHash','proofHash'}
        binding={'npcId':owner,'generationId':generation_id,'requestId':request_id,'expectedSourceVersion':expected_source}
        if (not isinstance(evidence,dict) or set(evidence)!=keys or evidence['schemaVersion']!='npc-replacement-expired-leases.v1'
            or any(evidence[k]!=v for k,v in binding.items())
            or any(not isinstance(evidence[k],str) or not re.fullmatch('[a-f0-9]{64}',evidence[k]) for k in ('snapshotHash','proofHash'))
            or evidence['proofHash']!=digest({k:v for k,v in evidence.items() if k!='proofHash'})
            or not isinstance(evidence['parents'],list) or not evidence['parents']):
            raise Conflict('replacement evidence binding')
        parents={}
        for row in evidence['parents']:
            if not isinstance(row,dict) or set(row)!={'parentRequestId','guardHash','leaseTokenHash','publicationRequestIds','stageRequestIds','timeoutProofHashes','childScopeHashes','canonicalCommitRequestIds','artifactReservationIds'}:raise Conflict('replacement parent schema')
            parent=row['parentRequestId']
            if not isinstance(parent,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,200}',parent) or parent in parents or parent==request_id:raise Conflict('replacement parent binding')
            if any(not isinstance(row[k],str) or not re.fullmatch('[a-f0-9]{64}',row[k]) for k in ('guardHash','leaseTokenHash')):raise Conflict('replacement guard hash')
            for key,pattern in (('publicationRequestIds',r'[A-Za-z0-9._:-]{1,200}'),('stageRequestIds',r'[A-Za-z0-9._:-]{1,200}'),('timeoutProofHashes','[a-f0-9]{64}'),('childScopeHashes','[a-f0-9]{64}'),('canonicalCommitRequestIds',r'[A-Za-z0-9._:-]{1,200}'),('artifactReservationIds',r'asset\.[a-f0-9]{48}')):
                values=row[key]
                if not isinstance(values,list) or not values or any(not isinstance(v,str) or not re.fullmatch(pattern,v) for v in values) or len(set(values))!=len(values):raise Conflict('replacement scope binding')
            parents[parent]=row
        return parents

    def retire_for_replacement(self,state,binding,evidence,parents):
        leases=state['leases']
        if set(parents)!=set(leases):raise Conflict('replacement active leases changed')
        for parent,row in parents.items():
            owner,generation=state['npcId'],state['generationId']
            guard={'npcId':owner,'generationId':generation,'requestId':parent,'leaseToken':leases[parent]}
            if digest(guard)!=row['guardHash'] or hashlib.sha256(leases[parent].encode()).hexdigest()!=row['leaseTokenHash']:raise Conflict('replacement guard changed')
            # Request owns semantic completeness of these typed logical IDs.
            # Writer only resolves exact records using existing path contracts.
            for operation in row['canonicalCommitRequestIds']:
                if self.receipt_path(operation).exists():raise Conflict('replacement publication history exists')
            for reservation in row['artifactReservationIds']:
                path=self.portrait_alias_path(reservation)
                intent=self.safe(path.with_suffix('.intent').relative_to(self.root).as_posix())
                if path.exists() or intent.exists():raise Conflict('replacement artifact reservation history exists')
        history=state.setdefault('replacementRetirements',{})
        if state['generationId'] in history:raise Conflict('replacement retirement already recorded')
        history[state['generationId']]={'reason':'expired-sticky-unpublished-replacement','binding':dict(binding),'proof':evidence,'retiredLeases':dict(leases)}
        state['retiredRequestIds']=sorted(set(state.get('retiredRequestIds',[]))|set(leases))
        state['leases']={}

    @staticmethod
    def replacement_retirement_receipt(state):
        item=state.get('replacementRetirements',{}).get(state['generationId'])
        if item is None:return {}
        proof=item['proof']
        return {'schemaVersion':'npc-replacement-retirement.v1',
                **{k:proof[k] for k in ('npcId','generationId','requestId','proofHash','snapshotHash')},
                'parents':[{k:row[k] for k in ('parentRequestId','guardHash')} for row in proof['parents']]}

    def stage_delete(self,owner,generation_id,request_id,expected_source,*,replacement_evidence=None):
        if not isinstance(request_id,str) or not request_id:raise ValueError('deletion request')
        with self.lock():
            self.require_no_migration();state=self.state(owner);self.require_no_cleanup(state)
            binding={'requestId':request_id,'expectedSourceVersion':expected_source}
            if state['generationId']!=generation_id:raise Conflict('generation changed')
            if state['state'] not in ('active','delete-staged','deleting'):raise Conflict('active lease or fenced')
            history=state.get('replacementRetirements',{}).get(generation_id)
            parents=None
            if replacement_evidence is not None:
                parents=self.replacement_evidence(owner,generation_id,request_id,expected_source,replacement_evidence)
                if state['state']!='active' and (history is None or history['proof']!=replacement_evidence):raise Conflict('replacement retirement changed')
            if state['leases'] and (state['state']!='active' or parents is None):raise Conflict('active lease or fenced')
            if state['state']=='active' and not state['leases'] and parents is not None:raise Conflict('replacement leases absent')
            if state['state']!='active' and state.get('stagedDelete')!=binding:raise Conflict('staged deletion changed')
            snap=self.snapshot_unlocked()
            if snap['sourceVersion']!=expected_source:raise Conflict('stale staged deletion')
            # Persist admission fence before backup. Any failure keeps source and fence.
            if state['state']=='active':
                if parents is not None:self.retire_for_replacement(state,binding,replacement_evidence,parents)
                state.update(state='delete-staged',stagedDelete=binding);self.write(self.safe(self.state_path(owner)),encoded(state))
            bid,manifest,files,closure=self.stage_owner_backup(request_id,owner,expected_source)
            import re as _re
            owner_docs={k[len(owner)+1:]:planning_projection(k,json.loads(v),files.__getitem__) for k,v in files.items() if k.startswith(owner+'/') and k.endswith('.json') and not any(x.startswith('.') for x in k.split('/'))}
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
            return {'npcId':owner,'generationId':generation_id,'requestId':request_id,'graph':graph,'archive':archive,'fence':fence,'backupId':bid,'backupManifestHash':digest(manifest),'sourceVersion':expected_source,'ownerManifestHash':closure['ownerManifestHash'],**({'replacementRetirement':self.replacement_retirement_receipt(state)} if generation_id in state.get('replacementRetirements',{}) else {})}
    def prepare_delete(self,owner,generation_id,request_id,external_hash=None,*,deletion_scope=None):
        canonical=deletion_scope=='canonical-owner-generation.v1'
        if not request_id or (canonical and external_hash is not None) or (not canonical and (deletion_scope is not None or not re.fullmatch('[0-9a-f]{64}',str(external_hash)))):raise ValueError('deletion binding')
        with self.lock():
            self.require_no_migration()
            s=self.state(owner);self.require_no_cleanup(s)
            binding={'requestId':request_id,'deletionScope':deletion_scope} if canonical else {'requestId':request_id,'externalOwnerManifestHash':external_hash}
            if s['generationId']!=generation_id:raise Conflict('generation changed')
            if generation_id in s.get('replacementRetirements',{}) and not canonical:raise Conflict('replacement requires canonical deletion')
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
    def workflow_delete_character(self, binding, metadata_after, *, target=None):
        """Only the separately enabled, authenticated PDS workflow route calls this."""
        keys={'officialRequestId','taskId','projectId','npcId','generationId','expectedSourceVersion',
              'expectedRevision','payloadSha256'}
        if (type(binding) is not dict or set(binding)!=keys or binding['projectId']!='binari'
            or type(binding['expectedRevision']) is not int or binding['expectedRevision']<0
            or any(type(binding[k]) is not str or not re.fullmatch(r'[A-Za-z0-9._:-]{1,200}',binding[k])
                   for k in ('officialRequestId','taskId','npcId','generationId','expectedSourceVersion'))
            or not re.fullmatch(r'[a-f0-9]{64}',str(binding['payloadSha256']))
            or (metadata_after is not None and type(metadata_after) is not dict)):
            raise ValueError('workflow delete binding')
        rid=binding['taskId']; owner=binding['npcId']; generation=binding['generationId']
        bh=digest(binding)
        if target is not None and target!={'root':NPC_PREFIX.rstrip('/'),'path':owner,'kind':'directory'}:
            raise ValueError('workflow delete target binding')
        move=None if target is None else {'operationId':rid,'target':target}
        with self.lock():
            self.require_no_migration()
            prior=self.receipt(rid) if self.receipt_path(rid).exists() else None
            if prior is not None:
                if prior.get('workflowDeleteBindingHash')!=bh or prior.get('status')!='committed':
                    raise Conflict('workflow delete request reused')
                manifest,_=self.verified_restorable_backup(prior.get('backupId'))
                if manifest['characterId']!=owner or digest(manifest)!=prior.get('backupManifestHash'):
                    raise Conflict('workflow delete backup no longer verified')
                if (move is not None)!=bool(prior.get('storageRecoveryId')):
                    raise Conflict('workflow delete transport replay changed')
                if move is not None:self.verify_workflow_folder_recovery(prior,owner)
                return self.workflow_delete_absence(prior,owner,generation,folder_move=move is not None)
            if metadata_after is None:raise Conflict('workflow deletion receipt missing')
            state=self.state(owner);self.require_no_cleanup(state)
            if state['state']!='active' or state['generationId']!=generation:
                raise Conflict('workflow delete generation changed')
            source=self.snapshot_unlocked()
            if source['sourceVersion']!=binding['expectedSourceVersion']:
                raise Conflict('workflow delete source changed')
            old=source['documents']['metadata.json'].get('_pdsCurrent',{})
            marker=metadata_after.get('_pdsCurrent',{})
            if (old.get('revision')!=binding['expectedRevision'] or
                marker.get('revision')!=binding['expectedRevision']+1 or marker.get('operationHash')!=bh):
                raise Conflict('workflow delete revision changed')
            if rid in state.get('retiredRequestIds',[]) or generation in state.get('workflowDeleteRetirements',{}):
                raise Conflict('workflow delete stale binding')
            # Atomic Filex commit installs the tombstone and deletes the exact
            # owner, with backup verification from the existing Owned store.
            # Under this lock no old admission/commit can interleave. Retain the
            # old request IDs as history; discard superseded tokens.
            state=dict(state,state='deleting',delete={'requestId':rid,'deletionScope':'canonical-owner-generation.v1'})
            old_leases=state['leases']
            state['workflowDeleteRetirements']={**state.get('workflowDeleteRetirements',{}),generation:
                {'bindingHash':bh,'taskId':rid}}
            state['retiredRequestIds']=sorted(set(state.get('retiredRequestIds',[]))|set(old_leases))
            state['leases']={}
            self._local.deletion=state
            self._local.workflow_delete_binding=binding
            try:
                receipt=super().delete_character(rid,bh,owner,binding['expectedSourceVersion'],metadata_after,move=move)
                return self.workflow_delete_absence(receipt,owner,generation,folder_move=move is not None)
            finally:
                self._local.deletion=None
                self._local.workflow_delete_binding=None

    def verify_workflow_folder_recovery(self,receipt,owner):
        rid=receipt.get('storageRecoveryId')
        if not isinstance(rid,str) or not re.fullmatch(r'[a-f0-9]{64}',rid):
            raise Conflict('workflow folder recovery identity')
        manifest,_=self.verified_restorable_backup(receipt['backupId'])
        expected={k:v for k,v in manifest['files'].items() if k!='metadata.json'}
        saved=self.safe('.storage-quarantine/'+rid+'/target')
        found={k:hashlib.sha256(v).hexdigest() for k,v in self.scoped_owner_files(saved,owner,single_volume=True).items()}
        if manifest['characterId']!=owner or digest(manifest)!=receipt.get('backupManifestHash') or found!=expected:
            raise Conflict('workflow folder recovery changed')
    def workflow_delete_absence(self,receipt,owner,generation,*,folder_move=False):
        state=self.state(owner)
        current=self.snapshot_unlocked()['documents']['metadata.json']
        if (state['generationId']!=generation or state['state']!='deleted'
            or owner in current['characterOrder'] or self.owner_files(owner)):
            raise Conflict('workflow delete target absence unconfirmed')
        target=self.safe(owner)
        if target.exists() and not folder_move:
            # Journaled owner-file deletion is already committed. Remove only
            # empty directories beneath the verified exact owner, never files
            # or links. On interrupted cleanup replay verifies again.
            try:
                for _, dirs, files, fd in os.fwalk(target,topdown=False,follow_symlinks=False):
                    if files:raise Conflict('workflow delete unexpected content')
                    for name in dirs:os.rmdir(name,dir_fd=fd)
                target.rmdir()
            except OSError as exc:
                raise Conflict('workflow delete target absence unconfirmed') from exc
        if target.exists() or target.is_symlink():
            raise Conflict('workflow delete target absence unconfirmed')
        return {**receipt,'targetAbsent':True}
    def commit_owned(self,request_id,receipt,writes,removes=(),*,move=None):
        publication=getattr(self._local,'publication',None)
        if publication:receipt=dict(receipt,generationId=publication['generationId'],parentRequestId=publication['requestId'],stageRequestId=publication.get('stageRequestId',publication['requestId']))
        s=getattr(self._local,'deletion',None)
        if s:
            s=dict(s,state='deleted');writes=dict(writes);writes[self.state_path(s['npcId'])]=encoded(s)
            receipt=dict(receipt,generationId=s['generationId'])
            if s['delete'].get('deletionScope')=='canonical-owner-generation.v1':
                receipt.update(deletionScope='canonical-owner-generation.v1',externalDataDisposition='preserved-uninspected')
                retirement=self.replacement_retirement_receipt(s)
                if retirement:receipt['replacementRetirement']=retirement
            else:receipt['externalOwnerManifestHash']=s['delete']['externalOwnerManifestHash']
            bound=getattr(self._local,'workflow_delete_binding',None)
            if bound is not None:
                receipt.update(workflowDeleteBindingHash=digest(bound),workflowDeleteBinding=bound)

        lifecycle=getattr(self._local,'lifecycle',None)
        if lifecycle:
            state=dict(lifecycle,state='active');writes=dict(writes)
            writes[self.state_path(state['npcId'])]=encoded(state)
            receipt=dict(receipt,generationId=state['generationId'],parentRequestId=state['lifecycle']['requestId'],lifecycleOperation=state['lifecycle']['operation'])
        return super().commit_owned(request_id,receipt,writes,removes,move=move)
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
            if old and (old['state'] not in ('deleted','creation-abandoned') or old['leases']):raise Conflict('owner not deleted')
            if old and old['state']=='creation-abandoned' and operation!='create':raise Conflict('abandoned creation requires new create')
            if old and old['state']=='creation-abandoned':
                abandoned=old.get('abandonedCreations',{}).get(old['generationId'])
                if not abandoned or abandoned.get('guard')!=self.lifecycle_guard(old) or abandoned.get('receipt')!={'npcId':owner,'requestId':old['lifecycle']['requestId'],'generationId':old['generationId'],'state':'creation-abandoned','abandoned':True,'previousGenerationId':old.get('previousGenerationId')}:raise Conflict('abandoned creation proof missing')
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
            if old and old.get('replacementRetirements'):
                state['replacementRetirements']=old['replacementRetirements']
            if old and old.get('workflowDeleteRetirements'):
                state['workflowDeleteRetirements']=old['workflowDeleteRetirements']
            if old and old.get('abandonedCreations'):
                state['abandonedCreations']=old['abandonedCreations']
            if old and old['state']=='creation-abandoned':
                state['previousGenerationId']=old.get('previousGenerationId')
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
    def abandon_creation(self,guard):
        # This revokes only a never-committed creation reservation. No asset,
        # backup, historical receipt or canonical metadata is removed.
        keys={'npcId','generationId','requestId','operation','lifecycleToken','expectedSourceVersion'}
        if not isinstance(guard,dict) or set(guard)!=keys or guard.get('operation')!='create':raise ValueError('creation guard required')
        with self.lock():
            self.require_no_migration()
            state=self.state(guard['npcId'])
            historical=state.get('abandonedCreations',{}).get(guard['generationId'])
            if historical:
                if historical['guard']!=guard:raise Conflict('abandoned guard mismatch')
                return historical['receipt']
            self.require_no_cleanup(state)
            if state['state']!='creating' or state.get('leases') or state.get('finished'):raise Conflict('creation not unstarted')
            if self.lifecycle_guard(state)!=guard:raise Conflict('creation guard mismatch')
            snap=self.snapshot_unlocked()
            if guard['npcId'] in snap['documents']['metadata.json']['characterOrder'] or self.owner_files(guard['npcId']):raise Conflict('creation owner exists')
            if self.receipt_path(guard['requestId']).exists():raise Conflict('creation already committed')
            # Any committed lifecycle publication activates this generation in
            # the same recoverable transaction (commit_owned). lock() recovers
            # pending journals before testing state; unrelated receipts are not scanned.
            receipt={'npcId':guard['npcId'],'requestId':guard['requestId'],'generationId':guard['generationId'],'state':'creation-abandoned','abandoned':True,'previousGenerationId':state.get('previousGenerationId')}
            state['state']='creation-abandoned'
            state.setdefault('abandonedCreations',{})[guard['generationId']]={'guard':dict(guard),'receipt':receipt}
            state['retiredRequestIds']=sorted(set(state.get('retiredRequestIds',[]))|{guard['requestId']})
            self.write(self.safe(self.state_path(guard['npcId'])),encoded(state))
            return receipt
    def prepare_create(self,owner,request_id,expected_source):
        return self.prepare_lifecycle(owner,request_id,expected_source,'create')
    def prepare_restore(self,owner,request_id,backup_id,expected_source):
        return self.prepare_lifecycle(owner,request_id,expected_source,'restore',backup_id)
    def commit_workflow_native_bytes(self,writes):
        super().commit_owned(None,None,writes)
        return {'ok':True}

    def workflow_plan_representative(self,binding,content,metadata_after):
        expected={'officialRequestId','taskId','projectId','npcId','payloadSha256','artifactSha256',
                  'sourceVersion','revision','generationId','categoryId'}
        if (type(binding) is not dict or set(binding)!=expected or binding['projectId']!='binari'
            or type(binding['revision']) is not int or binding['revision']<0
            or any(type(binding[k]) is not str or not re.fullmatch(r'[a-f0-9]{64}',binding[k])
                   for k in ('artifactSha256','payloadSha256','sourceVersion'))
            or type(content) is not str or not content.strip() or not 0<len(content)<=16000
            or len(content.encode())>64000):
            raise ValueError('workflow representative binding')
        b=binding;owner=b['npcId'];task=b['taskId'];g=b['generationId'];cid=b['categoryId'];oid=b['officialRequestId']
        if (type(oid) is not str or not re.fullmatch(r'request\.official\.[a-f0-9]{64}',oid)
            or task!='workflow.task.'+digest([oid,'planning_representative'])
            or type(g) is not str or not re.fullmatch(r'[a-f0-9]{48}',g)):
            raise ValueError('workflow representative identity')
        preset='workflow.task.'+digest([oid,'preset'])
        if cid!='category.preset.'+digest(['npc.standard','3',preset,'portrait.representative'])[:32]:
            raise ValueError('workflow representative category')
        self.state_path(owner)
        artifact={'schemaVersion':'workflow.npc-representative-plan-artifact.v1','taskId':task,
                  'projectId':'binari','npcId':owner,'categoryId':cid,'content':content}
        if digest(artifact)!=b['artifactSha256']:raise Conflict('workflow representative artifact changed')
        with self.lock():
            self.require_no_migration()
            before=self.state(owner);self.require_no_cleanup(before)
            if before['state']!='active' or before['generationId']!=g:
                raise Conflict('workflow representative generation changed')
            binding_hash=digest(b)
            snap=self.snapshot_unlocked();docs=snap['documents'];root=docs['metadata.json']
            if (snap['sourceVersion']!=b['sourceVersion'] or root.get('_pdsCurrent',{}).get('revision')!=b['revision']
                or owner not in root['characterOrder'] or type(metadata_after) is not dict):
                raise Conflict('workflow representative source changed')
            cat=owner+'/planning/'+cid+'.json'
            old=docs.get(cat);meta=docs.get(owner+'/metadata.json',{})
            group=meta.get('planningCategoryOrder',[])
            roles=[i for i in group if docs.get(owner+'/planning/'+i+'.json',{}).get('planningRole')=='portrait.representative']
            if (roles!=[cid] or type(old) is not dict or old.get('id')!=cid or type(old.get('content')) is not str
                or meta.get('creationBasis',{}).get('requestId')!=preset):
                raise Conflict('workflow representative category changed')
            marker=metadata_after.get('_pdsCurrent',{})
            if ({k:v for k,v in metadata_after.items() if k!='_pdsCurrent'}!={k:v for k,v in root.items() if k!='_pdsCurrent'}
                or marker.get('revision')!=b['revision']+1 or marker.get('operationHash')!=binding_hash
                or not re.fullmatch(r'[a-f0-9]{64}',str(marker.get('documentHash')))):
                raise Conflict('workflow representative metadata changed')
            if self.safe(markdown_path(cat)).exists():raise Conflict('NPC_PLANNING_MARKDOWN_WORKFLOW_REQUIRED')
            updated={**old,'content':content};after={**docs,cat:updated,'metadata.json':metadata_after}
            self.check_document_hash(after)
            from ownership_plan import plan
            prefix=owner+'/'
            prev_plan=self.manifests().get(owner)
            derived_old=plan(owner,{k.removeprefix(prefix):v for k,v in docs.items() if k.startswith(prefix)})
            new_plan=plan(owner,{k.removeprefix(prefix):v for k,v in after.items() if k.startswith(prefix)})
            if (prev_plan!=derived_old or new_plan['slots']!=prev_plan['slots']
                or new_plan['nonSlotReferences']!=prev_plan['nonSlotReferences']):
                raise Conflict('workflow representative asset changes prohibited')
            # Keep only the recoverable byte transaction, not an operation receipt.
            super().commit_owned(None,None,{cat:encoded(updated),'metadata.json':encoded(metadata_after),
                                            owner+'/.owned-assets.json':encoded(new_plan)})
            return {'ok':True}

    def workflow_plan_idle(self,b,content):
        return self.workflow_plan_current('idle',b,content,shared=False)

    def workflow_plan_current(self,stage,b,content,*,shared=True,markdown=False):
        # Authenticated PDS-only RPC: current official target, not a model-start
        # version, digest, generation snapshot or caller-supplied metadata.
        keys={'officialRequestId','taskId','projectId','npcId','payloadSha256','categoryId'}
        if (type(b) is not dict or set(b)!=keys or b['projectId']!='binari'
            or type(b['officialRequestId']) is not str or not re.fullmatch(r'request\.official\.[a-f0-9]{64}',b['officialRequestId'])
            or type(b['payloadSha256']) is not str or not re.fullmatch(r'[a-f0-9]{64}',b['payloadSha256'])
            or type(b['npcId']) is not str or not re.fullmatch(r'(?:npc|character)\.[A-Za-z0-9][A-Za-z0-9._-]{0,120}',b['npcId'])
            or type(content) is not str or not 0<len(content)<=16000 or not content.strip()
            or len(content.encode())>64000):
            raise ValueError('workflow idle planning binding')
        targets={'overview':('planning_overview','overview','overview',()),
                 'portrait-art':('planning_portrait_art','portrait-art','portrait.art',(('overview','overview'),)),
                 'representative':('planning_representative','portrait.representative','portrait.representative',
                                   (('portrait-art','portrait.art'),)),
                 'idle':('planning_idle','portrait.idle','portrait.idle',
                         (('portrait.representative','portrait.representative'),('portrait-art','portrait.art')))}
        if stage not in targets or (not shared and (stage!='idle' or markdown)):raise ValueError('workflow plan stage')
        task_key,key,role,requirements=targets[stage]
        owner=b['npcId'];oid=b['officialRequestId'];cid=b['categoryId']
        preset='workflow.task.'+digest([oid,'preset'])
        def category(key):
            return 'category.preset.'+digest(['npc.standard','3',preset,key])[:32]
        if b['taskId']!='workflow.task.'+digest([oid,task_key]) or cid!=category(key):
            raise ValueError('workflow planning target')
        from datetime import datetime,timezone
        from npc_document import decode
        from ownership_plan import plan
        prefix=owner+'/';cat=prefix+'planning/'+cid+'.json'
        with self.lock():
            self.require_no_migration()
            state=self.state(owner);self.require_no_cleanup(state)
            if state['state']!='active':
                raise Conflict('workflow idle planning owner inactive')
            docs=self.documents_unlocked();root=docs['metadata.json']
            marker=root.get('_pdsCurrent',{});rev=marker.get('revision')
            meta=docs.get(prefix+'metadata.json',{})
            group=meta.get('planningCategoryOrder',[])
            rows={i:docs[prefix+'planning/'+i+'.json'] for i in group}
            row=rows.get(cid,{})
            if (owner not in root['characterOrder'] or meta.get('id')!=owner
                or meta.get('creationBasis',{}).get('requestId')!=preset or group.count(cid)!=1
                or row.get('id')!=cid or type(row.get('content')) is not str or type(rev) is not int or rev<0
                or not (row.get('planningRole')==role or
                        (stage in ('overview','portrait-art') and 'planningRole' not in row))
                or any(i!=cid for i,val in rows.items() if val.get('planningRole')==role)):
                raise Conflict('workflow planning current target required')
            if shared:
                for dep_key,dep_role in requirements:
                    dep_id=category(dep_key);dep=rows.get(dep_id,{})
                    if (dep.get('id')!=dep_id or group.count(dep_id)!=1 or dep.get('planningRole')!=dep_role
                        or type(dep.get('content')) is not str or not dep['content'].strip()
                        or any(i!=dep_id for i,val in rows.items() if val.get('planningRole')==dep_role)):
                        raise Conflict('workflow planning current prerequisite required')
            if self.safe(markdown_path(cat)).exists():
                if markdown and row.get('planningRole')==role and row.get('content')==content:return {'ok':True}
                raise Conflict('NPC_PLANNING_MARKDOWN_WORKFLOW_REQUIRED')
            updated={**row,'content':content,'planningRole':role}
            category_writes=planning_write_bytes(cat,updated) if markdown else {cat:encoded(updated)}
            after={**docs,cat:updated}
            prev_plan=self.manifests().get(owner)
            if prev_plan!=plan(owner,{k.removeprefix(prefix):v for k,v in docs.items() if k.startswith(prefix)}):
                raise Conflict('workflow idle planning owner manifest changed')
            new_plan=plan(owner,{k.removeprefix(prefix):v for k,v in after.items() if k.startswith(prefix)})
            if (new_plan['slots']!=prev_plan['slots'] or
                new_plan['nonSlotReferences']!=prev_plan['nonSlotReferences']):
                raise Conflict('workflow idle planning asset changes prohibited')
            document=decode(after);document.pop('_pdsCurrent',None)
            after_root={**root,'_pdsCurrent':{'revision':rev+1,'savedAt':datetime.now(timezone.utc).isoformat(),
                'note':'Workflow '+stage+' planning','operationHash':digest([b,content]),'documentHash':digest(document)}}
            self.check_document_hash({**after,'metadata.json':after_root})
            super().commit_owned(None,None,{**category_writes,'metadata.json':encoded(after_root),
                                            prefix+'.owned-assets.json':encoded(new_plan)})
            return {'ok':True}

    def workflow_portrait_representative(self,b,raw,*,mode='representative'):
        # PDS-only authenticated RPC. Derive the exact write set locally.
        keys={'officialRequestId','taskId','projectId','npcId','payloadSha256',
              'categoryId','sha256'}
        if mode=='direction':keys.add('direction')
        pixel_ref=mode=='direction' and isinstance(b,dict) and b.get('referenceStage')=='portrait_pixel_idle_reference'
        if pixel_ref:keys.update({'referenceStage','referenceAssetId'})
        if mode not in ('representative','idle','direction'):raise ValueError('workflow portrait mode')
        if (type(b) is not dict or set(b)!=keys or b['projectId']!='binari'
            or any(type(b[k]) is not str or not re.fullmatch(r'[a-f0-9]{64}',b[k])
                   for k in ('sha256','payloadSha256'))
            or type(b['officialRequestId']) is not str or not re.fullmatch(r'request\.official\.[a-f0-9]{64}',b['officialRequestId'])
            or type(b['npcId']) is not str or not re.fullmatch(r'(?:npc|character)\.[A-Za-z0-9][A-Za-z0-9._-]{0,120}',b['npcId'])
            or type(raw) is not bytes or not 0<len(raw)<=8*1024*1024
            or hashlib.sha256(raw).hexdigest()!=b['sha256']):
            raise ValueError('workflow portrait binding')
        owner=b['npcId'];oid=b['officialRequestId'];task=b['taskId'];cid=b['categoryId']
        preset='workflow.task.'+digest([oid,'preset'])
        direction=b.get('direction')
        if mode=='direction' and direction not in ('n','ne','e','se','s','sw','w','nw'):
            raise ValueError('workflow portrait direction')
        key='portrait_'+direction if direction else ('portrait_idle_reference' if mode=='idle' else 'portrait_representative')
        role='portrait.representative' if mode=='representative' else 'portrait.idle'
        if (task!='workflow.task.'+digest([oid,key]) or
            cid!='category.preset.'+digest(['npc.standard','3',preset,role])[:32]):
            raise ValueError('workflow portrait target')
        from media_validation import png
        png(raw)
        from datetime import datetime,timezone
        from npc_document import decode
        from ownership_plan import plan
        prefix=owner+'/';meta_path=prefix+'metadata.json'
        view_key='idle/portrait/'+direction if direction else None
        view_path=prefix+'pose-views/'+view_key+'.json' if direction else None
        slot='assets/pose-views/'+view_key+'/image.png' if direction else 'assets/portraits/'+mode+'/portrait.png'
        with self.lock():
            self.require_no_migration()
            state=self.state(owner);self.require_no_cleanup(state)
            if state['npcId']!=owner or state['state']!='active':
                raise Conflict('workflow portrait target not active')
            docs=self.documents_unlocked();root=docs['metadata.json']
            revision=root.get('_pdsCurrent',{}).get('revision')
            if type(revision) is not int or revision<0 or owner not in root['characterOrder']:
                raise Conflict('workflow portrait current document invalid')
            meta=docs[meta_path];group=meta.get('planningCategoryOrder',[])
            rows={i:docs[prefix+'planning/'+i+'.json'] for i in group}
            reps=[i for i,row in rows.items() if row.get('planningRole')==role]
            if (meta.get('id')!=owner or meta.get('creationBasis',{}).get('requestId')!=preset
                or reps!=[cid] or rows[cid].get('id')!=cid
                or type(rows[cid].get('content')) is not str or not rows[cid]['content'].strip()):
                raise Conflict('workflow portrait planning changed')
            pairs=meta.get('portraitSets',{})
            def checked_asset(path,asset):
                source=self.safe(prefix+path)
                if (type(asset) is not str or not re.fullmatch(r'[a-f0-9]{64}\.png',asset)
                    or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest()!=asset[:-4]):
                    raise Conflict('workflow portrait slot integrity')

            if mode!='representative':
                ref_pos,ref_key=('idle','portrait_idle_reference') if direction else ('representative','portrait_representative')
                ref=pairs.get(ref_pos,{})
                if ref.get('requestId')!='workflow.task.'+digest([oid,ref_key]):
                    raise Conflict('workflow portrait reference not published')
                checked_asset('assets/portraits/'+ref_pos+'/portrait.png',ref.get('portraitAssetId'))
                if pixel_ref:
                    expected_center='workflow.task.'+digest([oid,'portrait_pixel_idle_reference'])
                    if (ref.get('pixelProvenance',{}).get('postprocess',{}).get('workflowTaskId')!=expected_center
                        or ref.get('pixelSourceAssetId')!=ref.get('portraitAssetId')
                        or ref.get('pixelAssetId')!=b['referenceAssetId']):
                        raise Conflict('workflow pixel center reference changed')
                    checked_asset('assets/portraits/idle/pixel.png',b['referenceAssetId'])

            prev_plan=self.manifests().get(owner)
            if prev_plan!=plan(owner,{k.removeprefix(prefix):v for k,v in docs.items() if k.startswith(prefix)}):
                raise Conflict('workflow portrait owner manifest changed')
            aid=b['sha256']+'.png';extra={}
            if direction:
                old=docs.get(view_path)
                keys=meta.get('poseViewKeys',[])
                if (view_key in keys)!=(old is not None):
                    raise Conflict('workflow portrait view index changed')
                if old is not None:
                    if old.get('requestId')!=task or old.get('sourceArtifactId') is not None:
                        raise Conflict('workflow portrait legacy directional view protected')
                    checked_asset(slot,old.get('assetId'))
                elif self.safe(prefix+slot).exists():
                    raise Conflict('workflow portrait unbound target file')
                width,height=int.from_bytes(raw[16:20],'big'),int.from_bytes(raw[20:24],'big')
                element={'schemaVersion':'ngs.npc-pose-view.v1','posePreset':'idle','referenceType':'portrait',
                         'direction':direction,'assetId':aid,'requestId':task,'mimeType':'image/png',
                         'bytes':len(raw),'width':width,'height':height,
                         **({'referenceArtifactId':b['referenceAssetId']} if pixel_ref else {})}
                updated={**meta,'poseViewKeys':sorted(set(keys)|{view_key})}
                extra[view_path]=element
            else:
                old=pairs.get(mode)
                if mode in pairs:
                    if type(old) is not dict or set(old)!={'portraitAssetId','requestId'}:
                        raise Conflict('workflow portrait legacy pair protected')
                    checked_asset(slot,old['portraitAssetId'])
                elif self.safe(prefix+slot).exists():
                    raise Conflict('workflow portrait unbound target file')
                inventory=meta['portraits']
                index={row['assetId']:row for row in inventory}
                if len(index)!=len(inventory):
                    raise Conflict('workflow portrait inventory invalid')
                retired=old['portraitAssetId'] if old else None
                if retired and (retired not in index or
                                (mode=='representative' and meta.get('activePortraitId')!=retired)):
                    raise Conflict('workflow portrait existing slot invalid')
                protected={pair[k] for pos,pair in pairs.items() if pos!=mode
                           for k in ('portraitAssetId','pixelAssetId') if k in pair}
                if mode=='idle':protected.add(meta.get('activePortraitId'))
                inventory=[row for row in inventory if row['assetId']!=retired or retired in protected or retired==aid]
                if aid not in index:
                    inventory.append({'assetId':aid,'label':'대표 포트레이트' if mode=='representative' else '아이들 포트레이트',
                                      'sourceRef':task})
                updated={**meta,'portraitSets':{**pairs,mode:{'portraitAssetId':aid,'requestId':task}},'portraits':inventory}
                if mode=='representative':updated['activePortraitId']=aid
            after={**docs,meta_path:updated,**extra}
            new_plan=plan(owner,{k.removeprefix(prefix):v for k,v in after.items() if k.startswith(prefix)})
            if ({k:v for k,v in prev_plan['slots'].items() if k!=slot}!={k:v for k,v in new_plan['slots'].items() if k!=slot}
                or new_plan['slots'].get(slot)!={'assetId':aid,'sha256':b['sha256']}
                or new_plan['nonSlotReferences']!=prev_plan['nonSlotReferences']):
                raise Conflict('workflow portrait unrelated asset changes prohibited')
            document=decode(after);document.pop('_pdsCurrent',None)
            after_root={**root,'_pdsCurrent':{'revision':revision+1,'savedAt':datetime.now(timezone.utc).isoformat(),
                'note':'Workflow '+mode+' portrait','operationHash':digest(b),'documentHash':digest(document)}}
            self.check_document_hash({**after,'metadata.json':after_root})
            super().commit_owned(None,None,{prefix+slot:raw,meta_path:encoded(updated),
                'metadata.json':encoded(after_root),prefix+'.owned-assets.json':encoded(new_plan),
                **{path:encoded(value) for path,value in extra.items()}})
            return {'ok':True}

    def workflow_preset(self,binding,files):
        # Authenticated PDS-only. No creating reservation and no worker lease.
        keys={'officialRequestId','taskId','projectId','npcId','payloadSha256','artifactSha256',
              'expectedRevision','expectedSourceVersion','predecessorReceiptRef'}
        if (type(binding) is not dict or set(binding)!=keys or binding['projectId']!='binari'
            or type(binding['expectedRevision']) is not int or binding['expectedRevision']<0
            or any(type(binding[k]) is not str or not re.fullmatch(r'[A-Za-z0-9._:-]{1,200}',binding[k])
                   for k in ('officialRequestId','taskId'))
            or any(type(binding[k]) is not str or not re.fullmatch(r'[a-f0-9]{64}',binding[k])
                   for k in ('payloadSha256','artifactSha256','expectedSourceVersion'))):
            raise ValueError('workflow preset binding')
        owner=binding['npcId'];rid=binding['taskId'];before_id=binding['predecessorReceiptRef']
        if type(owner) is not str or not re.fullmatch(r'(?:character|npc|entity|enemy)\.[a-z0-9._-]{2,80}',owner):
            raise ValueError('workflow preset owner')
        if before_id is not None and (type(before_id) is not str or not re.fullmatch(r'[A-Za-z0-9._:-]{1,200}',before_id) or before_id==rid):
            raise ValueError('workflow preset predecessor')
        with self.lock():
            self.require_no_migration()
            gp=self.safe(self.state_path(owner))
            old=json.loads(gp.read_bytes()) if gp.exists() else None
            rp=self.receipt_path(rid);bh=digest(binding)
            if rp.exists():
                prior=json.loads(rp.read_bytes())
                if (prior.get('workflowPresetBindingHash')!=bh or prior.get('workflowPresetBinding')!=binding
                    or prior.get('status')!='committed' or not old or old.get('state')!='active'
                    or old.get('generationId')!=prior.get('generationId')):
                    raise Conflict('workflow preset replay binding')
                return prior
            snap=self.snapshot_unlocked();root=snap['documents']['metadata.json'];rev=binding['expectedRevision']
            if (snap['sourceVersion']!=binding['expectedSourceVersion'] or root.get('_pdsCurrent',{}).get('revision')!=rev
                or owner in root['characterOrder'] or self.safe(owner).exists() or self.owner_files(owner)):
                raise Conflict('workflow preset target not absent or stale')
            if old:
                if old.get('state')!='deleted':raise Conflict('workflow preset previous generation not retired')
                self.require_no_cleanup(old)
            # Optional receipt is a Filex incarnation binding only. Dependency
            # readiness belongs to the workflow claim, never to this writer.
            if before_id is not None:
                if not old:raise Conflict('workflow preset predecessor state')
                delete=self.receipt_path(before_id)
                if not delete.is_file():raise Conflict('workflow preset predecessor missing')
                prior=json.loads(delete.read_bytes()); b=prior.get('workflowDeleteBinding',{})
                if (prior.get('requestId')!=before_id or prior.get('status')!='committed'
                    or prior.get('backupVerified') is not True or prior.get('deletedCharacter')!=owner
                    or prior.get('generationId')!=old['generationId'] or digest(b)!=prior.get('workflowDeleteBindingHash')
                    or b.get('taskId')!=before_id or b.get('generationId')!=old['generationId']
                    or b.get('officialRequestId')!=binding['officialRequestId'] or b.get('npcId')!=owner
                    or b.get('projectId')!=binding['projectId'] or b.get('payloadSha256')!=binding['payloadSha256']):
                    raise Conflict('workflow preset predecessor binding')
                if prior.get('storageRecoveryId') is not None:self.verify_workflow_folder_recovery(prior,owner)
            metadata=owner+'/metadata.json'
            if (type(files) is not dict or not {'metadata.json',metadata}<=files.keys()
                or any(type(body) is not dict or key not in {'metadata.json',metadata} and not
                       re.fullmatch(re.escape(owner)+r'/planning/category\.preset\.[a-f0-9]{32}\.json',key)
                       for key,body in files.items())):
                raise ValueError('workflow preset file scope')
            doc=files[metadata]; newroot=files['metadata.json']; marker=newroot.get('_pdsCurrent',{})
            if (doc.get('id')!=owner or doc.get('portraits')!=[] or doc.get('activePortraitId') is not None
                or any(doc.get(k) for k in ('poseViewKeys','portraitSets','animationSlotKeys','animationExperiments',
                                           'animationExperimentKeys','animationFrameSetKeys'))):
                raise ValueError('workflow preset assets prohibited')
            if ({k:v for k,v in newroot.items() if k!='_pdsCurrent'}!={**{k:v for k,v in root.items() if k!='_pdsCurrent'},
                'characterOrder':[*root['characterOrder'],owner],'generationEpoch':rid}
                or marker.get('revision')!=rev+1 or marker.get('operationHash')!=bh
                or not re.fullmatch(r'[a-f0-9]{64}',str(marker.get('documentHash')))):
                raise Conflict('workflow preset metadata binding')
            self.check_document_hash({**snap['documents'],**files})
            from ownership_plan import plan
            owner_plan=plan(owner,{k.removeprefix(owner+'/'):v for k,v in files.items() if k.startswith(owner+'/')})
            if owner_plan['slots'] or owner_plan['nonSlotReferences']:raise ValueError('workflow preset assets prohibited')
            retired=set(old.get('retiredRequestIds',[])) if old else set()
            if old:
                retired.update(old.get('finished',{}))
                retired.update(x['requestId'] for x in (old.get('lifecycle'),old.get('delete')) if x)
            if rid in retired:raise Conflict('workflow preset retired request')
            generation=secrets.token_hex(24)
            state={'npcId':owner,'generationId':generation,'state':'active','leases':{},'finished':{},
                   'retiredRequestIds':sorted(retired),'previousGenerationId':old['generationId'] if old else None}
            for k in ('replacementRetirements','workflowDeleteRetirements','abandonedCreations'):
                if old and old.get(k):state[k]=old[k]
            writes={k:encoded(v) for k,v in files.items()}
            writes[owner+'/.owned-assets.json']=encoded(owner_plan)
            writes[self.state_path(owner)]=encoded(state)
            receipt={'requestId':rid,'inputHash':bh,'workflowPresetBindingHash':bh,'workflowPresetBinding':binding,
                     'generationId':generation,'status':'committed','resultMetadata':marker}
            return self.commit_owned(rid,receipt,writes)

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
                    self.path(NPC_PREFIX+path);documents[path]=planning_projection(path,json.loads(raw),data.__getitem__)
            if owner+'/metadata.json' not in documents:raise Conflict('incomplete backup owner')
            return {'backupId':backup_id,'characterId':owner,'backupManifestHash':digest(manifest),'backupVerified':True,'documents':documents}
