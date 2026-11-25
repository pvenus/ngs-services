from test_workflow_delete_http import Tests as DeleteFixture
from test_workflow_delete_http import digest, handler, encoded
from project_data_service.workflow_preset import PATH, WorkflowPreset
from project_data_service.domain_contracts.legacy_http import NPC_TEXT_FIELDS
from project_data_service.domain_contracts.profile_model import blank_profile, TRAIT_GROUPS
from server import Conflict

class Tests(DeleteFixture):
    def setUp(self):
        DeleteFixture.setUp(self)
        with self.writer.lock():
            import json
            p=self.writer.root/'metadata.json';m=json.loads(p.read_bytes())
            m.update(schemaVersion=1,actId='act01',masterRevision=1,storyRevision=1)
            self.writer.write(p,encoded(m))
        self.b['expectedSourceVersion']=self.writer.snapshot()['sourceVersion']
        self.start_new_writer()
        self.approve('npc.fresh')
    def start_new_writer(self):
        from pathlib import Path
        from project_data_service.filex_npc_store import FilexNPCStore
        from project_data_service.filex_npc_authority import FilexCurrentNPC
        from project_data_service.pds_owned_transport import OwnedNPCTransport
        _,self.preset_writer_url=self.start(handler(self.writer,self.original_token,workflow_delete=True,workflow_preset=True))
        cfg={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','scope':'npc','enabled':True,
             'endpoint':self.preset_writer_url,'tokenFile':str(Path(self.writer.root).parent/'writer-token')}
        source=FilexCurrentNPC(FilexNPCStore(lambda:OwnedNPCTransport(cfg)))
        self.pds.workflow_preset=WorkflowPreset(source,lambda c: self.pb if self.running and c==self.claim else None)
    def approve(self,owner,predecessor=None,official='official.1',*,act='act01',chapter='chapter01'):
        p={k:'' for k in NPC_TEXT_FIELDS if k!='name'};p['role']='마을 주민'
        c={'npcId':owner,'name':'테스트 인물','slug':'fixture','role':p['role'],'actId':act,'chapterId':chapter}
        ids=['category.preset.'+'a'*32]
        p.update(traits={k:dict.fromkeys(v) for k,v in TRAIT_GROUPS.items()},numericProfile=blank_profile(),
                 planningCategories=[{'id':ids[0],'name':'기본 기획','content':''}],
                 planningPreset={'id':'npc.standard','version':'3','visibleCategoryIds':ids})
        self.art={'schemaVersion':'workflow.npc-preset-artifact.v1','taskId':'preset.task','projectId':'binari',
                  'character':c,'profile':p,'presetRegistrySha256':'b'*64,'contentGenerated':False,'oldDataReused':False}
        snap=self.writer.snapshot()
        doc=snap['documents']['metadata.json']['_pdsCurrent']
        self.pb={'officialRequestId':official,'taskId':self.art['taskId'],'projectId':'binari','npcId':owner,
                 'artifactSha256':digest(self.art),'payloadSha256':self.b['payloadSha256'],'expectedRevision':doc['revision'],
                 'expectedSourceVersion':snap['sourceVersion'],'predecessorReceiptRef':predecessor}
    def preset(self,body=None,**kw):
        return self.post(body if body is not None else {'binding':self.pb,'artifact':self.art,'claim':self.claim},path=PATH,**kw)
    def test_new_create_exact_commit_and_replay_without_reservations(self):
        from unittest.mock import patch
        other=self.writer.owner_files('npc.other')
        with patch.object(self.writer,'prepare_lifecycle',side_effect=AssertionError()), patch.object(self.writer,'admit',side_effect=AssertionError()):
            code,r=self.preset()
            self.assertEqual(code,200)
            self.assertEqual(self.preset(),(200,r))
        self.assertEqual(r,{'schemaVersion':'workflow.preset-publication-receipt.v1',
                            **{k:self.pb[k] for k in ('officialRequestId','taskId','projectId','npcId','payloadSha256','artifactSha256')},
                            'operation':'npc.preset','status':'committed','verified':True,'receiptId':'preset.task'})
        gen=self.writer.state('npc.fresh')
        self.assertEqual((gen['state'],gen['leases'],gen['previousGenerationId']),('active',{},None))
        self.assertNotIn('lifecycle',gen)
        self.assertEqual(self.writer.snapshot()['documents']['npc.fresh/metadata.json']['chapters'],['chapter01'])
        self.assertEqual(self.writer.owner_files('npc.other'),other)
    def test_replace_needs_exact_storage_delete_receipt_not_workflow_status_recheck(self):
        self.assertEqual(self.post()[0],200)
        self.approve('npc.test',self.b['taskId'],'official.wrong')
        self.assertEqual(self.preset()[0],409)
        self.approve('npc.test',act='act1',chapter='ch1')
        self.assertEqual(self.preset()[0],200)
        g=self.writer.state('npc.test')
        self.assertEqual((g['previousGenerationId'],g['state'],g['leases']),(self.g,'active',{}))
        self.assertNotEqual(g['generationId'],self.g)
        self.assertIn('old.lane',g['retiredRequestIds'])
        with self.assertRaises(Conflict):self.writer.put_asset('b'*64+'.png',b'x',self.legacy)
    def test_denials_never_create(self):
        self.running=False
        self.assertEqual(self.preset()[0],403)
        self.running=True
        self.assertEqual(self.preset(headers={'Origin':'http://127.0.0.1'})[0],403)
        self.art['profile']['summary']='generated'
        self.assertEqual(self.preset()[0],409)
        self.pb['artifactSha256']=digest(self.art)
        self.assertEqual(self.preset()[0],409)
        self.assertEqual(self.post({},path='/v1/workflow/preset',base=self.preset_writer_url)[0],401)
        self.assertEqual(self.post({},path='/v1/workflow/preset',base=self.writer_url,
                         headers={'Authorization':'Bearer '+self.original_token})[0],404)
        del self.pds.workflow_preset
        self.assertEqual(self.preset()[0],404)
        self.assertFalse(self.writer.safe(self.writer.state_path('npc.fresh')).exists())
        self.assertFalse((self.writer.root/'npc.fresh').exists())

    def test_stale_preset_has_explicit_no_write_proof(self):
        from unittest.mock import patch
        from server import PresetPrewriteConflict
        with patch.object(self.writer,'workflow_preset',side_effect=PresetPrewriteConflict('workflow preset stale snapshot')):
            self.assertEqual(self.preset(),
                             (409,{'error':'WORKFLOW_PRESET_REJECTED_BEFORE_WRITE'}))
        self.assertFalse((self.writer.root/'npc.fresh').exists())
        self.assertFalse(self.writer.receipt_path(self.art['taskId']).exists())
