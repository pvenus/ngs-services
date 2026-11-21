import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'data-service'))
from project_data_service.filex_npc_files import encode
from project_data_service.filex_npc_store import FilexNPCStore
from project_data_service.filex_npc_authority import FilexCurrentNPC
from project_data_service.pds_owned_transport import OwnedNPCTransport
from project_data_service.http_server import Handler as PDSHandler
from project_data_service.workflow_delete import WorkflowDelete, PATH
from fenced_store import FencedOwnedNPCStore
from npc_store import encoded, digest
from server import handler, Conflict
from ownership_plan import plan as owner_plan

class Tests(unittest.TestCase):
    def start(self, h):
        server=ThreadingHTTPServer(('127.0.0.1',0),h)
        t=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);t.start()
        self.addCleanup(server.server_close);self.addCleanup(t.join);self.addCleanup(server.shutdown)
        return server,'http://127.0.0.1:'+str(server.server_port)

    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        d=Path(t.name).resolve(); root=d/'files';root.mkdir()
        doc={'projectId':'ProjectBS','characters':[{'id':'npc.test'},{'id':'npc.other'}]}
        files=encode(doc); files['metadata.json']['_pdsCurrent']={'revision':1,'documentHash':digest(doc)}
        for n,v in files.items():
            p=root/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(encoded(v))
        for name in ('npc.test','npc.other'):
            (root/name/'.owned-assets.json').write_bytes(encoded(owner_plan(name,{'metadata.json':files[name+'/metadata.json']})))
        (root/'npc.test/nested/empty').mkdir(parents=True)
        self.writer=FencedOwnedNPCStore(root); self.original_token='a'*32
        self.g=self.writer.bootstrap('npc.test',self.writer.snapshot()['sourceVersion'])['generationId']
        self.other=self.writer.bootstrap('npc.other',self.writer.snapshot()['sourceVersion'])['generationId']
        self.legacy=self.writer.admit('npc.test',self.g,'old.lane')
        self.other_guard=self.writer.admit('npc.other',self.other,'unrelated')
        writer,url=self.start(handler(self.writer,self.original_token,workflow_delete=True));self.writer_url=url
        token=d/'writer-token';token.write_text(self.original_token);token.chmod(0o600)
        cfg={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','scope':'npc','enabled':True,
             'endpoint':url,'tokenFile':str(token)}
        pds,self.base=self.start(PDSHandler);self.pds=pds;self.running=True
        self.source=FilexCurrentNPC(FilexNPCStore(lambda:OwnedNPCTransport(cfg)))
        self.b={'officialRequestId':'official.1','taskId':'workflow.delete.1','projectId':'binari','npcId':'npc.test',
                'generationId':self.g,'expectedSourceVersion':self.writer.snapshot()['sourceVersion'],
                'expectedRevision':1,'payloadSha256':'c'*64}
        self.claim={'fixtureClaim':'recognized'}
        self.verifications=[]
        def verify(claim):
            self.verifications.append(claim)
            return dict(self.b) if self.running and claim==self.claim else None
        self.pds.workflow_delete=WorkflowDelete(self.source,verify)

    def post(self, body=None, path=PATH, headers=None, base=None):
        if body is None:body={'binding':self.b,'claim':self.claim}
        q=Request((base or self.base)+path,data=encoded(body),
                  headers={'Content-Type':'application/json',**(headers or {})})
        try:r=urlopen(q,timeout=3)
        except HTTPError as e:r=e
        with r:return r.code,json.load(r)

    def test_exact_http_commit_without_checking_legacy_lease_then_replay(self):
        fields={k:self.b[k] for k in ('officialRequestId','taskId','projectId','npcId','payloadSha256')}
        expected={'schemaVersion':'workflow.pds-delete-receipt.v1',**fields,'operation':'npc.delete-folder',
                  'status':'committed','verified':True,'targetAbsent':True,'receiptId':self.b['taskId']}
        self.assertEqual(self.post(),(200,expected))
        self.assertEqual(self.post(),(200,expected))
        s=self.writer.state('npc.test')
        self.assertEqual((s['state'],s['leases']),('deleted',{})); self.assertIn('old.lane',s['retiredRequestIds'])
        self.assertNotIn(self.legacy['leaseToken'],json.dumps(s))
        self.assertEqual(self.writer.generation('npc.other')['activeLeases'],1)
        self.assertFalse((self.writer.root/'npc.test').exists())
        self.assertFalse(self.writer.owner_files('npc.test')); self.assertTrue(self.writer.owner_files('npc.other'))
        with self.assertRaises(Conflict): self.writer.put_asset('b'*64+'.png',b'x',self.legacy)
        with self.assertRaises(Conflict): self.writer.admit('npc.test',self.g,'old.late')
        r=self.writer.receipt(self.b['taskId']); self.assertTrue(r['backupVerified'])
        self.assertEqual(self.writer.verified_restorable_backup(r['backupId'])[0]['characterId'],'npc.test')
        # A later generation is not absent just because an old receipt exists.
        self.writer.prepare_create('npc.test','future',self.writer.snapshot()['sourceVersion'])
        self.assertEqual(self.post()[0],409)

    def test_invalid_claim_scope_stale_source_or_unconfigured_never_writes(self):
        self.running=False
        self.assertEqual(self.post()[0],403)
        self.running=True
        self.assertEqual(self.post({'binding':{**self.b,'npcId':'npc.other'},'claim':self.claim})[0],403)
        self.assertEqual(self.post(headers={'Origin':'http://127.0.0.1'})[0],403)
        self.b['expectedSourceVersion']='c'*64
        self.assertEqual(self.post()[0],409)
        self.b['expectedSourceVersion']=self.writer.snapshot()['sourceVersion']
        self.b['generationId']='d'*48
        self.assertEqual(self.post()[0],409)
        del self.pds.workflow_delete
        self.assertEqual(self.post()[0],404)
        self.assertFalse(self.writer.receipt_path(self.b['taskId']).exists())
        self.assertEqual(self.writer.generation('npc.test')['activeLeases'],1)

    def test_uncertain_backup_never_removes_source_or_fence(self):
        from unittest.mock import patch
        with patch.object(self.writer,'stage_owner_backup',side_effect=Conflict('fixture backup failure')):
            self.assertEqual(self.post()[0],409)
        self.assertTrue(self.writer.owner_files('npc.test'))
        self.assertEqual(self.writer.generation('npc.test')['state'],'active')
        self.assertEqual(self.writer.generation('npc.test')['activeLeases'],1)
        self.assertFalse(self.writer.receipt_path(self.b['taskId']).exists())

    def test_replay_never_unlinks_unexpected_physical_content(self):
        self.assertEqual(self.post()[0],200)
        p=self.writer.root/'npc.test'/'unexpected.bin'
        p.parent.mkdir()
        p.write_bytes(b'preserve')
        self.assertEqual(self.post()[0],409)
        self.assertEqual(p.read_bytes(),b'preserve')

    def test_writer_auth_default_off_and_legacy_delete_unmodified(self):
        _,off=self.start(handler(self.writer,self.original_token))
        self.assertEqual(self.post({},path='/v1/workflow/delete-character',base=self.writer_url)[0],401)
        self.assertEqual(self.post({},path='/v1/workflow/delete-character',base=off,
            headers={'Authorization':'Bearer '+self.original_token})[0],404)
        with self.assertRaises(Conflict):
            self.writer.prepare_delete('npc.test',self.g,'generic',deletion_scope='canonical-owner-generation.v1')
        self.assertEqual(self.writer.generation('npc.test')['activeLeases'],1)
