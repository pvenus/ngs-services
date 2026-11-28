"""Manual WAV registration travels through both authenticated loopback services."""
import json
import sqlite3

from test_workflow_audio_http import Tests as AudioTests, audio, wav
from project_data_service.manual_audio_import import PATH, configured


class ManualAudioTests(AudioTests):
    def test_manual_import_exact_bytes_and_provenance(self):
        claim=self.plan()
        base=wav()
        self.assertEqual(self.call(self.body(claim,base))[0],200)
        token='X'*48
        secret=self.data.parent/'owner-manual-token'
        secret.write_text(token)
        secret.chmod(0o600)
        endpoint=f'http://127.0.0.1:{self.pds.server_port}'+PATH
        with sqlite3.connect(self.db) as db:
            before=db.execute('SELECT * FROM workflow_tasks').fetchall()
        manual=wav(27,48000,2,1)
        wire={'schemaVersion':'ngs.audio-import.v1','projectId':'binari',
              'title':'비나리 · 타이틀 BGM (27초 루프)',
              'source':{'sourceOfficialRequestId':claim['officialRequestId'],
                        'sourceSha256':audio(base)['sha256']},'artifact':audio(manual)}
        self.assertEqual(self.call({},base=endpoint)[0],404)
        configured(self.pds,'127.0.0.1',secret,self.source.factory)
        self.assertEqual(self.call({},base=endpoint)[0],401)
        headers={'Authorization':'Bearer '+token}
        self.assertEqual(self.call({},base=endpoint,headers={**headers,'Origin':'http://127.0.0.1'})[0],403)
        forged={**wire,'source':{**wire['source'],'sourceSha256':'0'*64}}
        self.assertEqual(self.call(forged,base=endpoint,headers=headers)[0],422)
        corrupted={**wire,'artifact':{**wire['artifact'],'sha256':'0'*64}}
        self.assertEqual(self.call(corrupted,base=endpoint,headers=headers)[0],422)
        status,ack=self.call(wire,base=endpoint,headers=headers)
        self.assertEqual(status,200,ack)
        self.assertEqual(self.call(wire,base=endpoint,headers=headers),(status,ack))
        self.assertEqual(len(list(self.root.glob('audio.import.*'))),1)
        result=self.root/ack['id']
        self.assertEqual((result/'audio.wav').read_bytes(),manual)
        meta=json.loads((result/'metadata.json').read_bytes())
        self.assertEqual(meta['schemaVersion'],'ngs.audio-import-result.v1')
        self.assertEqual(meta['audio']['durationSeconds'],27)
        self.assertEqual(meta['provenance'],{'mode':'owner-manual','source':wire['source']})
        self.assertEqual((self.root/claim['officialRequestId']/'audio.wav').read_bytes(),base)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT * FROM workflow_tasks').fetchall(),before)
