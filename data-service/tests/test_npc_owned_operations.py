import copy,hashlib,io,sys,tempfile,threading,unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from project_data_service.filex_npc_files import PREFIX
from project_data_service.filex_read import digest
from project_data_service.npc_owned_operations import OwnedNPCOperations
from project_data_service.pds_owned_transport import OwnedNPCTransport


class Tests(unittest.TestCase):
 def setUp(self):
  source=str(Path(__file__).resolve().parents[2]/'filex-writer')
  sys.path.insert(0,source);self.addCleanup(sys.path.remove,source)
  from fenced_store import FencedOwnedNPCStore
  from npc_store import encoded
  from server import handler
  tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
  root=Path(tmp.name).resolve()/'store';root.mkdir()
  (root/'metadata.json').write_bytes(encoded({'characterOrder':['npc','other']}))
  for owner in ('npc','other'):
   (root/owner).mkdir()
   (root/owner/'metadata.json').write_bytes(encoded({'id':owner}))
  self.store=FencedOwnedNPCStore(root)
  self.gen=self.store.bootstrap('npc',self.store.snapshot()['sourceVersion'])['generationId']
  self.other_gen=self.store.bootstrap('other',self.store.snapshot()['sourceVersion'])['generationId']
  self.original=self.store.admit('npc',self.gen,'parent.one')
  self.other=self.store.admit('other',self.other_gen,'parent.other')
  token=root.parent/'fixture-token';token.write_text('d'*32);token.chmod(0o600)
  server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.store,'d'*32))
  t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
  def cleanup():
   server.shutdown();server.server_close();t.join()
  self.addCleanup(cleanup)
  self.config=dict(schemaVersion='pds-filex-cas.v1',projectId='binari',scope='npc',enabled=True,ownershipMode='npc-owned-v1',
                   endpoint='http://127.0.0.1:'+str(server.server_port),tokenFile=str(token))
  self.key=PREFIX+'npc/planning/fixture.json'
  self.op=self.bind()

 def bind(self,guard=None,stage='stage.one'):
  return OwnedNPCOperations(lambda:OwnedNPCTransport(self.config),guard=self.original if guard is None else guard,stage_request_id=stage)

 def commit(self,op=None,request='stage.one',key=None,body=None,expected=None):
  key=self.key if key is None else key
  body={'content':'body'}if body is None else body
  return (self.op if op is None else op).commit(request,digest(body),
        [{'path':key,'body':body,'expectedHash':expected}],{key:expected})

 def test_commit_scoped_receipt_replay_and_cas(self):
  self.assertIsNone(self.op.receipt('stage.one'))
  r=self.commit()
  self.assertEqual(r['hashes'],{self.key:digest({'content':'body'})})
  self.assertEqual(self.commit(),r)
  with patch.object(OwnedNPCTransport,'receipt',side_effect=AssertionError('unscoped receipt forbidden')):
   self.assertEqual(self.op.receipt('stage.one'),r)
  self.assertEqual(self.store.snapshot()['documents']['npc/planning/fixture.json'],{'content':'body'})
  with self.assertRaises(ValueError):self.commit(body={'different':True})
  with self.assertRaises(ValueError):
   self.commit(self.bind(stage='stage.two'),request='stage.two',body={'different':True},expected='0'*64)
  self.assertEqual(self.op.receipt('stage.one'),r)

 def test_malformed_guard_other_scope_path_or_stage_no_writer_calls(self):
  for guard in ({**self.original,'npcId':'..'},{**self.original,'generationId':'nothex'},
                {**self.original,'leaseToken':'0'*63},{**self.original,'stageRequestId':'injected'},
                {k:v for k,v in self.original.items()if k!='leaseToken'}):
   with self.subTest(guard_fields=tuple(guard)),self.assertRaisesRegex(ValueError,'OPERATION_GUARD'):self.bind(guard=guard)
  with patch.object(OwnedNPCTransport,'call',side_effect=AssertionError('must deny before writer')):
   with self.assertRaisesRegex(ValueError,'OPERATION_STAGE'):self.commit(request='other.stage')
   with self.assertRaisesRegex(ValueError,'OPERATION_STAGE'):self.op.receipt('other.stage')
   for path in (PREFIX+'other/planning/fixture.json',PREFIX+'metadata.json',PREFIX+'npc/../other/planning/fixture.json',
                PREFIX+'npc-bogus/planning/fixture.json',PREFIX+'npc/planning/fixture.json/extra'):
    with self.subTest(path=path),self.assertRaisesRegex(ValueError,'OPERATION_SCOPE'):self.commit(key=path)
   with self.assertRaisesRegex(ValueError,'OPERATION_SCOPE'):
    self.op.commit('stage.one',digest({}),[{'path':self.key,'body':{},'expectedHash':None}],
                   {self.key:None,PREFIX+'other/metadata.json':None})

 def test_missing_asset_aborts_multi_file_batch_and_real_upload_unblocks(self):
  from PIL import Image
  with io.BytesIO()as o:
   Image.new('RGBA',(4,3),'red').save(o,'PNG');data=o.getvalue()
  sha=hashlib.sha256(data).hexdigest();asset=sha+'.png'
  key=PREFIX+'npc/animation-sequences/idle/walk/n.json'
  raw={'schemaVersion':'ngs.animation-native-png-sequence.v1','frameCount':2,
       'frames':[{'index':i,'assetId':asset,'sha256':sha,'mimeType':'image/png'} for i in range(2)]}
  files=[dict(path=self.key,body={'draft':'preserve'},expectedHash=None),dict(path=key,body=raw,expectedHash=None)]
  def commit():return self.op.commit('stage.one',digest(raw),files,{self.key:None,key:None})
  with self.assertRaises(ValueError):commit()
  self.assertIsNone(self.op.receipt('stage.one'))
  self.assertNotIn('npc/planning/fixture.json',self.store.snapshot()['documents'])
  with self.assertRaisesRegex(ValueError,'OPERATION_ASSET'):self.op.upload_asset(asset,b'other')
  self.assertEqual(self.op.upload_asset(asset,data)['sha256'],sha)
  result=commit()
  self.assertEqual(set(result['hashes']),{self.key,key})
  self.assertEqual(self.op.receipt('stage.one'),result)

 def test_past_finished_is_readable_and_unwritable(self):
  r=self.commit()
  with OwnedNPCTransport(self.config,self.original)as transport:self.assertTrue(transport.finish()['released'])
  self.assertEqual(self.op.receipt('stage.one'),r)
  with self.assertRaisesRegex(ValueError,'KNOWLEDGE_SOURCE_CHANGED'):self.commit()
  with self.assertRaisesRegex(ValueError,'KNOWLEDGE_SOURCE_CHANGED'):
   self.op.upload_asset(hashlib.sha256(b'raw').hexdigest()+'.png',b'raw')
  other=self.bind(self.other,stage='stage.one')
  self.assertIsNone(other.receipt('stage.one'))

 def test_receipt_rejects_injected_cross_owner_stage_or_unverified(self):
  self.commit()
  with OwnedNPCTransport(self.config)as transport:
   raw=transport.publication_receipts('npc',self.gen,'parent.one')
  for field,new in (('stageRequestId','stage.other'),('npcId','other'),('verified',False),('status','prepared'),
                    ('hashes',{PREFIX+'other/planning/fixture.json':'a'*64})):
   fake=copy.deepcopy(raw);fake['receipts'][0][field]=new
   with self.subTest(field=field),patch.object(OwnedNPCTransport,'publication_receipts',return_value=fake),self.assertRaisesRegex(ValueError,'OPERATION_(?:RECEIPT|SCOPE)'):
    self.op.receipt('stage.one')
  fake=copy.deepcopy(raw);fake['receipts']+=fake['receipts']
  with patch.object(OwnedNPCTransport,'publication_receipts',return_value=fake),self.assertRaisesRegex(ValueError,'OPERATION_RECEIPT'):
   self.op.receipt('stage.one')

 def test_storage_interruption_before_receipt_rolls_forward_under_existing_lock(self):
  original=self.store.write
  receipt_path=self.store.receipt_path('stage.one')
  def fail_at_receipt(path,data):
   if path==receipt_path:raise OSError('isolated interrupted receipt write')
   return original(path,data)
  with patch.object(self.store,'write',side_effect=fail_at_receipt):
   with self.assertRaisesRegex(ValueError,'FILEX_CAS_HTTP_503'):self.commit()
   self.assertFalse(receipt_path.is_file())
   self.assertTrue((self.store.root/'.owned-tx.json').is_file())
  recovered=self.op.receipt('stage.one')
  self.assertEqual(recovered['hashes'],{self.key:digest({'content':'body'})})
  self.assertTrue(receipt_path.is_file())
  self.assertFalse((self.store.root/'.owned-tx.json').exists())
  self.assertEqual(self.commit(),recovered)
