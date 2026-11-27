import base64
import hashlib
import json
import sqlite3
import struct
import unittest
import zlib
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import test_workflow_plan_representative_http as plan_fixture
from project_data_service.workflow_portrait_representative import ARTIFACT, PATH, SCHEMA, RepresentativePortrait
from test_workflow_delete_http import digest, encoded, handler


def png(red):
    def chunk(kind, data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))
            +chunk(b'IDAT',zlib.compress(bytes([0,red,0,0])))+chunk(b'IEND',b''))


class Tests(unittest.TestCase):
    start=plan_fixture.Tests.start
    post=plan_fixture.Tests.post
    plan=plan_fixture.Tests.plan
    approve=plan_fixture.Tests.approve
    preset=plan_fixture.Tests.preset
    publish_plan=plan_fixture.Tests.publish
    read_plan=plan_fixture.Tests.read

    def start_new_writer(self):
        plan_fixture.Tests.start_new_writer(self,workflow_portrait=True)

    def setUp(self):
        plan_fixture.Tests.setUp(self)
        status,view=self.read_plan();self.assertEqual(status,200)
        self.assertEqual(self.publish_plan(view['basis']),(200,{'ok':True}))
        self.plan_id=self.c['taskId']
        self.c['taskId']='workflow.task.'+digest([self.c['officialRequestId'],'portrait_representative'])
        self.c['claimStamp']=4
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET status='completed' WHERE task_id=?",(self.plan_id,))
            db.execute("""INSERT INTO workflow_tasks VALUES(?,?,'portrait_representative','npc.portrait-representative',
                       'image',?,NULL,4,?,'running')""",
                       (self.c['taskId'],self.c['officialRequestId'],encoded({'npcId':self.c['npcId']}).decode(),self.plan_id))
        s=self.pds.workflow_plan_representative
        self.pds.workflow_portrait_representative=RepresentativePortrait(s.source,s.verifier)

    def read(self,claim=None):
        return self.post({'claim':self.c if claim is None else claim},path=PATH+'/read')

    def artifact(self,raw):
        return {'schemaVersion':ARTIFACT,'taskId':self.c['taskId'],'projectId':'binari','npcId':'npc.fresh',
                'sha256':hashlib.sha256(raw).hexdigest(),'base64':base64.b64encode(raw).decode()}

    def publish(self,basis,raw=None,*,artifact=None):
        return self.post({'claim':self.c,'basis':basis,'artifact':artifact or self.artifact(raw)},path=PATH)

    def test_current_plan_png_and_overwrite_no_receipt(self):
        from unittest.mock import patch
        with urlopen(self.base+PATH,timeout=3) as response:
            self.assertEqual(json.load(response),{'enabled':True,'schemaVersion':SCHEMA})
        other=self.writer.owner_files('npc.other')
        initial=self.writer.owner_files('npc.fresh')
        before_db=self.db.read_bytes()
        prefix='npc.fresh/';slot=prefix+'assets/portraits/representative/portrait.png'
        task=self.c['taskId']; rp=self.writer.receipt_path(task)
        self.writer.write(rp,b'{"ignored":true}')
        with patch.object(self.writer,'receipt_path',side_effect=AssertionError('no history')), \
             patch.object(self.writer,'admit',side_effect=AssertionError('no lease')):
            status,view=self.read();self.assertEqual(status,200)
            self.assertEqual(set(view),{'basis','character','planningContent'})
            self.assertEqual(view['planningContent'],'새 대표 포즈 기획')
            self.assertEqual(self.publish(view['basis'],png(1)),(200,{'ok':True}))
            self.assertEqual(view['basis'].keys(),{'categoryId'})
            self.assertEqual(self.publish(view['basis'],png(2)),(200,{'ok':True}))
        self.assertEqual(self.db.read_bytes(),before_db)
        self.assertEqual(rp.read_bytes(),b'{"ignored":true}')
        self.assertEqual(self.writer.owner_files('npc.other'),other)
        self.assertEqual(self.writer.generation('npc.fresh')['activeLeases'],0)
        current=self.writer.owner_files('npc.fresh')
        self.assertEqual({k for k in set(initial)|set(current) if initial.get(k)!=current.get(k)},
                         {slot,prefix+'metadata.json',prefix+'.owned-assets.json'})
        self.assertEqual(current[slot],png(2))
        meta=json.loads(current[prefix+'metadata.json']);aid=hashlib.sha256(png(2)).hexdigest()+'.png'
        self.assertEqual(meta['portraitSets'],{'representative':{'portraitAssetId':aid,'requestId':task}})
        self.assertEqual(meta['activePortraitId'],aid)
        self.assertEqual([p['assetId'] for p in meta['portraits']],[aid])
        authority=self.pds.workflow_portrait_representative.source
        self.assertEqual(authority.asset_get(aid),png(2))
        # Validate the new slot using the existing NPC domain reader. The base
        # fixture's intentionally ID-only unrelated NPC is not a valid profile.
        from project_data_service.domain_contracts.legacy_http import validate_npcs
        doc=authority.current()['document']
        doc['characters']=[npc for npc in doc['characters'] if npc['id']=='npc.fresh']
        validate_npcs(doc,authority.asset_get)
        # Original-only does not expand the legacy combat contract.
        target=doc['characters'][0]
        target['portraitSets']['combat']=target['portraitSets'].pop('representative')
        with self.assertRaisesRegex(ValueError,'NPC_PORTRAIT_PAIR_INVALID'):
            validate_npcs(doc,authority.asset_get)

    def test_claim_and_current_basis_integrity_and_wrong_worker(self):
        with self.assertRaises(HTTPError) as failure:
            urlopen(Request(self.base+PATH,headers={'Origin':'http://localhost'}),timeout=3)
        self.assertEqual(failure.exception.code,403);failure.exception.close()
        self.assertEqual(self.read({**self.c,'claimStamp':5})[0],403)
        self.assertEqual(self.read({**self.c,'npcId':'npc.other'})[0],403)
        status,view=self.read();self.assertEqual(status,200)
        b=view['basis'];a=self.artifact(png(1));before=self.writer.snapshot()['sourceVersion']
        self.assertEqual(self.publish({'categoryId':'category.preset.'+'0'*32},artifact=a)[0],409)
        for key,value in [('generationId','0'*48),('sourceVersion','0'*64),('revision',2),('inputSha256','0'*64)]:
            self.assertEqual(self.publish({**b,key:value},artifact=a)[0],409)
        self.assertEqual(self.publish(b,artifact={**a,'sha256':'0'*64})[0],409)
        self.assertEqual(self.publish(b,b'invalid image')[0],409)
        self.assertEqual(self.publish(b,artifact={**a,'npcId':'npc.other'})[0],409)
        self.assertEqual(self.publish(b,artifact={**a,'outputPath':'../outside'})[0],409)
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET worker_type='document' WHERE task_id=?",(self.c['taskId'],))
        self.assertEqual(self.publish(b,artifact=a)[0],403)
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET worker_type='image',depends_on_task_id=? WHERE task_id=?",(self.preset_id,self.c['taskId']))
        self.assertEqual(self.read()[0],403)
        self.assertEqual(self.writer.snapshot()['sourceVersion'],before)
        self.assertFalse(self.writer.receipt_path(self.c['taskId']).exists())

    def test_real_save_mode_holds_no_database_read_lock_over_filex(self):
        from unittest.mock import patch
        from project_data_service.pds_owned_transport import OwnedNPCTransport
        svc=self.pds.workflow_portrait_representative
        svc.verifier.file_save_handoff=True
        with sqlite3.connect(self.db) as db:
            db.execute('ALTER TABLE workflow_tasks ADD COLUMN save_started_at INTEGER')
        _,value=self.read();basis=value['basis'];orig=OwnedNPCTransport.workflow_portrait_representative
        def send(tx,binding,raw,**kw):
            with sqlite3.connect(self.db,timeout=0) as db:
                self.assertIsNotNone(db.execute('SELECT save_started_at FROM workflow_tasks WHERE task_id=?',(self.c['taskId'],)).fetchone()[0])
                db.execute('UPDATE workflow_tasks SET updated_at=updated_at+1 WHERE task_id=?',(self.preset_id,))
            self.assertEqual(self.publish(basis,png(2))[0],403)
            return orig(tx,binding,raw,**kw)
        with patch.object(OwnedNPCTransport,'workflow_portrait_representative',send):
            self.assertEqual(self.publish(basis,png(1)),(200,{'ok':True}))
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE workflow_tasks SET status='completed',save_started_at=NULL WHERE task_id=?",(self.c['taskId'],))
            self.assertEqual(db.execute('SELECT status,save_started_at FROM workflow_tasks WHERE task_id=?',(self.c['taskId'],)).fetchone(),('completed',None))

    def test_filex_transport_loss_is_terminal_on_the_exact_portrait_attempt(self):
        from unittest.mock import patch
        from project_data_service.pds_owned_transport import OwnedNPCTransport
        svc=self.pds.workflow_portrait_representative
        svc.verifier.file_save_handoff=True
        with sqlite3.connect(self.db) as db:
            db.execute('ALTER TABLE workflow_tasks ADD COLUMN save_started_at INTEGER')
        _,value=self.read();basis=value['basis']
        with patch.object(OwnedNPCTransport,'workflow_portrait_representative',side_effect=TimeoutError) as write:
            self.assertEqual(self.publish(basis,png(1))[0],503)
            self.assertEqual(write.call_count,1)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT status,save_started_at FROM workflow_tasks WHERE task_id=?',(self.c['taskId'],)).fetchone(),('failed',None))

    def change_files(self,owner,modify):
        # Isolated concurrent publisher fixture, using the existing real Filex
        # current byte transaction and generating the same source/revision fence.
        from npc_document import decode
        from ownership_plan import plan
        with self.writer.lock():
            docs=self.writer.snapshot_unlocked()['documents']
            path=modify(docs)
            root=docs['metadata.json'];marker=root['_pdsCurrent'];doc=decode(docs);doc.pop('_pdsCurrent')
            root['_pdsCurrent']={**marker,'revision':marker['revision']+1,'documentHash':digest(doc)}
            prefix=owner+'/'
            manifest=plan(owner,{k.removeprefix(prefix):v for k,v in docs.items() if k.startswith(prefix)})
            self.writer.commit_owned(None,None,{p:encoded(obj) for p,obj in {
                'metadata.json':root,path:docs[path],prefix+'.owned-assets.json':manifest}.items()})
            return self.writer.safe(path).read_bytes()

    def test_disjoint_idle_and_other_npc_writes_during_render_stay_intact(self):
        code,start=self.read();self.assertEqual(code,200);b=start['basis']
        owner='npc.fresh'
        revision=self.writer.snapshot()['documents']['metadata.json']['_pdsCurrent']['revision']
        def idle(docs):
            path=next(p for p,r in docs.items() if p.startswith(owner+'/planning/') and r.get('planningRole')=='portrait.idle')
            docs[path]['content']='동시에 게시한 아이들 기획'
            return path
        def other(docs):
            path='npc.other/metadata.json'
            docs[path]['name']='다른 NPC 갱신'
            return path
        idle_bytes=self.change_files(owner,idle)
        other_bytes=self.change_files('npc.other',other)
        _,current=self.read()
        self.assertEqual(current['basis'],b)
        self.assertEqual(self.publish(b,png(1)),(200,{'ok':True}))
        docs=self.writer.snapshot()['documents']
        self.assertEqual(encoded(next(r for p,r in docs.items() if p.startswith(owner+'/planning/') and
                                      r.get('planningRole')=='portrait.idle')),idle_bytes)
        self.assertEqual(encoded(docs['npc.other/metadata.json']),other_bytes)
        self.assertEqual(docs['metadata.json']['_pdsCurrent']['revision'],revision+3)

    def test_no_read_to_write_fence_even_after_plan_update_preserves_latest_files(self):
        from unittest.mock import patch
        from project_data_service.pds_owned_transport import OwnedNPCTransport
        _,start=self.read();b=start['basis'];owner='npc.fresh'
        path=owner+'/planning/'+b['categoryId']+'.json'
        send=OwnedNPCTransport.workflow_portrait_representative
        def race(tx,binding,body,**kwargs):
            self.assertTrue({'inputSha256','generationId','sourceVersion','revision'}.isdisjoint(binding))
            def new_plan(docs):
                docs[path]['content']='저장 직전 새 대표 기획'
                return path
            self.change_files(owner,new_plan)
            return send(tx,binding,body,**kwargs)
        with patch.object(OwnedNPCTransport,'workflow_portrait_representative',race):
            self.assertEqual(self.publish(b,png(1)),(200,{'ok':True}))
        self.assertEqual(json.loads(self.writer.safe(path).read_bytes())['content'],'저장 직전 새 대표 기획')
        slot=self.writer.safe(owner+'/assets/portraits/representative/portrait.png')
        self.assertEqual(slot.read_bytes(),png(1))
        # No old basis checks, but existing canonical bytes still need to match
        # their own slot hash. Corrupt local datums are not silently overwritten.
        slot.write_bytes(b'fixture corruption')
        self.assertEqual(self.publish(b,png(2))[0],409)
        self.assertEqual(slot.read_bytes(),b'fixture corruption')

    def test_receiptless_success_and_disabled_writer(self):
        _,view=self.read()
        self.assertEqual(self.publish(view['basis'],png(1)),(200,{'ok':True}))
        self.assertFalse(self.writer.receipt_path(self.c['taskId']).exists())
        _,uri=self.start(handler(self.writer,self.original_token,workflow_representative=True))
        r=Request(uri+'/v1/workflow/portrait-representative',headers={'Authorization':'Bearer '+self.original_token,
                  'Content-Type':'application/json'},data=b'{}')
        with self.assertRaises(HTTPError) as failure:urlopen(r,timeout=3)
        self.assertEqual(failure.exception.code,404);failure.exception.close()
