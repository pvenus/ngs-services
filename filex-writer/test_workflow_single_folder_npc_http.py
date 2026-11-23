"""Actual isolated Filex writer + PDS HTTP and exact claim/preset closure."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import urlopen

import test_workflow_delete_claim_http as fixture
import test_workflow_preset_http as preset_fixture
from server import handler,Conflict
from single_target_delete import SingleTargetDelete as Move
from project_data_service.workflow_delete import NPC_ROOT, WorkflowDelete
from project_data_service.motion_presets import DIRECTION_ORDER, MotionPresets
from project_data_service.workflow_preset import PATH as PRESET, WorkflowPreset, REGISTRY_SHA, CATEGORIES
from project_data_service.single_target_delete import PATH, SCHEMA, SingleTargetDelete
from project_data_service.project_folders import ProjectFolders,FolderError
from project_data_service.recursive_folders import compare,fetch_chunk
from project_data_service.pds_owned_transport import OwnedNPCTransport
from project_data_service.filex_npc_store import FilexNPCStore
from project_data_service.filex_npc_authority import FilexCurrentNPC
from npc_store import encoded,digest


class Tests(unittest.TestCase):
    owner='npc.test'
    picks=None

    def setUp(self):
        prepared=fixture.Tests()
        self.addCleanup(prepared.doCleanups)
        prepared.setUp(self.owner,self.picks)
        for key in ('writer','original_token','pds','base','verifier','db','c','g','b'):
            setattr(self,key,getattr(prepared,key))
        with self.writer.lock():
            p=self.writer.root/'metadata.json';m=json.loads(p.read_bytes())
            m.update(schemaVersion=1,actId='act01',masterRevision=1,storyRevision=1)
            self.writer.write(p,encoded(m))
        writer,url=self.start(handler(self.writer,self.original_token,workflow_delete=True,
                                      workflow_npc_folder_delete=True,workflow_preset=True))
        config={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','scope':'npc','enabled':True,
                'endpoint':url,'tokenFile':str(self.writer.root.parent/'writer-token')}
        self.source=FilexCurrentNPC(FilexNPCStore(lambda:OwnedNPCTransport(config)))
        self.pds.workflow_delete=WorkflowDelete(self.source,self.verifier)
        self.pds.workflow_preset=WorkflowPreset(self.source,self.verifier)
        self.pds.single_target_delete=SingleTargetDelete({NPC_ROOT:self.pds.workflow_delete.storage_provider()})
        self.target={'root':NPC_ROOT,'path':self.owner,'kind':'directory'}

    start=fixture.Tests.start
    post=fixture.Tests.post

    def remove(self,target=None,claim=None):
        return self.post({'claim':self.c if claim is None else claim,
                          'target':self.target if target is None else target},path=PATH)

    def preset(self):
        official=self.c['officialRequestId'];rid='workflow.task.'+digest([official,'preset'])
        with sqlite3.connect(self.db) as db:
            payload=json.loads(db.execute('SELECT payload FROM workflow_runs').fetchone()[0])
            db.execute("UPDATE workflow_tasks SET status='completed',result_ref=? WHERE task_id=?",
                       (self.c['taskId'],self.c['taskId']))
            inp={'npcId':self.owner,'mode':'fresh-replacement','noOldDataReuse':True}
            db.execute("INSERT INTO workflow_tasks VALUES(?,?,'preset','npc.preset','document',?,NULL,3,?,'running')",
                       (rid,official,encoded(inp).decode(),self.c['taskId']))
        preset_fixture.Tests.approve(self,self.owner)
        art={**self.art,'character':payload['character'],'taskId':rid,'presetRegistrySha256':REGISTRY_SHA}
        categories=[{'id':'category.preset.'+digest(['npc.standard','3',rid,cid])[:32],
                     'name':name,'content':'',**({'planningRole':role} if role else {})} for cid,name,role in CATEGORIES]
        art['profile']['planningCategories']=categories
        art['profile']['planningPreset']['visibleCategoryIds']=[x['id'] for x in categories]
        return self.post({'claim':{**self.c,'taskId':rid,'claimStamp':3},'artifact':art,
                          'predecessorReceiptRef':self.c['taskId']},path=PRESET)

    def test_real_generic_delete_exact_rename_neighbor_and_preset(self):
        selected=self.writer.root/'npc.test'
        before_inode=selected.stat().st_ino
        sibling=self.writer.owner_files('npc.other')
        sibling_inode=(self.writer.root/'npc.other').stat().st_ino
        sibling_state=self.writer.state('npc.other')
        with urlopen(self.base+PATH,timeout=3) as r:
            self.assertEqual(json.load(r),{'enabled':True,'schemaVersion':SCHEMA,'roots':[NPC_ROOT]})
        original=self.writer.owner_files('npc.test')
        before=self.db.read_bytes()
        code,ack=self.remove();self.assertEqual(code,200);self.assertEqual(self.remove(),(200,ack))
        saved=self.writer.root/'.storage-quarantine'/ack['recoveryId']/'target'
        self.assertEqual(saved.stat().st_ino,before_inode)
        self.assertTrue((saved/'nested'/'empty').is_dir())
        self.assertEqual(self.writer.scoped_owner_files(saved,'npc.test'),original)
        self.assertFalse(selected.exists())
        old=self.writer.state('npc.test')
        self.assertEqual(old['state'],'deleted');self.assertIn('old.lane',old['retiredRequestIds'])
        self.assertEqual(self.writer.snapshot()['documents']['metadata.json']['characterOrder'],['npc.other'])
        self.assertEqual(self.db.read_bytes(),before)
        receipt=self.writer.receipt(self.c['taskId'])
        self.assertEqual((receipt['generationId'],receipt['storageRecoveryId']),(self.g,ack['recoveryId']))
        self.assertTrue(receipt['backupVerified'])
        self.assertEqual(self.preset()[0],200)
        new=self.writer.state('npc.test')
        self.assertEqual((new['state'],new['previousGenerationId']),('active',self.g))
        self.assertNotEqual(new['generationId'],self.g)
        self.assertEqual(self.writer.scoped_owner_files(saved,'npc.test'),original)
        self.assertEqual(self.writer.owner_files('npc.other'),sibling)
        self.assertEqual((self.writer.root/'npc.other').stat().st_ino,sibling_inode)
        self.assertEqual(self.writer.state('npc.other'),sibling_state)
        self.assertEqual(self.remove()[0],403)
        with self.assertRaises(Conflict):self.writer.admit('npc.test',self.g,'late')

    def test_target_scope_and_stale_claim_never_prepare_mutation(self):
        for target in ({**self.target,'path':'npc.other'},{**self.target,'kind':'file'},
                       {**self.target,'path':'npc.test/planning'},{**self.target,'path':'../npc.other'}):
            self.assertEqual(self.remove(target)[0],403)
        self.assertEqual(self.remove(claim={**self.c,'claimStamp':1})[0],403)
        self.assertFalse((self.writer.root/'.storage-quarantine').exists())
        self.assertFalse((self.writer.root/'.owned-tx.json').exists())

    def test_reply_loss_after_move_recovers_catalog_and_generation_through_normal_read(self):
        source=self.writer.root/'npc.test';inode=source.stat().st_ino
        original=Move.remove_locked
        calls=[]
        def interrupted(executor,command):
            ack=original(executor,command);calls.append(ack)
            if len(calls)==1:raise OSError('fixture interrupt after durable move')
            return ack
        with patch.object(Move,'remove_locked',interrupted):
            self.assertIn(self.remove()[0],(409,503))
            self.assertFalse(source.exists());self.assertTrue((self.writer.root/'.owned-tx.json').exists())
            self.assertFalse(self.writer.receipt_path(self.c['taskId']).exists())
            # Normal Filex read entry acquires the same lock and rolls forward.
            doc=self.writer.snapshot()['documents']
            self.assertNotIn('npc.test',doc['metadata.json']['characterOrder'])
            self.assertEqual(self.writer.generation('npc.test')['state'],'deleted')
        self.assertEqual(calls[0],calls[1])
        self.assertEqual(self.remove(),(200,calls[0]))
        saved=self.writer.root/'.storage-quarantine'/calls[0]['recoveryId']/'target'
        self.assertEqual(saved.stat().st_ino,inode)

    def test_raw_reader_refuses_pending_journal_before_tombstones_or_chunk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()/'canonical-files';domain=root/'binari/npcs'
            domain.mkdir(parents=True)
            data=b'{"characterOrder":[]}'
            (domain/'metadata.json').write_bytes(data)
            folders=ProjectFolders(root)
            folders.read_checkpoints=((('binari','npcs'),'.owned-tx.json'),)
            request={'scope':'binari/npcs/npc.test','files':[{'path':'metadata.json','sha256':'b'*64}],
                     'includeDeleted':True}
            journal=domain/'.owned-tx.json';journal.write_bytes(b'{}')
            with self.assertRaisesRegex(FolderError,'FOLDER_DOMAIN_COMMIT_IN_PROGRESS'):compare(folders,request)
            with self.assertRaisesRegex(FolderError,'FOLDER_DOMAIN_COMMIT_IN_PROGRESS'):
                fetch_chunk(folders,'binari/npcs','metadata.json',hashlib.sha256(data).hexdigest(),0)
            self.assertEqual(compare(folders,{'scope':'binari/concept','files':[],'includeDeleted':True})['deleted'],[])
            journal.unlink()
            self.assertEqual(compare(folders,request)['deleted'],request['files'])


class LegacyReplacementTests(unittest.TestCase):
    owner='character.seojin'
    start=Tests.start
    post=Tests.post
    remove=Tests.remove

    def setUp(self):
        self.motion=MotionPresets()
        self.presets={}
        self.picks=[]
        for i in range(20):
            pid=f'legacy-motion-{i}'
            pose='idle' if i%2 else 'combat'
            hashes={direction:hashlib.sha256(f'{pid}/{direction}'.encode()).hexdigest() for direction in DIRECTION_ORDER}
            self.presets['presets/'+pid+'.json']={'presetId':pid,'posePreset':pose,
                'directions':[{'direction':d,'sha256':hashes[d]} for d in DIRECTION_ORDER]}
            self.picks.append({'presetId':pid,'posePreset':pose,
                'assetSetSha256':digest(['ngs.motion-assets.v1',pid,pose,[[d,hashes[d]] for d in DIRECTION_ORDER]])})
        self.motion.snapshot=lambda:{'documents':self.presets}
        Tests.setUp(self)
        self.pds.workflow_delete.motion=self.motion
        self.prior=self.writer.owner_files(self.owner)
        self.sibling=self.writer.owner_files('npc.other')

    def test_existing_character_replacement_preflight_and_exact_delete_then_fresh_preset(self):
        first=list(self.presets)[-1]
        held=self.presets.pop(first)
        self.assertEqual(self.remove()[0],409)  # the first 19 picks were valid
        self.assertEqual(self.writer.owner_files(self.owner),self.prior)
        self.assertFalse(self.writer.receipt_path(self.c['taskId']).exists())
        self.presets[first]=held
        self.assertEqual(self.remove(target={**self.target,'path':'npc.seojin'})[0],403)
        self.assertEqual(self.remove(target={**self.target,'path':'npc.other'})[0],403)

        oid=self.c['officialRequestId']; delete=self.c['taskId']
        rid='workflow.task.'+digest([oid,'preset'])
        with sqlite3.connect(self.db) as db:
            payload=json.loads(db.execute('SELECT payload FROM workflow_runs').fetchone()[0])
            inp={'npcId':self.owner,'mode':'fresh-replacement','noOldDataReuse':True}
            db.execute("INSERT INTO workflow_tasks VALUES(?,?,'preset','npc.preset','document',?,NULL,3,?,'running')",
                       (rid,oid,encoded(inp).decode(),delete))
        preset_fixture.Tests.approve(self,self.owner)
        art={**self.art,'character':payload['character'],'taskId':rid,'presetRegistrySha256':REGISTRY_SHA}
        categories=[{'id':'category.preset.'+digest(['npc.standard','3',rid,cid])[:32],
                     'name':name,'content':'',**({'planningRole':role} if role else {})} for cid,name,role in CATEGORIES]
        art['profile']['planningCategories']=categories
        art['profile']['planningPreset']['visibleCategoryIds']=[x['id'] for x in categories]
        def preset(ref=None):
            return self.post({'claim':{**self.c,'taskId':rid,'claimStamp':3},'artifact':art,
                              'predecessorReceiptRef':ref},path=PRESET)
        def complete(ref):
            with sqlite3.connect(self.db) as db:
                db.execute("UPDATE workflow_tasks SET status='completed',result_ref=? WHERE task_id=?",(ref,delete))
        self.assertEqual(preset()[0],403)
        code,ack=self.remove();self.assertEqual(code,200)
        self.assertFalse((self.writer.root/self.owner).exists())
        self.assertEqual(self.writer.state(self.owner)['generationId'],self.g)
        self.assertEqual(preset()[0],403)
        complete('workflow.task.'+'a'*64)
        self.assertEqual(preset()[0],403)
        complete(delete)
        self.assertEqual(preset('workflow.task.'+'b'*64)[0],409)
        code,receipt=preset();self.assertEqual(code,200) # null is resolved by PDS, not trusted
        self.assertEqual(preset(delete),(200,receipt))
        state=self.writer.state(self.owner)
        self.assertEqual(state['previousGenerationId'],self.g)
        self.assertNotEqual(state['generationId'],self.g)
        self.assertEqual(self.writer.receipt(rid)['workflowPresetBinding']['predecessorReceiptRef'],delete)
        meta=self.writer.snapshot()['documents'][self.owner+'/metadata.json']
        self.assertEqual((meta['id'],meta['slug']),(self.owner,'seojin'))
        self.assertFalse((self.writer.root/'npc.seojin').exists())
        self.assertEqual(self.writer.owner_files('npc.other'),self.sibling)
        recovery=self.writer.root/'.storage-quarantine'/ack['recoveryId']/'target'
        self.assertEqual(self.writer.scoped_owner_files(recovery,self.owner),self.prior)
        self.assertEqual(self.remove()[0],403)
        with self.assertRaises(Conflict): self.writer.admit(self.owner,self.g,'old.after.replace')

    def test_invalid_legacy_slug_refused_before_delete(self):
        with sqlite3.connect(self.db) as db:
            payload=json.loads(db.execute('SELECT payload FROM workflow_runs').fetchone()[0])
            payload['character']['slug']='yeonwoo'
            db.execute('UPDATE workflow_runs SET payload=?,payload_sha256=?',(encoded(payload).decode(),digest(payload)))
        self.assertEqual(self.remove(claim={**self.c,'payloadSha256':digest(payload)})[0],403)
        self.assertEqual(self.writer.owner_files(self.owner),self.prior)
        self.assertEqual(self.writer.owner_files('npc.other'),self.sibling)


if __name__=='__main__':
    import sys
    from project_data_service.workflow_delete_claim import PrivatePlannerClaim
    db=Path(sys.argv[1]);sterile=Tests()
    sterile.setUp()
    try:
        st=db.stat();verifier=PrivatePlannerClaim(str(db),st.st_dev,st.st_ino)
        sterile.pds.workflow_delete=WorkflowDelete(sterile.source,verifier)
        sterile.pds.workflow_preset=WorkflowPreset(sterile.source,verifier)
        sterile.pds.single_target_delete=SingleTargetDelete({NPC_ROOT:sterile.pds.workflow_delete.storage_provider()})
        print(json.dumps({'port':sterile.pds.server_port,'root':str(sterile.writer.root),'oldGeneration':sterile.g}),flush=True)
        for line in sys.stdin:
            if line.strip()=='close':break
    finally:sterile.doCleanups()
