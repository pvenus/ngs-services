"""Exercises actual NPC-only authenticated writer with an independent canonical source."""
import base64
import json
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import test_workflow_native_media_http as legacy
from workflow_native_media import REGEN_RECEIPT, REGEN_MODES

ANIMATION='/v1/workflow/animation'
FRAMES='/v1/workflow/frames'


class RegenerationTests(legacy.SelectedTests):
    def setUp(self):
        super().setUp()
        self.complete(self.direction_claim)
        self.pick=self.picks[0]
        self.owner=self.c['npcId']
        self.suffix='combat/'+self.pick['presetId']+'/e'
        self.root=self.writer.root

    def bind(self,kind,salt='1',**overrides):
        oid='request.official.'+salt*64
        motion=self.pick['presetId']
        stem='motion_'+legacy.sha(motion.encode())[:16]+'_e'
        return {'officialRequestId':oid,'taskId':'workflow.task.'+legacy.digest([oid,kind+'_'+stem]),
                'npcId':self.owner,'projectId':'binari','payloadSha256':'f'*64,
                'motion':motion,'posePreset':'combat','direction':'e',
                'assetSetSha256':self.pick['assetSetSha256'],'regeneration':True,**overrides}

    def writer_call(self,path,b,artifact=None):
        with self.pds.workflow_native_media.source.store.factory() as tx:
            return tx.call('POST',path,{'binding':b,**({'artifact':artifact} if artifact is not None else {})})

    def canonical(self):
        return self.writer.snapshot(),self.writer.owner_files(self.owner),self.writer.owner_files('npc.other')

    def seed_original(self):
        anim=self.selected_media('animation',self.direction_claim,self.pick)
        packet,_=self.raw(anim,count=3,width=48,height=32)
        self.assertEqual(self.post(packet,path=legacy.ANIM),(200,{'ok':True}))
        self.complete(anim)
        frames=self.selected_media('frames',anim,self.pick)
        code,result=self.post({'claim':frames},path=legacy.FRAMES+'/read')
        self.assertEqual(code,200)
        self.assertEqual(self.post(self.corrected(frames,result['sequence']),path=legacy.FRAMES),(200,{'ok':True}))
        self.complete(frames)
        return self.canonical()

    def stage(self,salt='1',geometry=(64,40)):
        a,f=self.bind('animation',salt),self.bind('frames',salt)
        result=self.writer_call(ANIMATION+'/read',a)
        source={k:result[k] for k in ('generationId','portraitAssetId','resultBasisSha256')}
        packet,data=self.raw(a,count=2,width=geometry[0],height=geometry[1])
        raw={**packet['artifact'],'source':source}
        self.assertEqual(self.writer_call(ANIMATION,a,raw),{'ok':True})
        self.assertEqual(self.writer_call(ANIMATION,a,raw),{'ok':True})
        sequence=self.writer_call(FRAMES+'/read',f)['sequence']
        post=self.corrected(f,sequence)['artifact']
        return a,f,raw,sequence,post,data

    def test_atomic_regeneration_keeps_old_until_raw_alpha_pixel_are_ready(self):
        prior=self.seed_original()
        with self.pds.workflow_native_media.source.store.factory() as tx:
            self.assertTrue(tx.capabilities().get('workflowRegenerateMotionV1'))
            endpoint=tx.endpoint
        a,f,raw,sequence,post,buffers=self.stage()
        self.assertEqual(self.canonical(),prior)
        new_frame=sequence['frames'][0]['assetId']
        with self.pds.workflow_native_media.source.store.factory() as tx:
            self.assertEqual(tx.call('GET','/v1/asset?id='+new_frame,missing=True),None)
        with self.assertRaises(HTTPError) as http:
            urlopen(Request(endpoint+FRAMES+'/asset',data=json.dumps({'binding':f,'artifact':{'assetId':new_frame}}).encode(),
                            headers={'Content-Type':'application/json'}),timeout=3)
        self.assertEqual(http.exception.code,401)
        http.exception.close()
        expected=sequence['frames'][0]
        frame=self.writer_call(FRAMES+'/asset',f,{'assetId':new_frame})
        self.assertEqual(frame,{**{k:expected[k] for k in ('assetId','sha256','bytes')},
                                'base64':base64.b64encode(buffers[0]).decode()})
        with self.assertRaisesRegex(ValueError,'HTTP_400'):
            self.writer_call(FRAMES+'/asset',f,{'assetId':'0'*64+'.png'})
        with self.assertRaisesRegex(ValueError,'HTTP_400'):
            self.writer_call(FRAMES+'/asset',self.bind('frames','2'),{'assetId':new_frame})
        wrong={**post,'alpha':{**post['alpha'],'rawManifestHash':'0'*64}}
        with self.assertRaisesRegex(ValueError,'HTTP_400'):
            self.writer_call(FRAMES,f,wrong)
        self.assertEqual(self.canonical(),prior)
        self.assertEqual(self.writer_call(FRAMES,f,post),{'ok':True})
        self.assertEqual(self.writer_call(FRAMES,f,post),{'ok':True})
        snapshot,owner,other=self.canonical()
        self.assertEqual(other,prior[2])
        untouched={p:v for p,v in prior[1].items() if self.suffix not in p and not p.endswith('.owned-assets.json')}
        self.assertEqual({p:owner[p] for p in untouched},untouched)
        for layer,asset in zip(REGEN_MODES,('raw','alpha','pixel')):
            path=f'{self.owner}/{layer}/{self.suffix}.json'
            self.assertEqual(snapshot['documents'][path]['frameCount'],2)
            self.assertEqual(snapshot['documents'][path]['generationId'],raw['source']['generationId'])
            self.assertEqual(snapshot['documents'][path]['requestId'],a['taskId'] if asset=='raw' else f['taskId'])
            self.assertFalse((self.root/self.owner/'assets'/layer/self.suffix/(asset+'-2.png')).exists())
        self.assertEqual(owner[f'{self.owner}/assets/animation-sequences/{self.suffix}/raw-0.png'],buffers[0])
        receipt=self.writer.receipt(f['taskId'])
        self.assertEqual(receipt['schemaVersion'],REGEN_RECEIPT)
        self.assertEqual(receipt['status'],'committed')
        self.assertEqual(len(receipt['hashes']),3)

    def test_two_staged_jobs_cannot_overwrite_the_winner_or_accept_stale_basis(self):
        self.seed_original()
        a,f,raw,sequence,post,_=self.stage('3')
        b,g,other_raw,other_sequence,other_post,_=self.stage('4',(72,40))
        self.assertEqual(raw['source'],other_raw['source'])
        self.assertEqual(self.writer_call(FRAMES,f,post),{'ok':True})
        previous=self.canonical()
        with self.assertRaisesRegex(ValueError,'HTTP_400'):
            self.writer_call(FRAMES,g,other_post)
        with self.assertRaisesRegex(ValueError,'HTTP_400'):
            self.writer_call(ANIMATION,b,other_raw)
        self.assertEqual(self.canonical(),previous)
        self.assertFalse(self.writer.receipt_path(g['taskId']).exists())

    def test_empty_slot_can_publish_without_modifying_appearance(self):
        before=self.canonical()
        _,f,raw,_,post,_=self.stage('7')
        self.assertEqual(self.canonical(),before)
        self.assertEqual(self.writer_call(FRAMES,f,post),{'ok':True})
        after=self.canonical()
        self.assertEqual(before[2],after[2])
        self.assertEqual({k:v for k,v in before[1].items() if not k.endswith('/.owned-assets.json')},
                         {k:v for k,v in after[1].items() if k in before[1] and not k.endswith('/.owned-assets.json')})
        for layer in REGEN_MODES:
            self.assertIn(f'{self.owner}/{layer}/{self.suffix}.json',after[0]['documents'])

    def test_empty_slot_allowed_but_replaced_generation_never_commits(self):
        a,f,raw,sequence,post,_=self.stage('5')
        with self.writer.lock():
            state=self.writer.state(self.owner)
            self.writer.write(self.writer.safe(self.writer.state_path(self.owner)),
                              legacy.encoded({**state,'generationId':'9'*48}))
        with self.assertRaisesRegex(ValueError,'HTTP_400'):
            self.writer_call(FRAMES,f,post)
        with self.assertRaisesRegex(ValueError,'HTTP_400'):
            self.writer_call(ANIMATION,a,raw)
        self.assertFalse(self.writer.receipt_path(f['taskId']).exists())
        self.assertFalse(any(self.suffix in name for name in self.writer.owner_files(self.owner)))

    def test_write_interruption_recovers_complete_candidate_under_lock_then_replays(self):
        self.seed_original()
        _,f,_,_,post,_=self.stage('6')
        before=self.canonical()
        real=self.writer.write
        tripped=[]
        def interrupted(path,data):
            if not tripped and path.relative_to(self.root).as_posix().startswith(
                    f'{self.owner}/animation-alpha/{self.suffix}'):
                tripped.append(True)
                raise OSError('fixture write interruption')
            return real(path,data)
        with patch.object(self.writer,'write',interrupted):
            with self.assertRaisesRegex(ValueError,'HTTP_503'):
                self.writer_call(FRAMES,f,post)
        self.assertTrue(tripped)
        self.assertTrue((self.root/'.owned-tx.json').exists())
        observed=self.canonical()
        for layer in REGEN_MODES:
            name=f'{self.owner}/{layer}/{self.suffix}.json'
            self.assertEqual(observed[0]['documents'][name]['frameCount'],2)
            self.assertEqual(before[0]['documents'][name]['frameCount'],3)
        self.assertEqual(self.writer_call(FRAMES,f,post),{'ok':True})
        self.assertFalse((self.root/'.owned-tx.json').exists())
