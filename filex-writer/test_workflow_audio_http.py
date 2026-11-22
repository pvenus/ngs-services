"""Real isolated loopback PDS and exact scoped authenticated Filex writer."""
import base64,io,json,os,sqlite3,time,unittest,wave
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from unittest.mock import patch

import test_workflow_concept_category_http as fixture
from concept_store import encoded,digest
from project_data_service.project_folders import configured
from server import handler
from workflow_audio_store import AudioResult,WRITER_PATH,sha
from project_data_service.concept_filex import FilexConcept
from project_data_service.filex_cas_transport import FilexCASTransport
from project_data_service.workflow_audio import WorkflowAudio,PATH,PREFIX,REJECTION,SCHEMA
from project_data_service.workflow_concept_category import CategoryWorkflow
from project_data_service.workflow_delete_claim import PrivatePlannerClaim
from project_data_service.workflow_audio_delete import storage_provider,ROOT as AUDIO_ROOT
from project_data_service.single_target_delete import SingleTargetDelete,PATH as DELETE

def wav(seconds=10,hz=8000,channels=1,amplitude=0):
    scratch=io.BytesIO()
    with wave.open(scratch,'wb') as f:
        f.setnchannels(channels);f.setsampwidth(2);f.setframerate(hz)
        f.writeframes(amplitude.to_bytes(2,'little')*int(seconds*hz)*channels)
    return scratch.getvalue()

def audio(raw):
    return {'mimeType':'audio/wav','bytes':len(raw),'sha256':sha(raw),'base64':base64.b64encode(raw).decode('ascii')}

class Tests(unittest.TestCase):
    service=fixture.Tests.service
    def setUp(self):
        sterile=fixture.Tests()
        self.addCleanup(sterile.doCleanups);sterile.setUp()
        self.sterile=sterile
        self.writer,self.data,self.db,self.pds=(sterile.writer,sterile.data,sterile.db,sterile.pds)
        self.canonical=self.data.parent/'canonical-files'
        self.root=self.canonical/'binari'/'audio';self.root.mkdir(parents=True)
        self.pds.project_folders=configured(self.canonical,'127.0.0.1')
        self.neighbor=self.canonical/'binari'/'npcs'/'metadata.json'
        self.neighbor.parent.mkdir();self.neighbor.write_bytes(b'fixture neighbor')
        self.section=sterile.other
        self.root_metadata=self.data/'metadata.json'
        previous=json.loads(self.root_metadata.read_bytes())
        h=previous['document']['hierarchy']
        h['groups'][0]['categoryIds'].remove(self.section)
        h['groups'].append({'id':'group.sound','title':'Sound','categoryIds':[self.section]})
        self.writer.write(self.root_metadata,encoded(previous))
        self.audio=AudioResult(self.writer,self.root)
        self.token='f'*32
        http=self.service(handler(self.writer,self.token,workflow_audio=self.audio,workflow_audio_delete=True))
        self.writer_url=f'http://127.0.0.1:{http.server_port}'
        cfg=dict(schemaVersion='pds-filex-cas.v1',projectId='binari',enabled=True,
                 endpoint=self.writer_url,tokenFile=str(self.data.parent/'token'))
        st=self.db.stat()
        self.source=FilexConcept(transport_factory=lambda:FilexCASTransport(cfg))
        self.auth=PrivatePlannerClaim(str(self.db),st.st_dev,st.st_ino)
        self.pds.workflow_audio=WorkflowAudio(self.source,self.auth)

    def call(self,body=None,suffix='',base=None,headers=None):
        url=(base or f'http://127.0.0.1:{self.pds.server_port}'+PATH)+suffix
        req=Request(url,data=encoded(body) if body is not None else None,
                    headers={'Content-Type':'application/json',**(headers or {})})
        try:r=urlopen(req,timeout=5)
        except HTTPError as exc:r=exc
        with r:return r.code,json.load(r)

    def plan(self,kind='bgm',cid=None,*,key='fixture',duration=10,text='',extra=None):
        cid=cid or self.section
        oid='request.official.'+sha(key.encode())
        tid='workflow.task.'+digest([oid,'audio_generate'])
        p={'schemaVersion':'ngs.audio-generate.v1','projectId':'binari','kind':kind,'conceptId':cid,
           'title':'Official audio fixture','brief':'not copied to result','text':'isolated synthetic text' if kind=='tts' else text,
           'durationSeconds':None if kind=='tts' else duration,**(extra or {})}
        with sqlite3.connect(self.db) as q:
            q.execute("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)",
                      (oid,'binari','audio.generate',p['schemaVersion'],encoded(p).decode(),digest(p)))
            q.execute("INSERT INTO workflow_tasks VALUES(?,?,'audio_generate','audio.generate','audio',?,NULL,2,NULL,'running')",
                      (tid,oid,encoded({'kind':kind,'conceptId':cid}).decode()))
        return {'officialRequestId':oid,'taskId':tid,'projectId':'binari','payloadSha256':digest(p),'claimStamp':2}

    def body(self,claim,raw):
        status,d=self.call({'claim':claim},'/read')
        self.assertEqual(status,200,d)
        return {'claim':claim,'source':{k:d[k] for k in ('conceptId','sectionSha256')},'artifact':audio(raw)}

    def test_exact_filex_scope_and_replay_then_unrelated_edit(self):
        self.assertEqual(self.call(),(200,{'enabled':True,'schemaVersion':SCHEMA}))
        claim=self.plan();before=self.db.read_bytes()
        raw=wav();wire=self.body(claim,raw);wire['artifact']['bpm']=92
        _,source=self.call({'claim':claim},'/read')
        self.assertIn('한글',source['content']);self.assertNotIn(self.sterile.custom,source['content'])
        root=json.loads(self.root_metadata.read_bytes());root['revision']=999
        root['sectionIds'].reverse();root['document']['hierarchy']['groups'].reverse()
        self.writer.write(self.root_metadata,encoded(root))
        self.assertEqual(self.call({'claim':claim},'/read'),(200,source))
        ack={'ok':True,'resultRef':PREFIX+claim['officialRequestId']+'/metadata.json'}
        self.assertEqual(self.call(wire),(200,ack));self.assertEqual(self.call(wire),(200,ack))
        d=self.root/claim['officialRequestId'];self.assertEqual(sorted(p.name for p in d.iterdir()),['audio.wav','metadata.json'])
        self.assertEqual((d/'audio.wav').read_bytes(),raw)
        recorded=json.loads((d/'metadata.json').read_bytes())
        self.assertEqual(recorded['source'],wire['source'])
        self.assertEqual(recorded['audio']['sha256'],wire['artifact']['sha256'])
        self.assertEqual(recorded['audio']['durationSeconds'],10)
        self.assertEqual((recorded['bpm'],recorded['bpmBasis']),(92,'model'))
        self.assertEqual(recorded['taskId'],claim['taskId']);self.assertNotIn('brief',recorded);self.assertNotIn('text',recorded)
        self.assertEqual(self.call({**wire,'artifact':audio(wav(amplitude=1))})[0],409)
        self.assertEqual((d/'audio.wav').read_bytes(),raw)
        self.assertEqual(self.db.read_bytes(),before)


    def test_direct_concept_is_derived_from_exact_claim_and_never_reads_filex_master(self):
        direct='직접 입력한 콘셉트\n가창은 짧게 🎵'
        expected={'conceptId':'direct','sectionSha256':sha(direct.encode('utf-8')),
                  'title':'직접 입력','content':direct}
        c=self.plan(cid='direct',duration=None,extra={'conceptText':direct,'lyricsRequest':'별빛으로 짧게'})
        with patch.object(self.audio,'concept_locked',side_effect=AssertionError('no Filex concept lookup')):
            self.assertEqual(self.call({'claim':c},'/read'),(200,expected))
            self.assertEqual(self.call({'claim':{**c,'payloadSha256':'f'*64}},'/read'),(422,{'error':REJECTION}))
            self.assertEqual(self.call({'claim':c,'conceptText':'spoofed'},'/read'),(422,{'error':REJECTION}))
            wire=self.body(c,wav())
            forged={**wire,'source':{**wire['source'],'sectionSha256':'f'*64}}
            self.assertEqual(self.call(forged),(422,{'error':REJECTION}))
            self.assertEqual(list(self.root.iterdir()),[])
            self.assertEqual(self.call(wire)[0],200)
            self.assertEqual(self.call(wire)[0],200)
        recorded=json.loads((self.root/c['officialRequestId']/'metadata.json').read_bytes())
        self.assertEqual(recorded['source'],wire['source'])
        self.assertEqual(recorded['payloadSha256'],c['payloadSha256'])
        for key in ('conceptText','directConceptSha256','lyricsRequest','text','brief'):
            self.assertNotIn(key,recorded)

    def test_vocal_private_payload_contract_and_untrimmed_lyrics(self):
        valid=self.plan(cid='direct',duration=None,text='  [Verse]\r\n별이 떴어\n  ',
                        extra={'conceptText':'직접 입력'})
        self.assertEqual(self.call({'claim':valid},'/read')[0],200)
        for i,(cid,duration,text,extra) in enumerate([
            ('direct',None,'',{}),
            ('direct',None,'',{'conceptText':'  uncanonical  '}),
            ('direct',None,'',{'conceptText':'x'*4001}),
            (self.section,None,'',{'conceptText':'잘못된 조합'}),
            (self.section,None,'',{'lyricsRequest':'  uncanonical  '}),
            (self.section,None,'fixture lyric',{'lyricsRequest':'가사 요청'}),
            (self.section,None,' \n ',{}),
            (self.section,10,'',{'lyricsRequest':'가사 요청'}),
        ]):
            claim=self.plan(cid=cid,key='invalid'+str(i),duration=duration,text=text,extra=extra)
            self.assertEqual(self.call({'claim':claim},'/read'),(422,{'error':REJECTION}))
        self.assertEqual(list(self.root.iterdir()),[])

    def test_model_chosen_duration_false_before_write_and_actual_in_result(self):
        claim=self.plan(duration=None,text='[Humming]\n음—')
        wire=self.body(claim,wav(90,hz=48000,channels=2))
        for bad in (wav(9),wav(121)):
            self.assertEqual(self.call({**wire,'artifact':audio(bad)}),(422,{'error':REJECTION}))
        self.assertEqual(self.call(wire)[0],200)
        recorded=json.loads((self.root/claim['officialRequestId']/'metadata.json').read_bytes())
        self.assertIsNone(recorded['durationSeconds'])
        self.assertEqual(recorded['audio']['durationSeconds'],90)
        self.assertNotIn('text',recorded)
        self.assertNotIn('brief',recorded)

    def test_denied_wrong_claim_archived_non_sound_and_nonlocal(self):
        claim=self.plan();raw=wav()
        self.assertEqual(self.call({'claim':{**claim,'claimStamp':3}},'/read'),(422,{'error':REJECTION}))
        self.assertEqual(self.call({'claim':self.plan(cid='expression',key='other')},'/read'),(422,{'error':REJECTION}))
        self.assertEqual(self.call({'claim':self.plan(cid=self.sterile.custom,key='archive')},'/read'),(422,{'error':REJECTION}))
        self.assertEqual(self.call({'claim':claim},'/read',headers={'Origin':'http://localhost'})[0],403)
        self.assertEqual(self.call({'conceptId':self.section},base=self.writer_url+WRITER_PATH,suffix='/read')[0],401)
        self.assertEqual(self.call({**self.body(claim,raw),'destination':'/tmp/other'}),(422,{'error':REJECTION}))
        for kind in ('effect','tts'):
            self.assertEqual(self.call({'claim':self.plan(kind=kind,key=kind)},'/read'),(422,{'error':REJECTION}))
        self.assertEqual(list(self.root.iterdir()),[])

    def test_changed_selected_source_or_bad_wav_is_definite_before_write(self):
        c=self.plan(kind='bgm');w=self.body(c,wav(hz=48000,channels=2))
        a=w['artifact']
        for bad in ({**a,'sha256':'f'*64},{**a,'bpm':301},{**a,'bpmBasis':'model'},audio(wav(9.8)),audio(wav()[:-2]),audio(b'RIFF'+b'\0'*80)):
            self.assertEqual(self.call({**w,'artifact':bad}),(422,{'error':REJECTION}))
        field=self.data/'sections'/self.section/'fields/fixture.json';value=json.loads(field.read_bytes());value['value']='edited active source'
        with self.writer.lock():self.writer.write(field,encoded(value))
        self.assertEqual(self.call(w),(422,{'error':REJECTION}));self.assertEqual(list(self.root.iterdir()),[])
        current=self.body(c,wav(hz=48000,channels=2))
        self.assertEqual(self.call(current)[0],200)
        recorded=json.loads((self.root/c['officialRequestId']/'metadata.json').read_bytes())
        self.assertEqual((recorded['bpm'],recorded['bpmBasis']),(None,'unavailable'))

    def test_rename_response_loss_exact_replay_and_raw_visibility(self):
        c=self.plan();w=self.body(c,wav(hz=24000));dest=self.root/c['officialRequestId']
        real=os.rename
        def lost(a,b,*args,**kwargs):
            if Path(b)==dest:
                self.assertFalse(dest.exists())
                self.assertEqual({p.name for p in Path(a).iterdir()},{'audio.wav','metadata.json'})
                real(a,b,*args,**kwargs)
                raise OSError('fixture response loss after complete rename')
            return real(a,b,*args,**kwargs)
        with patch('workflow_audio_store.os.rename',lost):
            self.assertIn(self.call(w)[0],(409,503))
        self.assertTrue((dest/'audio.wav').is_file());self.assertTrue((dest/'metadata.json').is_file())
        self.assertEqual(self.call(w)[0],200)
        self.assertEqual(len(list(self.root.iterdir())),1)

    def test_migrated_exact_markdown_authority_is_used(self):
        c=self.plan()
        self.pds.workflow_concept_category=CategoryWorkflow(self.source,self.auth)
        migrate=self.sterile.claim('migrate',self.section,'Test 10','category_generate')
        status,d=self.sterile.request({'claim':migrate},'/read');self.assertEqual(status,200)
        self.assertEqual(self.sterile.publish(migrate,'Test 10',d['source'])[0],200)
        w=self.body(c,wav())
        self.assertEqual(self.call(w)[0],200)

    def test_read_only_canonical_folder_sync_reads_exact_pair(self):
        c=self.plan();raw=wav(hz=24000);req=self.body(c,raw)
        self.assertEqual(self.call(req)[0],200)
        oid=c['officialRequestId']
        (self.root/'.audio-stage-interrupted').mkdir()
        (self.root/'.audio-stage-interrupted'/'audio.wav').write_bytes(b'not published')
        def mcp(name,args):
            code,body=self.call({'jsonrpc':'2.0','id':1,'method':'tools/call',
                'params':{'name':name,'arguments':args}},base=f'http://127.0.0.1:{self.pds.server_port}/mcp',
                headers={'Accept':'application/json, text/event-stream'})
            self.assertEqual(code,200)
            return body['result']
        scope='binari/audio'
        found=mcp('npc_folders_compare',{'scope':scope,'files':[]})
        self.assertFalse(found['isError'])
        [folder]=found['structuredContent']['groups'];self.assertEqual(folder['folder'],oid)
        self.assertEqual({e['path'] for e in folder['files']},{oid+'/audio.wav',oid+'/metadata.json'})
        for e in folder['files']:
            item=mcp('npc_folder_file_chunk',{'scope':scope,'path':e['path'],'sha256':e['sha256'],'offset':0})
            self.assertFalse(item['isError'])
            self.assertEqual(base64.b64decode(item['structuredContent']['base64']),(self.root/e['path']).read_bytes())
        self.assertTrue(mcp('npc_folder_file_chunk',{'scope':scope,'path':oid+'/audio.wav','sha256':'0'*64,'offset':0})['isError'])
        self.assertTrue(mcp('npc_folder_file_chunk',{'scope':scope,'path':'../npcs/metadata.json','sha256':'0'*64,'offset':0})['isError'])
        self.assertEqual(self.neighbor.read_bytes(),b'fixture neighbor')

    def delete_plan(self, source):
        self.pds.single_target_delete=SingleTargetDelete({AUDIO_ROOT:storage_provider(self.source,self.auth)})
        oid='request.official.'+sha(('remove '+source).encode());tid='workflow.task.'+digest([oid,'audio_delete'])
        p={'schemaVersion':'ngs.audio-delete.v1','projectId':'binari','officialRequestId':source}
        i={'officialRequestId':source,'target':{'type':'directory','path':AUDIO_ROOT+'/'+source}}
        with sqlite3.connect(self.db) as db:
            db.execute("INSERT INTO workflow_runs VALUES(?,?,?,?,?,?,1,'planned',1,NULL)",(oid,'binari','audio.delete',p['schemaVersion'],encoded(p).decode(),digest(p)))
            db.execute("INSERT INTO workflow_tasks VALUES(?,?,'audio_delete','audio.delete','file',?,NULL,3,NULL,'running')",(tid,oid,encoded(i).decode()))
        return {'claim':{'officialRequestId':oid,'taskId':tid,'projectId':'binari','payloadSha256':digest(p),'claimStamp':3,'sourceRequestId':source},
                'target':{'root':AUDIO_ROOT,'path':source,'kind':'directory'}}

    def mark_result_completed(self,claim):
        with sqlite3.connect(self.db) as db:
            oid=claim['officialRequestId']
            db.execute("UPDATE workflow_tasks SET status='completed',result_ref=? WHERE task_id=?",
                       (AUDIO_ROOT+'/'+oid+'/metadata.json',claim['taskId']))

    def test_audio_generic_delete_selected_folder_and_verified_quarantine_after_loss(self):
        first=self.plan(key='first');second=self.plan(key='second')
        first_publish=self.body(first,wav());second_publish=self.body(second,wav(amplitude=1))
        self.assertEqual(self.call(first_publish)[0],200);self.assertEqual(self.call(second_publish)[0],200)
        source=self.root/first['officialRequestId'];other=self.root/second['officialRequestId']
        inode=source.stat().st_ino;two={p.name:p.read_bytes() for p in source.iterdir()}
        sibling={p.name:p.read_bytes() for p in other.iterdir()}
        wire=self.delete_plan(first['officialRequestId'])
        endpoint=f'http://127.0.0.1:{self.pds.server_port}'+DELETE
        self.assertEqual(self.call(base=endpoint)[1]['roots'],[AUDIO_ROOT])
        self.assertEqual(self.call(wire,base=endpoint)[0],403) # source is not yet completed.
        self.mark_result_completed(first)
        for bad in ({**wire,'target':{**wire['target'],'path':second['officialRequestId']}},
                    {**wire,'target':{**wire['target'],'kind':'file'}},
                    {**wire,'claim':{**wire['claim'],'claimStamp':2}},
                    {**wire,'claim':{**wire['claim'],'sourceRequestId':second['officialRequestId']}}):
            self.assertEqual(self.call(bad,base=endpoint)[0],403)
        self.assertTrue(source.exists())
        old=os.rename
        def lost(a,b,*args,**kwargs):
            result=old(a,b,*args,**kwargs)
            if b=='target':raise OSError('isolated response lost after actual folder move')
            return result
        with patch('single_target_delete.os.rename',lost):
            self.assertIn(self.call(wire,base=endpoint)[0],(409,503))
        self.assertFalse(source.exists())
        status,ack=self.call(wire,base=endpoint)
        self.assertEqual(status,200);self.assertEqual(ack['operationId'],wire['claim']['taskId'])
        self.assertTrue(ack['targetAbsent'])
        saved=self.root/'.deleted'/ack['recoveryId']/'target'
        self.assertEqual(saved.stat().st_ino,inode)
        self.assertEqual({p.name:p.read_bytes() for p in saved.iterdir()},two)
        self.assertEqual({p.name:p.read_bytes() for p in other.iterdir()},sibling)
        endpoint_mcp=f'http://127.0.0.1:{self.pds.server_port}/mcp'
        status,inventory=self.call({'jsonrpc':'2.0','id':1,'method':'tools/call',
            'params':{'name':'npc_folders_compare','arguments':{'scope':'binari/audio','files':[]}}},
            base=endpoint_mcp,headers={'Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2025-06-18'})
        self.assertEqual(status,200)
        public=json.dumps(inventory['result']['structuredContent'])
        self.assertNotIn('.deleted',public);self.assertNotIn(first['officialRequestId'],public)
        self.assertIn(second['officialRequestId'],public)

    def test_generated_at_stable_on_replay_and_legacy_metadata_stays_exact(self):
        c=self.plan();w=self.body(c,wav())
        before=time.time_ns()//1_000_000
        self.assertEqual(self.call(w)[0],200)
        path=self.root/c['officialRequestId']/'metadata.json'
        persisted=path.read_bytes();meta=json.loads(persisted)
        self.assertGreaterEqual(meta['generatedAt'],before)
        self.assertLessEqual(meta['generatedAt'],time.time_ns()//1_000_000)
        self.assertEqual(self.call(w)[0],200);self.assertEqual(path.read_bytes(),persisted)
        del meta['generatedAt'];path.write_bytes(encoded(meta))
        prior=path.read_bytes()
        self.assertEqual(self.call(w)[0],200)
        self.assertEqual(path.read_bytes(),prior) # no silent backfill during replay.

    def test_default_scope_is_not_advertised_or_writable(self):
        del self.pds.workflow_audio
        self.assertEqual(self.call()[0],404)
        http=self.service(handler(self.writer,self.token))
        self.assertEqual(self.call({'conceptId':self.section},base=f'http://127.0.0.1:{http.server_port}'+WRITER_PATH,
                         suffix='/read',headers={'Authorization':'Bearer '+self.token})[0],404)


if __name__=='__main__':
    import sys
    planner=Path(sys.argv[1]);app=Tests();app.setUp()
    try:
        st=planner.stat()
        app.pds.workflow_audio=WorkflowAudio(app.source,PrivatePlannerClaim(str(planner),st.st_dev,st.st_ino))
        print(json.dumps({'port':app.pds.server_port,'audio':str(app.root),'conceptId':app.section}),flush=True)
        for line in sys.stdin:
            if line.strip()=='close':break
    finally:app.doCleanups()
