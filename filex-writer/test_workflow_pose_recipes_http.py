"""Exact Planner claim, PDS HTTP, and canonical Filex data, using synthetic PNGs."""
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import test_workflow_native_media_http as media
import test_workflow_plan_representative_http as documents
import test_workflow_preset_claim_http as fixture
from project_data_service.filex_npc_authority import FilexCurrentNPC
from project_data_service.filex_npc_store import FilexNPCStore
from project_data_service.pds_owned_transport import OwnedNPCTransport
from project_data_service.workflow_delete_claim import SELECTION_V2_NEW, REGENERATE_V2
from project_data_service.workflow_native_media import NativeMedia
from project_data_service.workflow_plan_idle import IdlePlan
from project_data_service.workflow_plan_representative import RepresentativePlan
from project_data_service.workflow_portrait_representative import RepresentativePortrait, VARIANTS
from project_data_service.workflow_pose_recipes import POSE_RECIPES
from server import handler

PLAN='/v1/npcs/ownership/workflow-plan-'
PIXEL=media.PREFIX+'portrait-pixel'
OWNER='npc.fresh'
REF_ASSET='f'*64


class Tests(unittest.TestCase):
    start=fixture.Tests.start
    post=fixture.Tests.post
    approve=fixture.Tests.approve
    preset=fixture.Tests.preset
    upload=media.portrait.Tests.upload
    raw=media.NativeFixture.raw
    corrected=media.NativeFixture.corrected
    fetch=media.NativeFixture.fetch

    def start_new_writer(self):
        _,self.preset_writer_url=self.start(handler(self.writer,self.original_token,workflow_delete=True,
            workflow_preset=True,workflow_representative=True,workflow_portrait=True,workflow_idle_plan=True,
            workflow_idle_portrait=True,workflow_direction_portrait=True,workflow_native_media=True,workflow_shared_plan=True))
        cfg={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','scope':'npc','enabled':True,
             'endpoint':self.preset_writer_url,'tokenFile':str(Path(self.writer.root).parent/'writer-token')}
        source=FilexCurrentNPC(FilexNPCStore(lambda:OwnedNPCTransport(cfg)))
        from project_data_service.workflow_preset import WorkflowPreset
        self.pds.workflow_preset=WorkflowPreset(source,lambda c: self.pb if self.running and c==self.claim else None)

    def setUp(self):
        fixture.Tests.setUp(self)
        s=self.pds.workflow_preset
        self.pds.workflow_native_media=NativeMedia(s.source,s.verify)
        self.pds.workflow_plan_representative=RepresentativePlan(s.source,s.verify,documents.FixtureConcept())
        for stage in ('overview','portrait-art','idle','combat'):
            setattr(self.pds,'workflow_plan_'+stage.replace('-','_'),IdlePlan(s.source,s.verify,stage=stage))
        for mode in VARIANTS:
            setattr(self.pds,'workflow_portrait_'+mode,RepresentativePortrait(s.source,s.verify,mode=mode))
        self.refs=patch.object(self.pds.workflow_native_media,'_selected_source',
                               return_value={'referenceSha256':REF_ASSET,'referencePresetHash':'0'*64})
        self.refs.start();self.addCleanup(self.refs.stop)

    def make_run(self,poses):
        fixture.Tests.plan(self)
        self.picks={pose:{'presetId':f'motion-{pose}','posePreset':pose,'assetSetSha256':'a'*64}
                    for pose in poses}
        with sqlite3.connect(self.db) as db:
            src=json.loads(db.execute('SELECT payload FROM workflow_runs').fetchone()[0])
            src.pop('includeCombat')
            src.update(schemaVersion=SELECTION_V2_NEW,motionSelection=list(self.picks.values()))
            self.c['payloadSha256']=media.digest(src)
            db.execute('UPDATE workflow_runs SET schema_version=?,payload=?,payload_sha256=?',
                       (SELECTION_V2_NEW,media.encoded(src).decode(),self.c['payloadSha256']))
        if 'combat' in poses:
            rows=self.art['profile']['planningCategories']
            cid='category.preset.'+media.digest(['npc.standard','3',self.c['taskId'],'portrait.combat'])[:32]
            self.assertEqual(self.preset()[0],409)
            rows.append({'id':cid,'name':'전투 포즈 기획','content':'','planningRole':'portrait.combat'})
            self.art['profile']['planningPreset']['visibleCategoryIds'].append(cid)
        result,body=self.preset()
        self.assertEqual(result,200,body)
        self.done(self.c)

    def task_claim(self,key,kind,lane,parent=None,*,fields=None,basis=None):
        base=self.c if basis is None else basis
        oid=base['officialRequestId']
        task='workflow.task.'+media.digest([oid,key])
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,NULL,15,?,'running')",
                       (task,oid,key,kind,lane,media.encoded({'npcId':OWNER,**(fields or {})}).decode(),
                        parent['taskId'] if parent else None))
        return {**base,'taskId':task,'claimStamp':15}

    def done(self,claim):
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET status='completed',result_ref=task_id WHERE task_id=?",(claim['taskId'],))

    def plan_claim(self,stage,parent):
        return self.task_claim('planning_'+stage.replace('-','_'),'npc.plan-'+stage,'document',parent)

    def plan_write(self,claim,stage):
        code,view=self.post({'claim':claim,'inputMode':'folder-snapshot'},path=PLAN+stage+'/read')
        self.assertEqual(code,200,view)
        if stage=='combat':
            self.assertEqual((view['representativeContent'],view['portraitArtContent']),
                             ('representative planning content','portrait-art planning content'))
        basis=view['basis']
        artifact={'schemaVersion':f'workflow.npc-{stage}-plan-artifact.v1','projectId':'binari',
                  'npcId':OWNER,'taskId':claim['taskId'],'categoryId':basis['categoryId'],
                  'content':stage+' planning content'}
        self.assertEqual(self.post({'claim':claim,'basis':basis,'artifact':artifact},path=PLAN+stage),
                         (200,{'ok':True}))
        self.done(claim)
        return claim

    def image_write(self,claim,mode,value,w=256,h=256,reference=None):
        code,view=self.post({'claim':claim},path=VARIANTS[mode][0]+'/read')
        self.assertEqual(code,200,view)
        if reference is not None:
            self.assertEqual(view['reference']['base64'],media.b64(reference))
        data=media.image(w,h,value)
        self.assertEqual(self.upload(mode,claim,view,data),(200,{'ok':True}))
        self.done(claim)
        return data

    def pixel_claim(self,pose,direction,parent):
        return self.task_claim(f'portrait_pixel_{pose}_{direction or "reference"}',
                          'npc.portrait-pixel','postprocess',parent,fields={'posePreset':pose,'direction':direction})

    def pixel_write(self,claim,pose,original,value):
        code,view=self.post({'claim':claim},path=PIXEL+'/read')
        self.assertEqual(code,200,view)
        self.assertEqual(view['posePreset'],pose)
        self.assertEqual(view['source']['assetId'],hashlib.sha256(original).hexdigest()+'.png')
        result=media.image(128,128,value)
        packet={'claim':claim,'artifact':{'sourceAssetId':view['source']['assetId'],'base64':media.b64(result)}}
        self.assertEqual(self.post(packet,path=PIXEL),(200,{'ok':True}))
        self.done(claim)
        return result

    def prepare_common(self):
        # Both exact common doc rows must exist before overview is admitted.
        overview=self.plan_claim('overview',self.c)
        art=self.plan_claim('portrait-art',overview)
        self.plan_write(overview,'overview')
        self.plan_write(art,'portrait-art')
        rep=self.plan_write(self.plan_claim('representative',art),'representative')
        picture=self.task_claim('portrait_representative','npc.portrait-representative','image',rep)
        raw=self.image_write(picture,'representative',44)
        return rep,raw

    def direction(self,pose,rep,reference,num):
        recipe=POSE_RECIPES[pose]
        plan=self.plan_write(self.plan_claim(pose,rep),pose)
        center=self.task_claim(recipe.reference_key,recipe.reference_type,'image',plan,fields={'imageStyle':'pixel-sprite.v1'})
        original=self.image_write(center,pose,num,reference=reference)
        if pose=='combat':
            # Intermediate canonical center is recognized only with its exact
            # workflow preset role and matching dedicated inventory source.
            from project_data_service.domain_contracts.legacy_http import validate_npcs
            authority=self.pds.workflow_portrait_combat.source
            document=authority.current()['document']
            document['characters']=[c for c in document['characters'] if c['id']==OWNER]
            validate_npcs(document,authority.asset_get)
        center_pixel=self.pixel_claim(pose,None,center)
        cp=self.pixel_write(center_pixel,pose,original,num+1)
        direction=self.task_claim(recipe.direction_prefix+'e',recipe.direction_type,'image',center_pixel,
                             fields={'imageStyle':'pixel-sprite.v1','direction':'e'})
        slot=pose if pose=='idle' else 'combat_direction'
        if pose=='idle':slot='direction'
        view=self.image_write(direction,slot,num+2,reference=cp)
        corrected=self.pixel_claim(pose,'e',direction)
        return direction,corrected,view

    def motion_claim(self,kind,pick,parent,*,basis=None,d='e'):
        fields={'posePreset':pick['posePreset'],'motion':pick['presetId'],'assetSetSha256':pick['assetSetSha256'],'direction':d}
        token=hashlib.sha256(pick['presetId'].encode()).hexdigest()[:16]
        runner,field,typ=('animation','output','npc.animation-native-png') if kind=='animation' else ('postprocess','input','npc.frames-postprocess')
        return self.task_claim(f'{kind}_motion_{token}_{d}',typ,runner,parent,
                          fields={**fields,field:'native-png'},basis=basis)

    def assert_output(self,pose,dir_claim,pixel,png,result):
        self.assertEqual(self.post({'claim':result},path=media.ANIM+'/read')[0],403)
        self.pixel_write(pixel,pose,png,199)
        with sqlite3.connect(self.db) as db:
            db.execute('UPDATE workflow_tasks SET depends_on_task_id=? WHERE task_id=?',(dir_claim['taskId'],result['taskId']))
        self.assertEqual(self.post({'claim':result},path=media.ANIM+'/read')[0],403)
        with sqlite3.connect(self.db) as db:
            db.execute('UPDATE workflow_tasks SET depends_on_task_id=? WHERE task_id=?',(pixel['taskId'],result['taskId']))
        code,read=self.post({'claim':result},path=media.ANIM+'/read')
        self.assertEqual(code,200,read)
        self.assertEqual(read['portraitAssetId'],hashlib.sha256(png).hexdigest()+'.png')
        with self.fetch(media.ANIM+'/asset',{'claim':result,'assetId':read['portraitAssetId']}) as res:
            self.assertEqual(res.read(),png)
        pick=self.picks[pose];body,_=self.raw(result,count=2,width=48,height=32)
        self.assertEqual(self.post(body,path=media.ANIM),(200,{'ok':True}))
        self.done(result)
        frames=self.motion_claim('frames',pick,result)
        status,value=self.post({'claim':frames},path=media.FRAMES+'/read')
        self.assertEqual(status,200,value)
        seq=value['sequence']
        key=POSE_RECIPES[pose].direction_prefix+'e'
        self.assertEqual(seq['sourceBindingHash'],media.digest([self.c['officialRequestId'],key,pick['presetId'],pose,pick['assetSetSha256']]))
        self.assertEqual(self.post(self.corrected(frames,seq),path=media.FRAMES),(200,{'ok':True}))
        self.done(frames)

    def test_new_basic_original_centers_use_only_new_owner_and_no_pixel_artifact(self):
        fixture.Tests.plan(self)
        with sqlite3.connect(self.db) as db:
            src=json.loads(db.execute('SELECT payload FROM workflow_runs').fetchone()[0])
            src.update(schemaVersion='ngs.npc-one-stop.basic.v1',includeCombat=True)
            src['character']['slug']='fresh'
            src['character']['actId']='act01'
            self.art['character']=src['character']
            self.c['payloadSha256']=media.digest(src)
            db.execute('UPDATE workflow_runs SET schema_version=?,payload=?,payload_sha256=?',
                       (src['schemaVersion'],media.encoded(src).decode(),self.c['payloadSha256']))
        rows=self.art['profile']['planningCategories']
        cid='category.preset.'+media.digest(['npc.standard','3',self.c['taskId'],'portrait.combat'])[:32]
        rows.append({'id':cid,'name':'전투 포즈 기획','content':'','planningRole':'portrait.combat'})
        self.art['profile']['planningPreset']['visibleCategoryIds'].append(cid)
        code,body=self.preset()
        self.assertEqual(code,200,body)
        self.done(self.c)
        rep,raw=self.prepare_common()
        for pose in ('idle','combat'):
            recipe=POSE_RECIPES[pose]
            plan=self.plan_write(self.plan_claim(pose,rep),pose)
            center=self.task_claim(recipe.reference_key,recipe.reference_type,'image',plan)
            self.image_write(center,pose,45 if pose=='idle' else 46,reference=raw)
            self.assertEqual(self.post({'claim':self.pixel_claim(pose,None,center)},path=PIXEL+'/read')[0],403)
        authority=self.pds.workflow_portrait_combat.source
        npc=next(c for c in authority.current()['document']['characters'] if c['id']==OWNER)
        self.assertEqual(set(npc['portraitSets']),{'representative','idle','combat'})
        self.assertTrue(all('pixelAssetId' not in slot for slot in npc['portraitSets'].values()))
        self.assertFalse(npc.get('poseViews'))

    def test_real_mixed_route_same_pose_pixel_barrier_and_actual_raw_bytes(self):
        self.make_run(('combat','idle'))
        rep,ref=self.prepare_common()
        for i,pose in enumerate(('idle','combat')):
            direction,pixel,png=self.direction(pose,rep,ref,100+i*10)
            self.assert_output(pose,direction,pixel,png,self.motion_claim('animation',self.picks[pose],pixel))
        files=self.writer.owner_files(OWNER)
        self.assertIn(OWNER+'/pose-views/combat/pixel/e.json',files)
        self.assertIn(OWNER+'/pose-views/idle/pixel/e.json',files)
        self.assertIn(OWNER+'/animation-processed-directions/combat/motion-combat/e.json',files)
        self.assertIn(OWNER+'/animation-processed-directions/idle/motion-idle/e.json',files)

    def test_idle_only_denies_combat_and_preserves_raw_pixel_gate(self):
        self.make_run(('idle',))
        rep,ref=self.prepare_common()
        combat=self.plan_claim('combat',rep)
        self.assertEqual(self.post({'claim':combat,'inputMode':'folder-snapshot'},path=PLAN+'combat/read')[0],403)
        direction,pixel,png=self.direction('idle',rep,ref,120)
        self.assert_output('idle',direction,pixel,png,self.motion_claim('animation',self.picks['idle'],pixel))
        self.assertFalse(any('/combat/' in path for path in self.writer.owner_files(OWNER)))

    @unittest.skipUnless(hasattr(__import__('fenced_store').FencedOwnedNPCStore,'workflow_media_asset'),
                         'active scoped writer owns regeneration asset path')
    def test_regeneration_v2_reads_exact_existing_pose_not_idle_or_wrong_direction(self):
        self.make_run(('combat',))
        rep,ref=self.prepare_common()
        _,_,idle=self.direction('idle',rep,ref,124)
        pick=self.picks['combat']
        payload={'schemaVersion':REGENERATE_V2,'projectId':'binari','npcId':OWNER,'motionSelection':[pick]}
        oid='request.official.'+'f'*64
        base={**self.c,'officialRequestId':oid,'payloadSha256':media.digest(payload)}
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)",
                       (oid,'binari','npc.animation.regenerate',REGENERATE_V2,media.encoded(payload).decode(),base['payloadSha256']))
        req=self.motion_claim('animation',pick,None,basis=base)
        wrong=self.motion_claim('animation',pick,None,basis=base,d='w')
        self.assertEqual(self.post({'claim':req},path=media.ANIM+'/read')[0],409)
        _,_,combat=self.direction('combat',rep,ref,153)
        code,value=self.post({'claim':req},path=media.ANIM+'/read')
        self.assertEqual(code,200,value)
        self.assertEqual(value['portraitAssetId'],hashlib.sha256(combat).hexdigest()+'.png')
        self.assertNotEqual(value['portraitAssetId'],hashlib.sha256(idle).hexdigest()+'.png')
        self.assertEqual(self.post({'claim':wrong},path=media.ANIM+'/read')[0],409)
        body,_=self.raw(req,count=2,width=48,height=32)
        body['artifact']['source']={k:value[k] for k in ('generationId','portraitAssetId','resultBasisSha256')}
        self.assertEqual(self.post(body,path=media.ANIM),(200,{'ok':True}))
        self.done(req)
        post=self.motion_claim('frames',pick,req,basis=base)
        status,value=self.post({'claim':post},path=media.FRAMES+'/read')
        self.assertEqual(status,200,value)
        seq=value['sequence']
        self.assertEqual(seq['sourceBindingHash'],media.digest([oid,'portrait_combat_e',pick['presetId'],'combat',pick['assetSetSha256']]))
        self.assertEqual(self.post(self.corrected(post,seq),path=media.FRAMES),(200,{'ok':True}))
