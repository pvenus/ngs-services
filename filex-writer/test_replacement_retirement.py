import hashlib
import threading
import unittest
from unittest.mock import patch
from test_fenced_store import Tests as Fixture
from npc_store import digest, encoded, NPC_PREFIX
from server import Conflict
from fenced_store import FencedOwnedNPCStore


def evidence(npc,gen,rid,source,guards):
    p={'schemaVersion':'npc-replacement-expired-leases.v1','npcId':npc,'generationId':gen,'requestId':rid,
       'expectedSourceVersion':source,'snapshotHash':'a'*64,'parents':[
         {'parentRequestId':g['requestId'],'guardHash':digest(g),'leaseTokenHash':hashlib.sha256(g['leaseToken'].encode()).hexdigest(),'stageRequestIds':[g['requestId']+'.child'],
          'publicationRequestIds':[g['requestId']+'.child'],'timeoutProofHashes':['b'*64],'childScopeHashes':['c'*64]} for g in guards]}
    for row in p['parents']:
        parent=row['parentRequestId'];stage=row['stageRequestIds'][0]
        row['canonicalCommitRequestIds']=[parent,stage,'npc.portrait.'+digest([stage,npc,'idle',parent,stage])]
        row['artifactReservationIds']=['asset.'+hashlib.sha256(('binari:'+stage+suffix).encode()).hexdigest()[:48] for suffix in ('','.pixel','.pixel-source')]
    return seal(p)


def seal(p):
    p['proofHash']=digest({k:v for k,v in p.items() if k!='proofHash'})
    return p


class Tests(unittest.TestCase):
    def setUp(self):
        Fixture.setUp(self)
        from ownership_plan import plan
        self.s.write(self.s.safe('npc/.owned-assets.json'),encoded(plan('npc',{'metadata.json':{'id':'npc'}})))
    tearDown=Fixture.tearDown

    def stage(self, guards=None, proof=None, source=None, rid='replacement'):
        if source is None:source=self.s.snapshot()['sourceVersion']
        if proof is None:proof=evidence('npc',self.gen,rid,source,guards or [self.g])
        return self.s.stage_delete('npc',self.gen,rid,source,replacement_evidence=proof)

    def test_exact_fence_and_backup_failure_replay_without_release(self):
        src=self.s.snapshot()['sourceVersion'];original=self.s.owner_files('npc')
        proof=evidence('npc',self.gen,'replacement',src,[self.g])
        with patch.object(self.s,'stage_owner_backup',side_effect=OSError('backup failed')):
            with self.assertRaises(OSError):self.stage(proof=proof,source=src)
        restarted=FencedOwnedNPCStore(self.root)
        state=restarted.state('npc')
        self.assertEqual(state['state'],'delete-staged');self.assertEqual(state['leases'],{})
        self.assertNotIn('finished',state)
        self.assertEqual(state['replacementRetirements'][self.gen]['retiredLeases'],{self.g['requestId']:self.g['leaseToken']})
        self.assertEqual(restarted.owner_files('npc'),original)
        a=restarted.stage_delete('npc',self.gen,'replacement',src)
        self.assertEqual(a,restarted.stage_delete('npc',self.gen,'replacement',src,replacement_evidence=proof))
        different=evidence('npc',self.gen,'replacement',src,[self.g]);different['snapshotHash']='d'*64
        with self.assertRaises(Conflict):restarted.stage_delete('npc',self.gen,'replacement',src,replacement_evidence=seal(different))
        with self.assertRaises(Conflict):restarted.stage_delete('npc',self.gen,'different',src)
        with self.assertRaises(Conflict):restarted.prepare_delete('npc',self.gen,'replacement','d'*64)
        with self.assertRaises(Conflict):restarted.finish(self.g)

    def test_fence_write_failure_cannot_drop_live_lease(self):
        before=self.s.state('npc')
        with patch.object(self.s,'write',side_effect=OSError('crash before state write')):
            with self.assertRaises(OSError):self.stage()
        self.assertEqual(FencedOwnedNPCStore(self.root).state('npc'),before)

    def test_altered_or_new_lease_and_changed_source_fail_closed(self):
        src=self.s.snapshot()['sourceVersion'];g2=self.s.admit('npc',self.gen,'new.parent')
        with self.assertRaises(Conflict):self.stage(source=src)
        changed={**g2,'leaseToken':'wrong'}
        with self.assertRaises(Conflict):self.stage(guards=[self.g,changed],source=src)
        with self.assertRaises(Conflict):self.stage(guards=[self.g,g2],source='0'*64)
        self.assertEqual(self.s.generation('npc')['activeLeases'],2)

    def test_exact_pending_and_committed_records_refused(self):
        for suffix in ('.intent','.json'):
            aid='asset.'+hashlib.sha256(b'binari:r.child.pixel-source').hexdigest()[:48]
            p=self.s.safe('.portrait-assets/'+aid+suffix)
            self.s.write(p,b'{}')
            with self.assertRaisesRegex(Conflict,'artifact reservation'):self.stage()
            p.unlink()
        for status in ('prepared','committed','unknown'):
            p=self.s.receipt_path('r.child');self.s.write(p,encoded({'status':status}))
            with self.assertRaisesRegex(Conflict,'publication history'):self.stage()
            p.unlink()
        p=self.s.receipt_path('npc.portrait.'+digest(['r.child','npc','idle','r','r.child']))
        self.s.write(p,encoded({'status':'committed'}))
        with self.assertRaisesRegex(Conflict,'publication history'):self.stage()
        self.assertEqual(self.s.generation('npc')['activeLeases'],1)

    def test_late_asset_batch_portrait_and_next_generation_admit_denied(self):
        stage=self.stage();src=stage['sourceVersion']
        b=b'data';aid=hashlib.sha256(b).hexdigest()+'.png'
        calls=(lambda:self.s.put_asset(aid,b,self.g), lambda:self.s.batch('r.late','h',[],{},self.g),
               lambda:self.s.register_portrait_asset({'runId':'r','projectId':'ProjectBS','sha256':aid[:-4],'size':len(b),'provenance':{}},b,self.g))
        with patch('media_validation.png'):
            for call in calls:
                with self.assertRaises(Conflict):call()
        self.s.prepare_delete('npc',self.gen,'replacement',deletion_scope='canonical-owner-generation.v1')
        result=self.s.delete_character('replacement','h','npc',src,{'characterOrder':[],'generationEpoch':'replacement'}, {'generationId':self.gen})
        self.assertTrue(result['backupVerified'])
        guard=self.s.prepare_create('npc','new.generation',self.s.snapshot()['sourceVersion'])
        state=self.s.state('npc')
        self.assertIn('r',state['retiredRequestIds'])
        self.assertIn(self.gen,state['replacementRetirements'])
        self.assertNotIn('r',state['finished'])
        self.s.create_character('new.generation','x',[{'path':NPC_PREFIX+'npc/metadata.json','expectedHash':None,'body':{'id':'npc'}},{'path':NPC_PREFIX+'metadata.json','expectedHash':self.s.snapshot()['hashes']['metadata.json'],'body':{'characterOrder':['npc'],'generationEpoch':'new.generation'}}],{NPC_PREFIX+'npc/metadata.json':None,NPC_PREFIX+'metadata.json':self.s.snapshot()['hashes']['metadata.json']},guard['expectedSourceVersion'],guard)
        with self.assertRaisesRegex(Conflict,'retired'):self.s.admit('npc',guard['generationId'],'r')

    def test_commit_wins_then_stage_rechecks_and_keeps_active(self):
        src=self.s.snapshot()['sourceVersion'];ready=threading.Event();resume=threading.Event();out=[]
        second=FencedOwnedNPCStore(self.root)
        def publish():
            try:
                with self.s.lock():
                    ready.set()
                    if not resume.wait(3):raise AssertionError('stage thread not started')
                    Fixture.publish(self)
            except Exception as e:out.append(e)
        def retire():
            resume.set()
            try:second.stage_delete('npc',self.gen,'replacement',src,replacement_evidence=evidence('npc',self.gen,'replacement',src,[self.g]))
            except Exception as e:out.append(e)
        one=threading.Thread(target=publish);two=threading.Thread(target=retire)
        one.start();self.assertTrue(ready.wait(3));two.start();one.join(3);two.join(3)
        self.assertEqual(len(out),1,out);self.assertIsInstance(out[0],Conflict)
        self.assertEqual(self.s.generation('npc')['activeLeases'],1)
        self.assertEqual(self.s.receipt('r')['status'],'committed')

    def test_unrelated_npc_and_old_output_records_unchanged(self):
        root=self.s.safe('metadata.json')
        self.s.write(root,encoded({'characterOrder':['npc','other'],'generationEpoch':'init'}))
        self.s.write(self.s.safe('other/metadata.json'),encoded({'id':'other'}))
        other=self.s.bootstrap('other',self.s.snapshot()['sourceVersion'])['generationId']
        untouched=self.s.safe('other/metadata.json').read_bytes()
        historical=self.s.receipt_path('unrelated');self.s.write(historical,encoded({'status':'committed'}))
        before=historical.read_bytes()
        self.stage()
        self.assertEqual(self.s.admit('other',other,'other.parent')['npcId'],'other')
        self.assertEqual(self.s.safe('other/metadata.json').read_bytes(),untouched)
        self.assertEqual(historical.read_bytes(),before)

    def test_crashed_owned_commit_recovers_before_retirement_checks(self):
        src=self.s.snapshot()['sourceVersion']
        proof=evidence('npc',self.gen,'replacement',src,[self.g])
        with patch.object(self.s,'recover_owned'):
            Fixture.publish(self)
        self.assertTrue(self.s.safe('.owned-tx.json').exists())
        restarted=FencedOwnedNPCStore(self.root)
        with self.assertRaisesRegex(Conflict,'stale staged deletion'):
            restarted.stage_delete('npc',self.gen,'replacement',src,replacement_evidence=proof)
        self.assertEqual(restarted.receipt('r')['status'],'committed')
        self.assertEqual(restarted.generation('npc')['activeLeases'],1)

    def test_dangling_exact_publication_symlinks_are_not_absence(self):
        p=self.s.receipt_path('r.child')
        p.parent.mkdir(exist_ok=True);p.symlink_to(self.root/'missing')
        with self.assertRaisesRegex(ValueError,'symlink'):self.stage()
        p.unlink()
        alias='asset.'+hashlib.sha256(b'binari:r.child').hexdigest()[:48]
        p=self.s.safe('.portrait-assets/'+alias+'.intent')
        p.parent.mkdir(exist_ok=True);p.symlink_to(self.root/'missing')
        with self.assertRaisesRegex(ValueError,'symlink'):self.stage()
        self.assertEqual(self.s.generation('npc')['activeLeases'],1)

    def test_writer_uses_typed_keys_without_domain_derivation(self):
        src=self.s.snapshot()['sourceVersion'];proof=evidence('npc',self.gen,'replacement',src,[self.g])
        row=proof['parents'][0]
        row['canonicalCommitRequestIds']=['opaque.commit.name']
        row['artifactReservationIds']=['asset.'+'5'*48]
        seal(proof)
        receipt=self.s.receipt_path('opaque.commit.name')
        self.s.write(receipt,encoded({'status':'committed'}))
        with self.assertRaisesRegex(Conflict,'publication history'):self.stage(proof=proof,source=src)
        receipt.unlink()
        intent=self.s.safe('.portrait-assets/asset.'+'5'*48+'.intent')
        self.s.write(intent,b'{}')
        with self.assertRaisesRegex(Conflict,'reservation history'):self.stage(proof=proof,source=src)
        self.assertEqual(self.s.generation('npc')['activeLeases'],1)

    def test_writer_rejects_paths_and_duplicate_typed_keys(self):
        src=self.s.snapshot()['sourceVersion']
        for key,value in (('canonicalCommitRequestIds',['../outside']),('artifactReservationIds',['asset.'+'1'*48]*2),('artifactReservationIds',[])):
            proof=evidence('npc',self.gen,'replacement',src,[self.g])
            proof['parents'][0][key]=value
            with self.assertRaisesRegex(Conflict,'scope binding'):self.stage(proof=seal(proof),source=src)
        self.assertEqual(self.s.generation('npc')['activeLeases'],1)
