from contextlib import contextmanager
import json
import os
from pathlib import Path
from http.server import ThreadingHTTPServer
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request,urlopen

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'data-service'))
from server import Store,handler
from single_target_delete import SingleTargetDelete,put_record
from project_data_service.single_target_delete import (AuthorizedStorage, ClaimDenied, PATH, SCHEMA,
    SingleTargetDelete as PDS, filex_execute, filex_ready)
from project_data_service.filex_cas_transport import FilexCASTransport
from project_data_service.http_server import Handler

ROOT='fixture/storage'

class Tests(unittest.TestCase):
    def service(self,h):
        s=ThreadingHTTPServer(('127.0.0.1',0),h)
        t=threading.Thread(target=lambda:s.serve_forever(poll_interval=.02),daemon=True);t.start()
        self.addCleanup(t.join);self.addCleanup(s.server_close);self.addCleanup(s.shutdown)
        return s
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        p=Path(tmp.name).resolve();self.root=p/'root';self.root.mkdir()
        token=p/'token';token.write_text('f'*32);token.chmod(0o600)
        actor=Store(self.root);self.writer=SingleTargetDelete(actor,ROOT)
        self.fx=self.service(handler(actor,'f'*32,single_target_delete=self.writer))
        cfg=dict(schemaVersion='pds-filex-cas.v1',projectId='binari',enabled=True,
                 endpoint=f'http://127.0.0.1:{self.fx.server_port}',tokenFile=str(token))
        self.factory=lambda:FilexCASTransport(cfg)
        self.pds=self.service(Handler);self.approved={}
        @contextmanager
        def authorize(claim):
            if set(claim)!={'taskId'} or type(claim['taskId'])is not str or claim['taskId'] not in self.approved:
                raise ClaimDenied()
            yield {'operationId':claim['taskId'],'target':self.approved[claim['taskId']]}
        self.pds.single_target_delete=PDS({ROOT:AuthorizedStorage(authorize,
            lambda x:filex_execute(self.factory,x),lambda:filex_ready(self.factory))})
    def req(self,value=None,*,headers=None,port=None):
        r=Request(f'http://127.0.0.1:{port or self.pds.server_port}'+PATH,
            data=None if value is None else json.dumps(value).encode(),headers=headers or {'Content-Type':'application/json'})
        try:
            with urlopen(r,timeout=5) as res:return res.status,json.load(res)
        except HTTPError as e:
            with e:return e.code,json.load(e)
    def add(self,task,path,kind):
        dest={'root':ROOT,'path':path,'kind':kind}
        self.approved[task]=dest
        return {'claim':{'taskId':task},'target':dict(dest)}
    def test_real_http_moves_one_directory_then_one_file_only(self):
        datum=self.root/'npcs/target';datum.mkdir(parents=True);(datum/'a').write_bytes(b'raw')
        (datum/'b').mkdir();(datum/'b/c').write_bytes(b'unparsed binary\0')
        peer=self.root/'npcs/other';peer.write_bytes(b'peer')
        self.assertEqual(self.req(),(200,{'enabled':True,'schemaVersion':SCHEMA,'roots':[ROOT]}))
        pkt=self.add('task.dir','npcs/target','directory')
        code,receipt=self.req(pkt);self.assertEqual(code,200)
        self.assertEqual((receipt['ok'],receipt['operationId'],receipt['targetAbsent']),(True,'task.dir',True))
        q=self.root/'.storage-quarantine'/receipt['recoveryId']
        self.assertEqual((q/'target/b/c').read_bytes(),b'unparsed binary\0')
        self.assertEqual((q/'target/a').read_bytes(),b'raw')
        self.assertFalse(datum.exists());self.assertEqual(peer.read_bytes(),b'peer')
        self.assertEqual(self.req(pkt),(200,receipt))
        self.assertEqual(self.req(self.add('task.file','npcs/other','file'))[0],200)
        self.assertFalse(peer.exists())
    def test_scope_array_traversal_kind_and_symlink_rejected_without_move(self):
        p=self.root/'live';p.mkdir();f=p/'leaf';f.write_bytes(b'unchanged')
        pkt=self.add('task.safe','live/leaf','file')
        for bad in ([pkt],{**pkt,'targets':[pkt['target']]},{**pkt,'target':{**pkt['target'],'path':'/live/leaf'}},
                    {**pkt,'target':{**pkt['target'],'path':'live/../leaf'}},
                    {**pkt,'target':{**pkt['target'],'path':'live//leaf'}},
                    {**pkt,'target':{**pkt['target'],'path':'live/%2e%2e/leaf'}},
                    {**pkt,'target':{**pkt['target'],'kind':'directory'}},
                    {**pkt,'target':{**pkt['target'],'root':'neighbor/storage'}}):
            self.assertIn(self.req(bad)[0],(403,409))
        # Even if the caller had authorized an incompatible kind or link, writer refuses.
        self.assertEqual(self.req(self.add('task.kind','live/leaf','directory'))[0],409)
        (self.root/'link').symlink_to(p)
        self.assertIn(self.req(self.add('task.link','link/leaf','file'))[0],(409,503))
        (p/'indirect').symlink_to(f)
        self.assertEqual(self.req(self.add('task.leaf-link','live/indirect','file'))[0],409)
        self.assertEqual(f.read_bytes(),b'unchanged')
        self.assertFalse((self.root/'.storage-quarantine').exists())
    def test_lost_ack_reconciles_without_redeleting_recreated_or_restored_target(self):
        source=self.root/'a';source.write_text('value')
        pkt=self.add('task.loss','a','file')
        def crash(fd,record):
            if record['state']=='moved':raise OSError('fixture acknowledgment lost')
            return put_record(fd,record)
        with patch('single_target_delete.put_record',side_effect=crash):
            self.assertEqual(self.req(pkt)[0],409)
        self.assertFalse(source.exists())
        code,out=self.req(pkt);self.assertEqual(code,200);self.assertEqual(self.req(pkt),(200,out))
        recovered=self.root/'.storage-quarantine'/out['recoveryId']/'target'
        source.write_text('new')
        self.assertEqual(self.req(pkt)[0],409)
        self.assertEqual(source.read_text(),'new');self.assertEqual(recovered.read_text(),'value')
        source.unlink();os.rename(recovered,source)
        self.assertEqual(self.req(pkt)[0],409)
        self.assertEqual(source.read_text(),'value')
    def test_disabled_writer_not_activated_and_wrong_token_not_authorized(self):
        self.assertEqual(self.req({'operationId':'task','target':{'root':ROOT,'path':'leaf','kind':'file'}},
            port=self.fx.server_port,headers={'Content-Type':'application/json','Authorization':'Bearer '+'0'*32})[0],401)
        default=self.service(handler(Store(self.root),'f'*32))
        self.assertEqual(self.req({'operationId':'task','target':{'root':ROOT,'path':'leaf','kind':'file'}},
            port=default.server_port,headers={'Content-Type':'application/json','Authorization':'Bearer '+'f'*32})[0],404)
