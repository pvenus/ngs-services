"""Actual PDS + authenticated Filex, temporary roots only; external scratch untouched."""
import copy, json, threading, unittest, urllib.request, urllib.error, http.client
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from test_npc_owned_authority import Tests as Fixture
from test_npc_external_inventory import ExternalInventory
from project_data_service.http_server import Handler
from project_data_service.domain_contracts import legacy_http
from project_data_service.npc_ownership import make_transport, request_scope

class Tests(unittest.TestCase):
 setUp=Fixture.setUp
 tearDown=Fixture.tearDown
 headers=Fixture.headers
 inventory=ExternalInventory.inventory
 def start(self):
  scratch=self.inventory()
  class Forbidden:
   def prepare(self,*a,**k):raise AssertionError('external inventory called')
   def cleanup(self,*a,**k):raise AssertionError('external cleanup called')
  self.source.external_inventory=Forbidden()
  self.pds=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.pds.npc_store=self.source
  self.pds.app=SimpleNamespace(domains=SimpleNamespace(npcs=self.source,dispatch=lambda h:legacy_http.Handler.route(h)))
  t=threading.Thread(target=self.pds.serve_forever,daemon=True);t.start()
  self.addCleanup(lambda:(self.pds.shutdown(),self.pds.server_close(),t.join()))
  return scratch
 def post(self,path,p,guard=None):
  import base64
  headers={'Content-Type':'application/json'}
  if guard:headers['X-NPC-Ownership-Guard']=base64.b64encode(json.dumps(guard).encode()).decode()
  q=urllib.request.Request('http://127.0.0.1:'+str(self.pds.server_port)+path,data=json.dumps(p).encode(),headers=headers)
  with urllib.request.urlopen(q,timeout=15)as r:return json.load(r)
 def delete(self,rid='delete.exact'):
  return self.post('/api/npcs/delete-character',{'requestId':rid,'inputHash':'original','characterId':'npc','expectedRevision':2})
 def test_staged_same_child_backup_replay_and_restore(self):
  scratch=self.start();before=self.writer.owner_files('npc');snap=self.writer.snapshot()
  staged=self.writer.stage_delete('npc',self.generation,'delete.exact',snap['sourceVersion'])
  r=self.delete();d=r['deletionReceipt']
  self.assertEqual(d['backupId'],staged['backupId']);self.assertEqual(d['backupManifestHash'],staged['backupManifestHash'])
  self.assertEqual(d['deletionScope'],'canonical-owner-generation.v1');self.assertEqual(d['externalDataDisposition'],'preserved-uninspected')
  self.assertNotIn('externalOwnerManifestHash',d);self.assertNotIn('externalCleanup',r)
  self.assertEqual(self.delete(),r);self.assertEqual((scratch/'frame.png').read_bytes(),b'frame')
  with make_transport(self.config)as t:self.assertEqual(t.get_deletion_receipt('delete.exact'),d)
  c=self.source.current();g=self.post('/v1/npcs/ownership/prepare-restore',{'npcId':'npc','requestId':'restore.exact','backupId':d['backupId'],'expectedSourceVersion':c['sourceVersion']})
  rr=self.post('/v1/npcs/ownership/restore',{'requestId':'restore.exact','inputHash':'restore','backupId':d['backupId'],'expectedRevision':c['revision']},g)
  self.assertTrue(rr['restorationReceipt']['backupVerified']);self.assertEqual(self.writer.owner_files('npc'),before)
 def test_lost_response_replays_immutable_receipt(self):
  self.start();original=self.writer.commit_owned
  def lost(*a,**k):
   original(*a,**k);raise RuntimeError('simulated response loss')
  self.writer.commit_owned=lost
  with self.assertRaises((urllib.error.HTTPError,http.client.RemoteDisconnected)):self.delete()
  self.writer.commit_owned=original
  self.assertEqual(self.delete()['deletionReceipt'],self.writer.receipt('delete.exact'))
 def test_other_child_and_generation_cannot_take_stage(self):
  self.start();s=self.writer.snapshot();self.writer.stage_delete('npc',self.generation,'delete.exact',s['sourceVersion'])
  with self.assertRaises(urllib.error.HTTPError):self.delete('different')
  with self.assertRaises(Exception):self.writer.prepare_delete('npc','wrong','delete.exact',deletion_scope='canonical-owner-generation.v1')
  self.assertEqual(self.writer.generation('npc')['state'],'delete-staged')
 def test_target_late_admission_and_write_denied(self):
  self.start();s=self.writer.snapshot();self.writer.stage_delete('npc',self.generation,'delete.exact',s['sourceVersion'])
  with make_transport(self.config)as t:
   with self.assertRaises(ValueError):t.admit('npc',self.generation,'late')
  with request_scope(self.headers()):
   with self.assertRaises(ValueError):self.source.save(2,'late',self.doc)
  self.assertEqual(self.writer.snapshot()['sourceVersion'],s['sourceVersion'])
 def test_active_lease_and_cleanup_block(self):
  self.start()
  with make_transport(self.config)as t:g=t.admit('npc',self.generation,'busy')
  with self.assertRaises(urllib.error.HTTPError):self.delete()
  self.assertEqual(self.writer.generation('npc')['state'],'active')
 def test_legacy_prepared_cannot_be_reinterpreted(self):
  self.start();s=self.writer.snapshot();self.writer.stage_delete('npc',self.generation,'delete.exact',s['sourceVersion'])
  self.writer.prepare_delete('npc',self.generation,'delete.exact','a'*64)
  before=self.writer.state('npc')
  with self.assertRaises(urllib.error.HTTPError):self.delete()
  self.assertEqual(self.writer.state('npc'),before)
 def test_mixed_or_unknown_scope_rejected(self):
  self.start()
  with make_transport(self.config)as t:
   p={'npcId':'npc','generationId':self.generation,'requestId':'delete.exact','deletionScope':'canonical-owner-generation.v1','externalOwnerManifestHash':'a'*64}
   with self.assertRaises(ValueError):t.call('POST','/v1/generation/prepare-delete',p)
   p.pop('externalOwnerManifestHash');p['deletionScope']='unknown'
   with self.assertRaises(ValueError):t.call('POST','/v1/generation/prepare-delete',p)

 def sibling(self):
  import base64
  c=self.source.current()
  with make_transport(self.config)as t:g=t.prepare_create('sibling','sibling.create',c['sourceVersion'])
  doc=copy.deepcopy(c['document']);doc['characters'].append({'id':'sibling','name':'other','planningCategories':[]})
  h={'X-NPC-Ownership-Guard':base64.b64encode(json.dumps(g).encode()).decode()}
  with request_scope(h):self.source.save(c['revision'],'create sibling',doc)
  return self.writer.generation('sibling')['generationId']
 def test_sibling_admission_is_independent_of_target_fence(self):
  self.start();sg=self.sibling();source=self.writer.snapshot()['sourceVersion']
  self.writer.stage_delete('npc',self.generation,'delete.exact',source)
  with make_transport(self.config)as t:g=t.admit('sibling',sg,'sibling.worker')
  self.assertEqual(g['npcId'],'sibling');self.assertEqual(self.writer.generation('npc')['state'],'delete-staged')
 def test_sibling_publication_allowed_but_source_cas_conflicts(self):
  import base64
  self.start();sg=self.sibling();source=self.writer.snapshot()['sourceVersion']
  stage=self.writer.stage_delete('npc',self.generation,'delete.exact',source)
  before=self.writer.safe('.deleted/'+stage['backupId']+'/manifest.json').read_bytes()
  with make_transport(self.config)as t:g=t.admit('sibling',sg,'sibling.worker')
  c=self.source.current();doc=copy.deepcopy(c['document']);doc['characters'][1]['name']='changed sibling'
  h={'X-NPC-Ownership-Guard':base64.b64encode(json.dumps(g).encode()).decode()}
  with request_scope(h):self.source.save(c['revision'],'sibling publish',doc)
  current=self.source.current()
  with self.assertRaises(urllib.error.HTTPError):
   self.post('/api/npcs/delete-character',{'requestId':'delete.exact','inputHash':'original','characterId':'npc','expectedRevision':current['revision']})
  self.assertEqual(self.writer.safe('.deleted/'+stage['backupId']+'/manifest.json').read_bytes(),before)
  self.assertEqual(self.writer.generation('npc')['state'],'delete-staged')
 def test_corrupt_backup_and_unfinished_cleanup_fail_closed(self):
  from npc_store import encoded
  self.start();s=self.writer.snapshot();staged=self.writer.stage_delete('npc',self.generation,'delete.exact',s['sourceVersion'])
  self.writer.safe('.deleted/'+staged['backupId']+'/files/npc/metadata.json').write_bytes(b'corrupt')
  with self.assertRaises(urllib.error.HTTPError):self.delete()
  state=self.writer.state('npc');state['cleanupReservations']={'pending':{}}
  self.writer.write(self.writer.safe(self.writer.state_path('npc')),encoded(state))
  with self.assertRaisesRegex(Exception,'cleanup reserved'):
   self.writer.prepare_delete('npc',self.generation,'delete.exact',deletion_scope='canonical-owner-generation.v1')
 def test_receipt_reader_legacy_and_canonical_are_distinct(self):
  from project_data_service.pds_owned_transport import OwnedNPCTransport
  from unittest.mock import patch
  self.start();r=self.delete()['deletionReceipt']
  with make_transport(self.config)as t:
   legacy={k:v for k,v in r.items()if k not in ('deletionScope','externalDataDisposition')};legacy['externalOwnerManifestHash']='a'*64
   for value in (r,legacy):
    with patch.object(OwnedNPCTransport,'receipt',return_value=value):self.assertEqual(t.get_deletion_receipt('delete.exact'),value)
   for change in ({'requestId':'different'},{'deletionScope':'unknown'},{'externalDataDisposition':'deleted'},{'externalOwnerManifestHash':'a'*64}):
    with patch.object(OwnedNPCTransport,'receipt',return_value={**r,**change}):
     with self.assertRaisesRegex(ValueError,'UNVERIFIED'):t.get_deletion_receipt('delete.exact')
 def test_replay_other_input_and_late_write_rejected(self):
  self.start();result=self.delete()
  with self.assertRaises(urllib.error.HTTPError):
   self.post('/api/npcs/delete-character',{'requestId':'delete.exact','inputHash':'changed','characterId':'npc','expectedRevision':2})
  with make_transport(self.config)as t:
   with self.assertRaises(ValueError):t.admit('npc',self.generation,'late')
  with request_scope(self.headers()):
   with self.assertRaises(ValueError):self.source.save(3,'late',self.doc)
  self.assertEqual(self.delete(),result)

class NGSBoundary(Tests):
 def test_formal_completion_create_same_id_and_old_replay(self):
  import os,sys,pathlib,base64
  sys.path.insert(0,os.environ['NGS_RUNTIME_SOURCE'])
  from local.services.npc_scratch_runtime.coordinator import Coordinator
  from local.services.npc_scratch_runtime.pds import PDS
  from local.services.npc_lifecycle.canonical_completion import preserve
  scratch=self.start();native=self.root/'native';native.mkdir();(native/'old').write_bytes(b'preserved external')
  def call(method,path,payload=None):
   self.assertEqual(method,'GET')
   with urllib.request.urlopen('http://127.0.0.1:'+str(self.pds.server_port)+path,timeout=5)as r:return json.load(r)
  pds=PDS(call);coordinator=Coordinator(self.root/'native-ledger',{'work':native},self.root/'quarantine-native',pds)
  coordinator.activate('npc',self.generation);coordinator.admit('npc',self.generation,'old')
  coordinator.register('npc',self.generation,'old','work','old','frame-scratch');coordinator.finish('npc',self.generation,'old')
  cfg=self.root/'host.json';cfg.write_text(json.dumps({'schemaVersion':'npc-scratch-host.v1','enabled':True,'roots':{'work':str(native)},'ledger':str(coordinator.path),'quarantine':str(coordinator.quarantine)}));cfg.chmod(0o600)
  from unittest.mock import patch
  with patch('local.services.runtime_root_binding.operational_runtime_root',return_value=self.root/'ngs-runtime'):
   from local.services.npc_lifecycle.host import Source
  c=self.source.current();after=copy.deepcopy(c['document']);after['characters']=[];after['generationEpoch']='delete.exact'
  plan={'authority':'filex','operation':'delete','npcIds':['npc'],'inputHash':'original','expectedRevision':c['revision'],'document':after}
  def source_api(method,path,payload):
   self.assertEqual(method,'POST');return self.post(path,payload)
  with patch('local.services.npc_lifecycle.host.api',side_effect=source_api),patch('local.services.npc_lifecycle.canonical_completion.preserve',side_effect=lambda r:preserve(r,config_path=cfg,call=call)):
   receipt=Source().apply(plan,'delete.exact')['deletionReceipt']
  with coordinator.locked()as db:
   self.assertEqual(db.execute('SELECT state FROM npc_generation').fetchone()[0],'canonical-deleted-preserved');self.assertEqual(db.execute('SELECT COUNT(*) FROM owned').fetchone()[0],1)
  current=self.source.current()
  with make_transport(self.config)as t:g=t.prepare_create('npc','create.same-id',current['sourceVersion'])
  doc=copy.deepcopy(current['document']);doc['characters'].append({'id':'npc','name':'new incarnation','planningCategories':[]})
  with request_scope({'X-NPC-Ownership-Guard':base64.b64encode(json.dumps(g).encode()).decode()}):self.source.save(current['revision'],'new structure',doc)
  coordinator.activate('npc',g['generationId']);coordinator.admit('npc',g['generationId'],'new')
  preserve(receipt,config_path=cfg,call=call) # old completion response loss after new admission
  with coordinator.locked()as db:
   old=json.loads(db.execute('SELECT record FROM npc_generation_history').fetchone()[0]);self.assertEqual(old['state'],'canonical-deleted-preserved');self.assertEqual(db.execute('SELECT COUNT(*) FROM leases').fetchone()[0],1)
  self.assertEqual((native/'old').read_bytes(),b'preserved external');self.assertEqual((scratch/'frame.png').read_bytes(),b'frame')
  with self.assertRaises(ValueError):coordinator.admit('npc',self.generation,'late')
