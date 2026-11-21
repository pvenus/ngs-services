import hashlib
import json
import sqlite3
from unittest.mock import patch
from urllib.request import urlopen

import test_workflow_npc_planning_update_http as update
from project_data_service.workflow_npc_planning_add import ARTIFACT,PATH,SCHEMA,TYPE,PlanningAdd
from test_workflow_delete_http import digest,encoded

class Tests(update.Tests):
    def start_new_writer(self):
        update.rep.Tests.start_new_writer(self,workflow_shared_plan=True,workflow_npc_planning_update=True,
                                          workflow_npc_folder_delete=True,workflow_npc_planning_add=True)

    def setUp(self):
        super().setUp()
        self.add_service=PlanningAdd(self.service.source,self.service.verifier)
        self.pds.workflow_npc_planning_add=self.add_service

    def add_claim(self,seed='1',**over):
        p={'schemaVersion':SCHEMA,'projectId':'binari','npcId':'npc.fresh','categoryId':'category.add.'+seed*32,
           'title':'관계 문서 '+seed,'outline':'대상 관계를 정리', 'requirements':'',
           'requiredSections':['관계','말투'],**over}
        oid='request.official.'+seed*64;tid='workflow.task.'+digest([oid,'planning_add'])
        with sqlite3.connect(self.db)as db:
            db.execute("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)",
                (oid,'binari',TYPE,SCHEMA,encoded(p).decode(),digest(p)))
            db.execute("INSERT INTO workflow_tasks VALUES(?,?,'planning_add','npc.planning.add','document',?,NULL,3,NULL,'running')",
                (tid,oid,encoded({k:p[k]for k in ('npcId','categoryId')}).decode()))
        return {'officialRequestId':oid,'taskId':tid,'projectId':'binari','npcId':p['npcId'],
                'payloadSha256':digest(p),'claimStamp':3}

    def add_read(self,c):
        return self.post({'claim':c},path=PATH+'/read')
    def add_publish(self,c,source,body,**over):
        return self.post({'claim':c,'artifact':{'schemaVersion':ARTIFACT,**source,'content':body,**over}},path=PATH)

    def test_two_independent_markdown_docs_preserve_four_and_other_owner(self):
        with urlopen(self.base+PATH,timeout=3)as r:
            self.assertEqual(json.load(r),{'enabled':True,'schemaVersion':'workflow.npc-planning-add-write.v1'})
        owner='npc.fresh';prefix=owner+'/';start=self.writer.owner_files(owner);neighbor=self.writer.owner_files('npc.other')
        before=json.loads(start[prefix+'metadata.json']);body='# 관계 문서\n\n## 관계\n\n관계 내용\n\n## 말투\n\n말투 내용'
        c=self.add_claim();status,source=self.add_read(c);self.assertEqual(status,200)
        self.assertEqual(set(source),{'categoryId','title','generationId'})
        self.assertEqual(self.add_read({**c,'claimStamp':99})[0],403)
        self.assertEqual(self.add_read({**c,'npcId':'npc.other'})[0],403)
        self.assertEqual(self.add_publish(c,source,body,title='wrong')[0],403)
        self.assertEqual(self.add_publish(c,source,body,categoryId='category.add.'+'0'*32)[0],403)
        self.assertEqual(self.add_publish(c,source,body,generationId='a'*48)[0],409)
        self.assertEqual(self.writer.owner_files(owner),start)
        with patch.object(self.writer,'admit',side_effect=AssertionError('no lease')), \
             patch.object(self.writer,'delete_character',side_effect=AssertionError('no delete')):
            self.assertEqual(self.add_publish(c,source,body),(200,{'ok':True}))
            self.assertEqual(self.add_publish(c,source,body),(200,{'ok':True}))
            self.assertEqual(self.add_publish(c,source,body+' altered')[0],409)
            d=self.add_claim('2');st,detail=self.add_read(d);self.assertEqual(st,200)
            self.assertEqual(self.add_publish(d,detail,body+' (second)'),(200,{'ok':True}))
        files=self.writer.owner_files(owner);meta=json.loads(files[prefix+'metadata.json'])
        self.assertEqual(meta,{**before,'planningCategoryOrder':before['planningCategoryOrder']+[source['categoryId'],detail['categoryId']]})
        self.assertEqual({k for k,v in start.items()if files[k]!=v},{prefix+'metadata.json',prefix+'.owned-assets.json'})
        self.assertEqual(self.writer.owner_files('npc.other'),neighbor)
        expected=set()
        snap=self.service.source.store.snapshot()
        for item,md in ((source,body),(detail,body+' (second)')):
            logical=prefix+'planning/'+item['categoryId']+'.json';path=logical.removesuffix('.json')+'/content.md'
            expected.update((logical,path));self.assertEqual(files[path].decode(),md)
            self.assertEqual(json.loads(files[logical]),{'id':item['categoryId'],'uuid':item['categoryId'].removeprefix('category.'),
                'name':item['title'],'planningRole':'custom','contentFormat':'markdown.v1','contentSha256':hashlib.sha256(md.encode()).hexdigest()})
            self.assertEqual(snap['documents'][logical]['content'],md)
        self.assertEqual(set(files)-set(start),expected)
        self.state('representative','running')
        st,out=self.read('representative');self.assertEqual(st,200)
        self.assertEqual(out['portraitArtContent'],'# portrait-art\n\n초기 원본')
        role_claim=self.new_update_claim();st,out=self.update_read(role_claim);self.assertEqual(st,200)
        self.assertEqual(out['source'],'# portrait-art\n\n초기 원본')

    def test_unbound_custom_files_and_duplicate_intake_fail_closed(self):
        self.assertEqual(self.add_read(self.add_claim('3',requiredSections=['a','a']))[0],403)
        self.assertEqual(self.add_read(self.add_claim('4',categoryId='../escape'))[0],403)
        c=self.add_claim('5');st,d=self.add_read(c);self.assertEqual(st,200)
        parent=self.writer.safe('npc.fresh/planning/'+d['categoryId']);parent.mkdir()
        self.assertEqual(self.add_publish(c,d,'# no overwrite')[0],409)
        self.assertFalse((parent/'content.md').exists())
        z=self.add_claim('6');st,a=self.add_read(z);self.assertEqual(st,200)
        self.assertEqual(self.add_publish(z,a,'# canonical'),(200,{'ok':True}))
        self.assertEqual(self.add_read(self.add_claim('7',categoryId=a['categoryId']))[0],409)
        self.assertEqual(self.add_read(self.add_claim('8',title=a['title']))[0],409)
        self.assert_generic_move(a['categoryId'],'# canonical')
