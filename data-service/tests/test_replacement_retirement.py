import json
import socket
import threading
import unittest
from pathlib import Path
from test_npc_owned_authority import Tests as Fixture
from project_data_service.filex_npc_authority import FilexCurrentNPC
from project_data_service.npc_retirement_host_client import client
from project_data_service.filex_read import digest
import hashlib


class Tests(unittest.TestCase):
    setUp=Fixture.setUp
    tearDown=Fixture.tearDown

    def test_actual_private_reader_to_authenticated_filex_and_replay(self):
        from ownership_plan import plan
        from npc_store import encoded
        owner='npc';rid='replacement.delete'
        docs={'metadata.json': self.writer.snapshot()['documents']['npc/metadata.json'],
              'planning/art.json': self.writer.snapshot()['documents']['npc/planning/art.json']}
        self.writer.write(self.writer.safe('npc/.owned-assets.json'),encoded(plan(owner,docs)))
        source=self.writer.snapshot()['sourceVersion']
        content={'schemaVersion':'npc-replacement-expired-leases.v1','npcId':owner,'generationId':self.generation,
                 'requestId':rid,'expectedSourceVersion':source,'snapshotHash':'a'*64,
                 'parents':[{'parentRequestId':self.guard['requestId'],'guardHash':digest(self.guard),
                             'leaseTokenHash':hashlib.sha256(self.guard['leaseToken'].encode()).hexdigest(),
                             'stageRequestIds':['child.request'],'publicationRequestIds':['child.request'],
                             'canonicalCommitRequestIds':['child.request',self.guard['requestId']],
                             'artifactReservationIds':['asset.'+hashlib.sha256(b'binari:child.request').hexdigest()[:48]],
                             'timeoutProofHashes':['b'*64],'childScopeHashes':['c'*64]}]}
        content['proofHash']=digest(content)
        # macOS has a short Unix path length; reserve an isolated private folder.
        import tempfile
        with tempfile.TemporaryDirectory(prefix='pr-',dir='/tmp') as folder, socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
            path=Path(folder).resolve()/'host.sock';listener.bind(str(path));path.chmod(0o600);listener.listen();listener.settimeout(3)
            seen=[]
            def respond():
                try:
                    conn,_=listener.accept()
                    with conn,conn.makefile('rb') as stream:
                        seen.append(json.loads(stream.readline()))
                        conn.sendall(json.dumps({'result':content}).encode()+b'\n')
                except Exception as exc:seen.append(exc)
            thread=threading.Thread(target=respond);thread.start()
            try:
                with self.assertRaisesRegex(ValueError,'NOT_IDLE'):
                    self.source.delete_character(rid,'input',owner,1)
                self.source=FilexCurrentNPC(self.source.store,replacement_reader=client(path,replacement=True))
                result=self.source.delete_character(rid,'input',owner,1)
                self.assertEqual(result,self.source.delete_character(rid,'input',owner,1))
            finally:
                thread.join(4)
            self.assertEqual(seen,[{'operation':'replacement-preflight','npcId':owner,'generationId':self.generation,
                                    'requestId':rid,'expectedSourceVersion':source}])
        self.assertTrue(result['deletionReceipt']['backupVerified'])
        self.assertEqual(result['deletionReceipt']['externalDataDisposition'],'preserved-uninspected')
        self.assertEqual(self.writer.generation(owner)['state'],'deleted')
        self.assertNotIn('retiredLeases',result['deletionReceipt'])
        retirement=result['deletionReceipt']['replacementRetirement']
        self.assertEqual(retirement['proofHash'],content['proofHash'])
        self.assertEqual(retirement['parents'],[{'parentRequestId':self.guard['requestId'],'guardHash':digest(self.guard)}])
        self.assertNotIn(self.guard['leaseToken'],json.dumps(retirement))
        self.assertEqual(self.writer.state(owner)['replacementRetirements'][self.generation]['proof'],content)
