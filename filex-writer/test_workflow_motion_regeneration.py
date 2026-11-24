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
        motion=overrides.get('motion',self.pick['presetId'])
        direction=overrides.get('direction','e')
        stem='motion_'+legacy.sha(motion.encode())[:16]+'_'+direction
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

    def test_formal_PDS_claims_route_staged_asset_then_publish_same_slot(self):
        import sqlite3
        old=self.seed_original()
        a,f=self.bind('animation','8'),self.bind('frames','8')
        oid=a['officialRequestId']
        payload={'schemaVersion':'ngs.npc-animation.regenerate.v1','projectId':'binari',
                 'npcId':self.owner,'motionSelection':[self.pick]}
        ph=legacy.digest(payload)
        claim=lambda b:{**{k:b[k] for k in ('officialRequestId','taskId','projectId','npcId')},
                        'payloadSha256':ph,'claimStamp':31}
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',31,NULL)",
                       (oid,'binari','npc.animation.regenerate',payload['schemaVersion'],legacy.encoded(payload).decode(),ph))
            for b,kind,parent in ((a,'animation',None),(f,'frames',a['taskId'])):
                motion=self.pick['presetId'];stem=f"motion_{legacy.sha(motion.encode())[:16]}_e"
                worker,typ,io=('animation','npc.animation-native-png','output') if kind=='animation' else (
                    'postprocess','npc.frames-postprocess','input')
                frame_input={k:b[k] for k in ('npcId','direction','motion','posePreset','assetSetSha256')}
                frame_input[io]='native-png'
                db.execute("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,NULL,31,?,'running')",
                           (b['taskId'],oid,kind+'_'+stem,typ,worker,legacy.encoded(frame_input).decode(),parent))
        ac,fc=claim(a),claim(f)
        status,read=self.post({'claim':ac},path=legacy.ANIM+'/read')
        self.assertEqual(status,200,read)
        self.assertEqual(read['portraitAssetId'],legacy.sha(self.portrait_bytes)+'.png')
        self.assertEqual(read['referenceSha256'],legacy.sha(self.references[self.pick['presetId']][1]['e']))
        self.assertEqual(len(read['generationId']),48)
        self.assertEqual(len(read['resultBasisSha256']),64)
        packet,original=self.raw(ac,count=2,width=64,height=40)
        packet['artifact']['source']={k:read[k] for k in ('generationId','portraitAssetId','resultBasisSha256')}
        self.assertEqual(self.post(packet,path=legacy.ANIM),(200,{'ok':True}))
        self.assertEqual(self.canonical(),old)
        self.assertEqual(self.post({'claim':fc},path=legacy.FRAMES+'/read')[0],403)
        self.complete(ac)
        status,read=self.post({'claim':fc},path=legacy.FRAMES+'/read')
        self.assertEqual(status,200,read)
        sequence=read['sequence'];asset=sequence['frames'][0]['assetId']
        with self.fetch(legacy.FRAMES+'/asset',{'claim':fc,'assetId':asset}) as response:
            self.assertEqual(response.headers['content-type'],'image/png')
            self.assertEqual(response.read(),original[0])
        with self.assertRaises(HTTPError) as http:
            self.fetch(legacy.FRAMES+'/asset',{'claim':fc,'assetId':'7'*64+'.png'})
        self.assertEqual(http.exception.code,409)
        http.exception.close()
        self.assertEqual(self.canonical(),old)
        processed=self.corrected(fc,sequence)
        self.assertEqual(self.post(processed,path=legacy.FRAMES),(200,{'ok':True}))
        after=self.canonical()
        self.assertEqual(after[2],old[2])
        self.assertEqual(after[1][f'{self.owner}/assets/animation-sequences/{self.suffix}/raw-0.png'],original[0])
        for mode in REGEN_MODES:
            self.assertEqual(after[0]['documents'][f'{self.owner}/{mode}/{self.suffix}.json']['frameCount'],2)

    def test_shared_parent_can_stage_multiple_presets_and_directions_before_postprocessing(self):
        second=self.claim_portrait('portrait_w','npc.portrait-direction',self.idle['taskId'],19,'w')
        status,view=self.post({'claim':second},path=legacy.portrait.DIRECTION_PATH+'/read')
        self.assertEqual(status,200,view)
        self.assertEqual(self.upload('direction',second,view,legacy.png(109)),(200,{'ok':True}))
        self.complete(second)
        before=self.canonical()
        staged=[]
        for direction,pick,w in (('e',self.pick,48),('w',self.pick,56),('e',self.picks[1],64)):
            self.assertEqual(pick['posePreset'],'combat')
            a,f=(self.bind(kind,'9',direction=direction,motion=pick['presetId'],assetSetSha256=pick['assetSetSha256'])
                 for kind in ('animation','frames'))
            view=self.writer_call(ANIMATION+'/read',a)
            packet,data=self.raw(a,count=2,width=w,height=32)
            source={k:view[k] for k in ('generationId','portraitAssetId','resultBasisSha256')}
            self.assertEqual(self.writer_call(ANIMATION,a,{**packet['artifact'],'source':source}),{'ok':True})
            staged.append((f,data))
        self.assertEqual(self.canonical(),before)
        for f,data in staged:
            sequence=self.writer_call(FRAMES+'/read',f)['sequence']
            self.assertEqual((sequence['direction'],sequence['presetId']),(f['direction'],f['motion']))
            raw=self.writer_call(FRAMES+'/asset',f,{'assetId':sequence['frames'][0]['assetId']})
            self.assertEqual(raw['base64'],legacy.b64(data[0]))
            self.assertEqual(self.writer_call(FRAMES,f,self.corrected(f,sequence)['artifact']),{'ok':True})
        after=self.canonical()
        self.assertEqual(after[2],before[2])
        for f,data in staged:
            self.assertEqual(after[1][f"{self.owner}/assets/animation-sequences/combat/{f['motion']}/{f['direction']}/raw-0.png"],data[0])


def load_tests(loader, suite, pattern):
    # Reuse the canonical fixture setup, but do not run its inherited tests
    # twice after both fixtures moved to the same canonical suite.
    import unittest
    return unittest.TestSuite(RegenerationTests(name) for name in vars(RegenerationTests) if name.startswith('test_'))
