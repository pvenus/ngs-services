"""Temporary authenticated PDS/writer retirement, no operational data/model calls."""
import base64,hashlib,json,sqlite3,threading,urllib.request,urllib.error,unittest
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from test_npc_external_inventory import ExternalInventory
from project_data_service.http_server import Handler
from project_data_service.domain_contracts import legacy_http
from project_data_service.npc_current_generation_inventory import CurrentGenerationInventory
from project_data_service.npc_external_provider import ledger_snapshot
from project_data_service.npc_ownership import make_transport
from project_data_service.npc_external_lifecycle import restore as restore_external

class StagedHTTP(ExternalInventory):
 def start_current(self,valid=True):
  scratch=self.inventory();coordinator=self.source.external_inventory.coordinator
  legacy=self.root/'legacy-unknown';legacy.write_bytes(b'preserve ambiguous history')
  with sqlite3.connect(coordinator.path)as db:
   db.row_factory=sqlite3.Row;ledger=ledger_snapshot(db,'npc',self.generation)
  self.coverage={'valid':valid}
  def evidence(n,g,r):return {'npcId':n,'generationId':g,'requestId':r,'scope':'current-generation','currentGenerationCoverageComplete':self.coverage['valid'],'unresolvedCurrentPaths':[],'allRegisteredHostsVerified':True,'ledgerSnapshotHash':ledger,'legacyPreserved':True,'legacySnapshotHash':hashlib.sha256(legacy.read_bytes()).hexdigest(),'globalCoverageComplete':False,'legacyResumeBlocked':True}
  self.source.external_inventory=CurrentGenerationInventory(coordinator,evidence)
  http=ThreadingHTTPServer(('127.0.0.1',0),Handler);http.npc_store=self.source
  http.app=SimpleNamespace(domains=SimpleNamespace(npcs=self.source,dispatch=lambda h:legacy_http.Handler.route(h)))
  thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
  self.addCleanup(lambda:(http.shutdown(),http.server_close(),thread.join()))
  self.url='http://127.0.0.1:'+str(http.server_port)
  return scratch,coordinator,legacy
 def post(self,path,payload,guard=None):
  headers={'Content-Type':'application/json'}
  if guard:headers['X-NPC-Ownership-Guard']=base64.b64encode(json.dumps(guard).encode()).decode()
  with urllib.request.urlopen(urllib.request.Request(self.url+path,data=json.dumps(payload).encode(),headers=headers),timeout=30)as response:return json.load(response)
 def delete(self):return self.post('/api/npcs/delete-character',{'requestId':'scoped.delete','inputHash':'test-input','characterId':'npc','expectedRevision':2})
 def test_fence_get_reentrant_bound_readonly_and_invalid_after_commit(self):
  from urllib.parse import urlencode
  self.start_current();provider=self.source.external_inventory;original=provider.evidence_reader;proofs=[]
  def get(rid='scoped.delete',gen=None,extra='',origin=None):
   url=self.url+'/v1/npcs/ownership/staged-delete-fence?'+urlencode({'npcId':'npc','generationId':gen or self.generation,'requestId':rid})+extra
   req=urllib.request.Request(url,headers={'Origin':origin} if origin else {})
   with urllib.request.urlopen(req,timeout=3)as response:return json.load(response)
  with self.assertRaises(urllib.error.HTTPError):get()
  def reentrant(*args):
   before=self.writer.owner_files('npc');a=get();b=get();self.assertEqual(a,b);self.assertEqual(before,self.writer.owner_files('npc'))
   self.assertEqual(a['state'],'delete-staged');self.assertEqual(a['activeLeases'],0);self.assertTrue(a['admissionBlocked']);self.assertEqual(a['fenceId'],a['fenceToken'])
   for kwargs in ({'rid':'other'},{'gen':'stale'},{'extra':'&requestId=duplicate'},{'origin':'http://example.invalid'}):
    with self.assertRaises(urllib.error.HTTPError):get(**kwargs)
   with make_transport(self.config)as t:
    with self.assertRaises(ValueError):t.admit('npc',self.generation,'racing-admit')
   self.assertEqual(get(),a);proofs.append(a);return original(*args)
  provider.evidence_reader=reentrant
  self.delete();self.assertEqual(len(proofs),1)
  with self.assertRaises(urllib.error.HTTPError):get()
 def test_current_delete_and_complete_restore_http(self):
  scratch,c,legacy=self.start_current();original=self.writer.owner_files('npc');result=self.delete();receipt=result['deletionReceipt']
  self.assertFalse(result['externalCleanup']['globalCoverageComplete']);self.assertTrue(result['externalCleanup']['legacyPreserved']);self.assertFalse((scratch/'frame.png').exists());self.assertEqual(self.delete(),result)
  current=self.source.current();guard=self.post('/v1/npcs/ownership/prepare-restore',{'npcId':'npc','requestId':'scoped.restore','backupId':receipt['backupId'],'expectedSourceVersion':current['sourceVersion']})
  restored=self.post('/v1/npcs/ownership/restore',{'requestId':'scoped.restore','inputHash':'restore-input','backupId':receipt['backupId'],'expectedRevision':current['revision']},guard)
  self.assertTrue(restored['restorationReceipt']['backupVerified']);self.assertNotEqual(guard['generationId'],self.generation);self.assertEqual(self.writer.owner_files('npc'),original)
  # External scratch is separately recoverable, never implicitly rescheduled.
  restored_scratch=restore_external(c.quarantine,result['externalCleanup']['quarantineId'],c.roots)
  self.assertFalse(restored_scratch['reactivated']);self.assertEqual((scratch/'frame.png').read_bytes(),b'frame');self.assertEqual(legacy.read_bytes(),b'preserve ambiguous history')
 def test_failed_closure_preserves_bytes_fences_admission_and_retries(self):
  scratch,c,legacy=self.start_current(False);before=self.writer.owner_files('npc')
  with self.assertRaises(urllib.error.HTTPError):self.delete()
  self.assertEqual(self.writer.owner_files('npc'),before);self.assertTrue((scratch/'frame.png').exists());self.assertEqual(self.writer.generation('npc')['state'],'delete-staged')
  with make_transport(self.config)as t:
   with self.assertRaises(ValueError):t.admit('npc',self.generation,'late-admission')
  self.coverage['valid']=True;result=self.delete();self.assertEqual(result['deletionReceipt']['status'],'committed');self.assertTrue(legacy.exists())
 def test_concurrent_admission_and_second_delete_cannot_cross_staged_boundary(self):
  import concurrent.futures
  scratch,c,legacy=self.start_current();ready=threading.Event();release=threading.Event();provider=self.source.external_inventory;original=provider.evidence_reader
  def paused(*args):
   ready.set()
   if not release.wait(10):raise ValueError('test barrier timeout')
   return original(*args)
  provider.evidence_reader=paused
  with concurrent.futures.ThreadPoolExecutor(max_workers=1)as pool:
   future=pool.submit(self.delete)
   try:
    self.assertTrue(ready.wait(10));self.assertTrue(self.writer.owner_files('npc'));self.assertTrue((scratch/'frame.png').exists())
    with make_transport(self.config)as t:
     with self.assertRaises(ValueError):t.admit('npc',self.generation,'racing-worker')
    with self.assertRaises(urllib.error.HTTPError):self.post('/api/npcs/delete-character',{'requestId':'racing.delete','inputHash':'other','characterId':'npc','expectedRevision':2})
   finally:release.set()
   self.assertEqual(future.result(timeout=10)['deletionReceipt']['status'],'committed')
  self.assertTrue(legacy.exists())
 def test_backup_corruption_after_closure_cannot_commit_or_retire(self):
  scratch,c,legacy=self.start_current();before=self.writer.owner_files('npc');provider=self.source.external_inventory;original=provider.evidence_reader
  def corrupt_after_stage(*args):
   backup=hashlib.sha256(b'scoped.delete').hexdigest()
   self.writer.safe('.deleted/'+backup+'/files/npc/metadata.json').write_bytes(b'corrupted fixture backup')
   return original(*args)
  provider.evidence_reader=corrupt_after_stage
  with self.assertRaises(urllib.error.HTTPError):self.delete()
  self.assertEqual(self.writer.owner_files('npc'),before);self.assertTrue((scratch/'frame.png').exists());self.assertTrue(legacy.exists());self.assertEqual(self.writer.generation('npc')['state'],'delete-staged')
 def test_other_delete_request_cannot_steal_staged_fence(self):
  scratch,c,legacy=self.start_current(False)
  with self.assertRaises(urllib.error.HTTPError):self.delete()
  self.coverage['valid']=True
  with self.assertRaises(urllib.error.HTTPError):self.post('/api/npcs/delete-character',{'requestId':'different.delete','inputHash':'other','characterId':'npc','expectedRevision':2})
  self.assertTrue((scratch/'frame.png').exists());self.assertEqual(self.writer.generation('npc')['state'],'delete-staged')
  self.assertEqual(self.delete()['deletionReceipt']['status'],'committed')
if __name__=='__main__':unittest.main()
