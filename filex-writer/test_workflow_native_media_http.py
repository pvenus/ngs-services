import base64
import hashlib
import json
import sqlite3
import struct
import unittest
import zlib
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request,urlopen

import test_workflow_idle_direction_portrait_http as portrait
import test_workflow_plan_idle_http as idle
from project_data_service.workflow_native_media import NativeMedia,PREFIX,SCHEMAS
from test_workflow_delete_http import digest,encoded
from test_workflow_portrait_representative_http import png

ANIM=PREFIX+'animation';FRAMES=PREFIX+'frames'


def image(w,h,value):
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
    raw=(b'\0'+bytes([value,33,56,255])*w)*h
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,6,0,0,0))
            +chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b''))


def b64(data):return base64.b64encode(data).decode()


class Tests(unittest.TestCase):
    start=portrait.Tests.start
    post=portrait.Tests.post
    plan=portrait.Tests.plan
    approve=portrait.Tests.approve
    preset=portrait.Tests.preset
    publish_plan=portrait.Tests.publish_plan
    read_plan=portrait.Tests.read_plan
    read=portrait.Tests.read
    publish=portrait.Tests.publish
    artifact=portrait.Tests.artifact
    prepare_image=portrait.Tests.prepare_image
    claim_portrait=portrait.Tests.claim_portrait
    upload=portrait.Tests.upload

    def start_new_writer(self):
        idle.Tests.start_idle_writer(self,workflow_idle_portrait=True,workflow_direction_portrait=True,workflow_native_media=True)

    def setUp(self):
        portrait.Tests.setUp(self)
        c,view=self.post({'claim':self.idle},path=portrait.IDLE_PATH+'/read')
        self.assertEqual(c,200)
        self.assertEqual(self.upload('idle',self.idle,view,png(100)),(200,{'ok':True}))
        self.complete(self.idle)
        self.direction_claim=self.claim_portrait('portrait_e','npc.portrait-direction',self.idle['taskId'],9,'e')
        c,view=self.post({'claim':self.direction_claim},path=portrait.DIRECTION_PATH+'/read')
        self.assertEqual(c,200)
        self.portrait_bytes=png(101)
        self.assertEqual(self.upload('direction',self.direction_claim,view,self.portrait_bytes),(200,{'ok':True}))
        p=self.pds.workflow_portrait_representative
        self.pds.workflow_native_media=NativeMedia(p.source,p.verifier)

    def complete(self,claim):
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET status='completed' WHERE task_id=?",(claim['taskId'],))

    def claim_media(self,kind,parent,*,motion='walk',direction='e'):
        key=f'{kind}_{motion}_{direction}';task='workflow.task.'+digest([self.c['officialRequestId'],key])
        worker,typ,mode=(('animation','npc.animation-native-png','output') if kind=='animation' else
                         ('postprocess','npc.frames-postprocess','input'))
        value={'npcId':self.c['npcId'],'motion':motion,'direction':direction,mode:'native-png'}
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,NULL,11,?,'running')",
                       (task,self.c['officialRequestId'],key,typ,worker,encoded(value).decode(),parent['taskId']))
        return {**self.c,'taskId':task,'claimStamp':11}

    def raw(self,claim,count=33,width=320,height=288):
        data=[image(width,height,i) for i in range(count)]
        pkt={'claim':claim,'artifact':{'fps':24,'width':width,'height':height,'frames':[b64(i) for i in data]}}
        return pkt,data

    def fetch(self,path,pkt):
        req=Request(self.base+path,data=json.dumps(pkt).encode(),headers={'Content-Type':'application/json'})
        return urlopen(req,timeout=5)

    def corrected(self,claim,raw):
        w,h=raw['width'],raw['height'];aid=claim['taskId'];fps=raw['fps']
        lineage={k:raw[k] for k in ('npcId','generationId','posePreset','presetId','direction','sourceBindingHash','frameCount','fps')}
        rawhash=digest(raw);frames=[];rows=[];assets={}
        l,t,r,b=1,1,w-1,h-1
        import math
        pad=max(4,math.ceil(max(r-l,b-t)*.1));cw,ch=r-l+2*pad,b-t+2*pad
        pw,ph=(cw+1)//2,(ch+1)//2;sx,sy=pw/cw,ph/ch
        canvas={'width':pw,'height':ph,'sourceWidth':w,'sourceHeight':h,'paddingPixels':math.ceil(pad*.5),
                'marginRatio':.1,'scale':.5,'scaleX':sx,'scaleY':sy,'sourceCanvasWidth':cw,'sourceCanvasHeight':ch,
                'offsetX':(pad-l)*sx,'offsetY':(pad-t)*sy,'unionBounds':[l,t,r,b],
                'pivot':{'x':(w/2-l+pad)*sx,'y':(b-t+pad)*sy}}
        for i,f in enumerate(raw['frames']):
            alpha_bytes=image(w,h,200);pixel_bytes=image(pw,ph,222)
            ah=hashlib.sha256(alpha_bytes).hexdigest();phash=hashlib.sha256(pixel_bytes).hexdigest()
            ap,pp=ah+'.png',phash+'.png'
            assets.update({ap:b64(alpha_bytes),pp:b64(pixel_bytes)})
            rows.append({'index':i,'rawAssetId':f['assetId'],'alphaAssetId':ap,'sha256':ah,'bytes':len(alpha_bytes),'mimeType':'image/png'})
            frames.append({'index':i,'sourceTimestamp':i/fps,'assetId':pp,'rawAssetId':f['assetId'],
                           'alphaAssetId':ap,'sourceBounds':[l,t,r,b]})
        alpha={**lineage,'schemaVersion':'ngs.animation-alpha-output.v1','requestId':aid,'rawRequestId':raw['requestId'],
               'rawManifestId':f"canonical-files/binari/npcs/{raw['npcId']}/animation-sequences/idle/{raw['presetId']}/{raw['direction']}.json",
               'rawManifestHash':rawhash,'dependencyHash':'a'*64,'width':w,'height':h,'frames':rows,
               'inferenceProvenance':{'engine':'birefnet-sam2','modelId':'approved','modelDigest':'a'*64,
                                      'policyHash':'b'*64,'inferenceRequestId':aid,'fixture':False},
               'rawRetention':'retain-native-sequence-independently'}
        from native_pixel_direction import POLICY,WORKFLOW_SCHEMA
        policy={**POLICY,'paletteScope':'all-frames-in-direction','palette':list(range(96))}
        pixel={**lineage,'schemaVersion':WORKFLOW_SCHEMA,'requestId':aid,'rawRetention':'retain-native-sequence-independently',
               'canvas':canvas,'pixelPolicy':policy,'frames':frames,'rawManifestHash':rawhash,
               'alphaManifestHash':digest(alpha),'normalizationHash':digest({'canvas':canvas,'pixelPolicy':policy})}
        return {'claim':claim,'artifact':{'alpha':alpha,'pixel':pixel,'assets':assets}}

    def test_actual_raw_and_corrected_33_frame_route_keeps_original_and_other_files(self):
        for kind in SCHEMAS:
            with urlopen(self.base+PREFIX+kind,timeout=5) as r:
                self.assertEqual(json.load(r),{'enabled':True,'schemaVersion':SCHEMAS[kind]})
        animation=self.claim_media('animation',self.direction_claim)
        self.assertEqual(self.post({'claim':animation},path=ANIM+'/read')[0],403)
        self.complete(self.direction_claim)
        code,view=self.post({'claim':animation},path=ANIM+'/read')
        self.assertEqual((code,view['motion'],view['direction']),(200,'walk','e'))
        with self.fetch(ANIM+'/asset',{'claim':animation,'assetId':view['portraitAssetId']}) as r:
            self.assertEqual(r.headers['content-type'],'image/png')
            self.assertEqual(r.read(),self.portrait_bytes)
        other=self.writer.owner_files('npc.other');pkt,buffers=self.raw(animation)
        prior=self.db.read_bytes()
        with patch.object(self.writer,'receipt_path',side_effect=AssertionError('no receipt')), \
             patch.object(self.writer,'admit',side_effect=AssertionError('no lease')):
            self.assertEqual(self.post(pkt,path=ANIM),(200,{'ok':True}))
            self.assertEqual(self.post(pkt,path=ANIM)[0],409)
        self.assertEqual(self.db.read_bytes(),prior)
        self.complete(animation)
        frames=self.claim_media('frames',animation)
        code,view=self.post({'claim':frames},path=FRAMES+'/read')
        self.assertEqual(code,200);raw=view['sequence']
        self.assertEqual((raw['requestId'],raw['frameCount'],raw['fps']),(animation['taskId'],33,24))
        with self.fetch(FRAMES+'/asset',{'claim':frames,'assetId':raw['frames'][17]['assetId']}) as r:
            self.assertEqual(r.read(),buffers[17])
        with self.assertRaises(HTTPError) as err:self.fetch(FRAMES+'/asset',{'claim':frames,'assetId':'0'*64+'.png'})
        self.assertEqual(err.exception.code,409);err.exception.close()
        malformed=self.corrected(frames,raw);malformed['artifact']['pixel']['rawManifestHash']='0'*64
        self.assertEqual(self.post(malformed,path=FRAMES)[0],409)
        before=self.writer.owner_files('npc.fresh');db=self.db.read_bytes()
        form=self.corrected(frames,raw)
        with patch.object(self.writer,'receipt_path',side_effect=AssertionError('no receipt')):
            self.assertEqual(self.post(form,path=FRAMES),(200,{'ok':True}))
        after=self.writer.owner_files('npc.fresh')
        self.assertEqual({k for k,v in before.items() if after.get(k)!=v},{'npc.fresh/.owned-assets.json'})
        self.assertEqual(self.writer.owner_files('npc.other'),other);self.assertEqual(self.db.read_bytes(),db)
        self.assertEqual(self.writer.generation('npc.fresh')['activeLeases'],0)
        self.assertFalse(any(k.endswith('.mp4') for k in after))
        for i in range(33):
            self.assertTrue(f'npc.fresh/assets/animation-alpha/idle/walk/e/alpha-{i}.png' in after)
            self.assertTrue(f'npc.fresh/assets/animation-processed-directions/idle/walk/e/pixel-{i}.png' in after)

    def test_claim_released_before_filex_http_and_wrong_direction_or_invalid_png_rejected(self):
        self.complete(self.direction_claim)
        wrong=self.claim_media('animation',self.direction_claim,direction='n')
        self.assertEqual(self.post({'claim':wrong},path=ANIM+'/read')[0],403)
        a=self.claim_media('animation',self.direction_claim)
        packet,_=self.raw(a,count=2,width=16,height=16);packet['artifact']['frames'][0]=b64(b'mp4')
        self.assertEqual(self.post(packet,path=ANIM)[0],409)
        from project_data_service.pds_owned_transport import OwnedNPCTransport
        call=OwnedNPCTransport.workflow_media
        def transfer(tx,*args):
            # Exclusive SQLite lock must succeed even during publication transport.
            with sqlite3.connect(self.db,timeout=.2) as db:
                db.execute('BEGIN EXCLUSIVE')
            return call(tx,*args)
        packet,_=self.raw(a,count=2,width=16,height=16)
        with patch.object(OwnedNPCTransport,'workflow_media',transfer):
            self.assertEqual(self.post(packet,path=ANIM),(200,{'ok':True}))

    def test_static_pixel_same_run_chain_with_final_center_reference_preserves_raw(self):
        static=PREFIX+'portrait-pixel'
        npc=self.c['npcId'];prefix=npc+'/';oid=self.c['officialRequestId']
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET status='running',task_input=? WHERE task_id=?",
                       (encoded({'npcId':npc,'imageStyle':'pixel-sprite.v1'}).decode(),self.idle['taskId']))
        _,basis=self.post({'claim':self.idle},path=portrait.IDLE_PATH+'/read')
        center_raw=image(280,256,105)
        self.assertEqual(self.upload('idle',self.idle,basis,center_raw),(200,{'ok':True}))
        self.complete(self.idle)
        def claim_pixel(direction,parent):
            key='portrait_pixel_idle_'+(direction or 'reference')
            tid='workflow.task.'+digest([oid,key])
            inp={'npcId':npc,'posePreset':'idle','direction':direction}
            with sqlite3.connect(self.db) as db:
                db.execute("INSERT INTO workflow_tasks VALUES(?,?,?,?,?,?,NULL,13,?,'running')",
                           (tid,oid,key,'npc.portrait-pixel','postprocess',encoded(inp).decode(),parent['taskId']))
            return {**self.c,'taskId':tid,'claimStamp':13}
        center=claim_pixel(None,self.idle)
        wrong={**center,'taskId':'workflow.task.'+digest([oid,'portrait_pixel_combat_reference'])}
        self.assertEqual(self.post({'claim':wrong},path=static+'/read')[0],403)
        wrong={**center,'taskId':'workflow.task.'+digest([oid,'portrait_pixel_representative_reference'])}
        self.assertEqual(self.post({'claim':wrong},path=static+'/read')[0],403)
        code,data=self.post({'claim':center},path=static+'/read')
        self.assertEqual(code,200);self.assertEqual(data['direction'],None)
        aid=data['source']['assetId'];self.assertEqual(aid,hashlib.sha256(center_raw).hexdigest()+'.png')
        with self.fetch(static+'/asset',{'claim':center,'assetId':aid}) as r:
            self.assertEqual(r.headers['content-type'],'image/png');self.assertEqual(r.read(),center_raw)
        center_final=image(128,128,106);center_aid=hashlib.sha256(center_final).hexdigest()+'.png'
        packet={'claim':center,'artifact':{'sourceAssetId':aid,'base64':b64(center_final)}}
        self.assertEqual(self.post({**packet,'artifact':{**packet['artifact'],'sourceAssetId':'0'*64+'.png'}},path=static)[0],409)
        self.assertEqual(self.post({**packet,'artifact':{**packet['artifact'],'base64':b64(image(129,128,106))}},path=static)[0],409)
        before=self.writer.owner_files(npc);other=self.writer.owner_files('npc.other')
        self.assertEqual(self.post(packet,path=static),(200,{'ok':True}))
        self.assertEqual(self.post(packet,path=static)[0],409)
        after=self.writer.owner_files(npc)
        self.assertEqual(after[prefix+'assets/portraits/idle/portrait.png'],center_raw)
        self.assertEqual(after[prefix+'assets/portraits/idle/pixel.png'],center_final)
        self.assertEqual(after[prefix+'assets/portraits/idle/pixel-source.png'],center_raw)
        self.assertEqual({k for k,v in before.items() if after[k]!=v},{prefix+'metadata.json',prefix+'.owned-assets.json'})
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET depends_on_task_id=?, task_input=? WHERE task_id=?",
                       (center['taskId'],encoded({'npcId':npc,'direction':'e','imageStyle':'pixel-sprite.v1'}).decode(),
                        self.direction_claim['taskId']))
        self.assertEqual(self.post({'claim':self.direction_claim},path=portrait.DIRECTION_PATH+'/read')[0],403)
        self.complete(center)
        code,view=self.post({'claim':self.direction_claim},path=portrait.DIRECTION_PATH+'/read')
        self.assertEqual(code,200)
        self.assertEqual(view['basis']['referenceAssetId'],center_aid)
        self.assertEqual(view['basis']['referenceStage'],'portrait_pixel_idle_reference')
        self.assertEqual(base64.b64decode(view['reference']['base64']),center_final)
        bad={**view,'basis':{**view['basis'],'referenceAssetId':aid}}
        directional=image(250,300,111)
        self.assertEqual(self.upload('direction',self.direction_claim,bad,directional)[0],409)
        self.assertEqual(self.upload('direction',self.direction_claim,view,directional),(200,{'ok':True}))
        self.complete(self.direction_claim)
        anim=self.claim_media('animation',self.direction_claim)
        code,view=self.post({'claim':anim},path=ANIM+'/read');self.assertEqual(code,200)
        with self.fetch(ANIM+'/asset',{'claim':anim,'assetId':view['portraitAssetId']}) as r:
            self.assertEqual(r.read(),directional)
        directional_pixel=claim_pixel('e',self.direction_claim)
        code,source=self.post({'claim':directional_pixel},path=static+'/read');self.assertEqual(code,200)
        predecessor=source['source']['assetId'];self.assertEqual(predecessor,view['portraitAssetId'])
        final=image(128,128,112)
        self.assertEqual(self.post({'claim':directional_pixel,'artifact':{'sourceAssetId':predecessor,'base64':b64(final)}},path=static),(200,{'ok':True}))
        after=self.writer.owner_files(npc)
        self.assertEqual(after[prefix+'assets/pose-views/idle/portrait/e/image.png'],directional)
        self.assertEqual(after[prefix+'assets/pose-views/idle/pixel/e/image.png'],final)
        self.assertEqual(after[prefix+'assets/pose-views/idle/pixel/e/source.png'],directional)
        m=json.loads(after[prefix+'pose-views/idle/pixel/e.json'])
        self.assertEqual((m['sourceArtifactId'],m['referenceArtifactId']),(predecessor,center_aid))
        self.assertEqual(self.writer.owner_files('npc.other'),other)
