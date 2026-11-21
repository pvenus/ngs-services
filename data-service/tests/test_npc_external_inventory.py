import pathlib,sys
from test_npc_owned_authority import Tests
from project_data_service.npc_ownership import make_transport,request_scope
from project_data_service.npc_external_inventory import TrustedExternalInventory,AuthenticatedInventoryPDS
from project_data_service.npc_external_coordinator import Coordinator

class ExternalInventory(Tests):
 def inventory(self,complete=True):
  scratch=self.root/'scratch';scratch.mkdir();(scratch/'frame.png').write_bytes(b'frame')
  c=Coordinator(self.root/'external.sqlite',{'frames':scratch},self.root/'quarantine',AuthenticatedInventoryPDS(lambda:make_transport(self.config)))
  c.activate('npc',self.generation);c.admit('npc',self.generation,'producer')
  c.register('npc',self.generation,'producer','frames','frame.png','frame-scratch');c.finish('npc',self.generation,'producer')
  p=TrustedExternalInventory(c,lambda n,g:{'complete':complete,'npcId':n,'generationId':g,'producerRegistryHash':'registry','unresolvedPaths':[]})
  self.source.external_inventory=p
  with request_scope(self.headers()):
   import copy
   changed=copy.deepcopy(self.doc);changed['characters'][0]['name']='updated fixture'
   self.source.save(1,'register owned fixture',changed)
   with make_transport(self.config) as t:t.finish()
  return scratch
 def test_delete_external_actual_authenticated_writer(self):
  scratch=self.inventory();result=self.source.delete_character('delete1','hash','npc',2)
  self.assertEqual(result['deletionReceipt']['generationId'],self.generation)
  self.assertEqual(result['externalCleanup']['files'],1);self.assertFalse((scratch/'frame.png').exists())
  self.assertEqual(self.source.delete_character('delete1','hash','npc',2),result)
  self.assertEqual(self.writer.generation('npc')['state'],'deleted')
 def test_incomplete_coverage_cannot_delete(self):
  scratch=self.inventory(False)
  with self.assertRaisesRegex(ValueError,'INCOMPLETE'):self.source.delete_character('delete1','hash','npc',2)
  self.assertTrue((scratch/'frame.png').exists());self.assertEqual(self.writer.generation('npc')['state'],'delete-staged');self.assertTrue(self.writer.owner_files('npc'))
 def test_no_provider_fails_closed(self):
  with request_scope(self.headers()):
   with make_transport(self.config) as t:t.finish()
  with self.assertRaisesRegex(ValueError,'PROVIDER_REQUIRED'):self.source.delete_character('delete1','hash','npc',1)

 def test_producer_hooks_publication_before_release(self):
  import copy
  from project_data_service.npc_external_producer import ScratchProducer
  from project_data_service.pds_owned_transport import OwnedNPCTransport
  scratch=self.root/'producer';scratch.mkdir();(scratch/'output').write_bytes(b'produced')
  c=Coordinator(self.root/'producer.sqlite',{'pose':scratch},self.root/'recovery',AuthenticatedInventoryPDS(lambda:make_transport(self.config)))
  hook=ScratchProducer(c,lambda:OwnedNPCTransport(self.config,self.guard),self.guard);hook.start();hook.register_output('pose','output','pose-scratch')
  with self.assertRaisesRegex(ValueError,'UNVERIFIED'):hook.finish_published('not-published','hash')
  self.assertEqual(self.writer.generation('npc')['activeLeases'],1)
  changed=copy.deepcopy(self.doc);changed['characters'][0]['name']='producer updated'
  with request_scope(self.headers()):result=hook.publish(lambda generation:self.source.save(1,'hook publish',changed))
  from project_data_service.filex_read import digest
  h=digest([1,'hook publish',changed]);self.assertTrue(hook.finish_published('npc.write.'+h,h)['released'])
  self.assertEqual(self.writer.generation('npc')['activeLeases'],0)
 def test_external_predelete_backup_preserved(self):
  scratch=self.inventory();result=self.source.delete_character('delete1','hash','npc',2)
  backups=list((self.root/'quarantine'/'predelete').glob('*/0'))
  self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),b'frame')
 def test_authoritative_publication_set_through_pds(self):
  import copy,json,threading,urllib.request,urllib.error
  from types import SimpleNamespace
  from http.server import ThreadingHTTPServer
  from project_data_service.http_server import Handler
  changed=copy.deepcopy(self.doc);changed['characters'][0]['name']='published'
  with request_scope(self.headers('stage.actual')):self.source.save(1,'receipt-set fixture',changed)
  http=ThreadingHTTPServer(('127.0.0.1',0),Handler);http.app=SimpleNamespace(domains=SimpleNamespace(npcs=self.source));thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
  from urllib.parse import urlencode
  url='http://127.0.0.1:'+str(http.server_port)+'/v1/npcs/ownership/publications?'+urlencode({'npcId':'npc','generationId':self.generation,'parentRequestId':'parent.request'})
  try:
   value=json.load(urllib.request.urlopen(url));self.assertEqual(len(value['receipts']),1);self.assertEqual(value['receipts'][0]['stageRequestId'],'stage.actual')
   self.assertEqual(value['receipts'][0]['npcId'],'npc');self.assertTrue(value['receipts'][0]['verified'])
   with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(urllib.request.Request(url,headers={'Origin':'http://127.0.0.1:'+str(http.server_port)}))
  finally:http.shutdown();http.server_close();thread.join()
 def test_current_equivalent_excludes_pending_and_checks_owner(self):
  import hashlib,base64
  from project_data_service.pds_owned_transport import OwnedNPCTransport
  data=b'isolated video bytes';h=hashlib.sha256(data).hexdigest();aid=h+'.mp4'
  self.writer.put_asset(aid,data,self.guard)
  key='canonical-files/binari/npcs/npc/animations/idle/run/n.json'
  self.writer.batch('video.publish','input',[{'path':key,'expectedHash':None,'body':{'assetId':aid}}],{key:None},self.guard)
  with OwnedNPCTransport(self.config,self.guard) as transport:
   p=transport.current_equivalent('npc',self.generation,'parent.request',h)
   self.assertTrue(p['currentSlot']);self.assertEqual(base64.b64decode(p['base64']),data)
   unique=b'unpublished';u=hashlib.sha256(unique).hexdigest();self.writer.put_asset(u+'.png',unique,self.guard)
   self.assertFalse(transport.current_equivalent('npc',self.generation,'parent.request',u)['currentSlot'])
   with self.assertRaises(ValueError):transport.current_equivalent('npc','0'*48,'parent.request',h)
 def test_completed_duplicate_cleanup_keeps_filex_and_unique(self):
  import hashlib
  scratch=self.root/'completed';scratch.mkdir();data=b'published output';p=scratch/'video';p.write_bytes(data);unique=scratch/'unique';unique.write_bytes(b'only copy')
  c=Coordinator(self.root/'completed.sqlite',{'scratch':scratch},self.root/'quarantine',AuthenticatedInventoryPDS(lambda:make_transport(self.config)))
  c.activate('npc',self.generation);c.admit('npc',self.generation,'parent.request')
  for name in ('video','unique'):c.register('npc',self.generation,'parent.request','scratch',name,'animation-scratch')
  aid=hashlib.sha256(data).hexdigest()+'.mp4';self.writer.put_asset(aid,data,self.guard)
  key='canonical-files/binari/npcs/npc/animations/idle/run/n.json'
  self.writer.batch('video.publish','input',[{'path':key,'expectedHash':None,'body':{'assetId':aid}}],{key:None},self.guard)
  proof={'npcId':'npc','generationId':self.generation,'requestId':'parent.request','state':'completed','resumeRequired':False,'nonrunning':True,'dependenciesComplete':True,'unpublishedInputs':[]}
  fn=lambda *a:proof
  with self.assertRaisesRegex(ValueError,'NO_EQUIVALENT'):c.cleanup_published_copy('npc',self.generation,'parent.request','scratch','unique',fn)
  proof['resumeRequired']=True
  with self.assertRaisesRegex(ValueError,'FINALIZATION'):c.cleanup_published_copy('npc',self.generation,'parent.request','scratch','video',fn)
  proof['resumeRequired']=False
  from unittest.mock import patch
  original_unlink=pathlib.Path.unlink
  def crash_unlink(path,*a,**kw):
   original_unlink(path,*a,**kw)
   if path==p:raise RuntimeError('crash-after-unlink')
  with patch.object(pathlib.Path,'unlink',crash_unlink):
   with self.assertRaisesRegex(RuntimeError,'crash-after-unlink'):c.cleanup_published_copy('npc',self.generation,'parent.request','scratch','video',fn)
  self.assertFalse(p.exists())
  with self.assertRaises(Exception):self.writer.admit('npc',self.generation,'competing.parent')
  with self.assertRaises(Exception):self.writer.finish(self.guard)
  result=c.cleanup_published_copy('npc',self.generation,'parent.request','scratch','video',fn)
  self.assertTrue(result['removed']);self.assertFalse(p.exists());self.assertTrue(unique.exists());self.assertEqual(self.writer.asset(aid)['base64'],__import__('base64').b64encode(data).decode())
  self.assertEqual(result,c.cleanup_published_copy('npc',self.generation,'parent.request','scratch','video',fn))
 def test_deletion_receipt_available_through_existing_pds_boundary(self):
  import json,threading,urllib.request
  from types import SimpleNamespace
  from http.server import ThreadingHTTPServer
  from project_data_service.http_server import Handler
  self.inventory();receipt=self.source.delete_character('delete.receipt','hash','npc',2)['deletionReceipt']
  http=ThreadingHTTPServer(('127.0.0.1',0),Handler);http.app=SimpleNamespace(domains=SimpleNamespace(npcs=self.source));thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
  try:
   result=json.load(urllib.request.urlopen('http://127.0.0.1:'+str(http.server_port)+'/v1/npcs/ownership/deletion-receipt?requestId=delete.receipt'))
   self.assertEqual(result,receipt)
  finally:http.shutdown();http.server_close();thread.join()
 def test_reserved_equivalent_via_pds_blocks_writer_until_release(self):
  import json,threading,urllib.request,hashlib
  from types import SimpleNamespace
  from http.server import ThreadingHTTPServer
  from project_data_service.http_server import Handler
  data=b'reserved equivalent';sha=hashlib.sha256(data).hexdigest();aid=sha+'.mp4'
  self.writer.put_asset(aid,data,self.guard)
  key='canonical-files/binari/npcs/npc/animations/idle/run/n.json'
  self.writer.batch('reserve.publish','input',[{'path':key,'expectedHash':None,'body':{'assetId':aid}}],{key:None},self.guard)
  http=ThreadingHTTPServer(('127.0.0.1',0),Handler);http.app=SimpleNamespace(domains=SimpleNamespace(npcs=self.source));thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
  base='http://127.0.0.1:'+str(http.server_port)+'/v1/npcs/ownership/'
  def post(name,value):return json.load(urllib.request.urlopen(urllib.request.Request(base+name,data=json.dumps(value).encode(),headers={'Content-Type':'application/json'})))
  body={'npcId':'npc','generationId':self.generation,'parentRequestId':'parent.request','sha256':sha,'reservationId':'a'*64}
  try:
   proof=post('reserve-equivalent',body);self.assertEqual(proof,post('reserve-equivalent',body))
   with self.assertRaises(Exception):self.writer.put_asset(aid,data,self.guard)
   with self.assertRaises(Exception):self.writer.admit('npc',self.generation,'other')
   release={k:v for k,v in body.items()if k!='sha256'};release['reservationToken']=proof['reservationToken']
   self.assertTrue(post('release-equivalent',release)['released']);self.assertTrue(post('release-equivalent',release)['released'])
   self.writer.put_asset(aid,data,self.guard)
  finally:http.shutdown();http.server_close();thread.join()

 def test_batch_retries_release_after_response_loss(self):
  import hashlib
  scratch=self.root/'completed';scratch.mkdir();data=b'published output';p=scratch/'video';p.write_bytes(data);unique=scratch/'unique';unique.write_bytes(b'only copy')
  c=Coordinator(self.root/'completed.sqlite',{'scratch':scratch},self.root/'quarantine',AuthenticatedInventoryPDS(lambda:make_transport(self.config)))
  c.activate('npc',self.generation);c.admit('npc',self.generation,'parent.request')
  for name in ('video','unique'):c.register('npc',self.generation,'parent.request','scratch',name,'animation-scratch')
  aid=hashlib.sha256(data).hexdigest()+'.mp4';self.writer.put_asset(aid,data,self.guard)
  key='canonical-files/binari/npcs/npc/animations/idle/run/n.json'
  self.writer.batch('video.publish','input',[{'path':key,'expectedHash':None,'body':{'assetId':aid}}],{key:None},self.guard)
  proof={'npcId':'npc','generationId':self.generation,'requestId':'parent.request','state':'completed','resumeRequired':False,'nonrunning':True,'dependenciesComplete':True,'unpublishedInputs':[]}
  fn=lambda *a:proof
  from unittest.mock import patch
  original=c.pds.release_equivalent;calls=[]
  def lost(*args):
   calls.append(args)
   if len(calls)==1 and getattr(self,'fail_before_release',False):raise RuntimeError('release-response-lost')
   result=original(*args)
   if len(calls)==1:raise RuntimeError('release-response-lost')
   return result
  with patch.object(c.pds,'release_equivalent',lost):
   with self.assertRaisesRegex(RuntimeError,'response-lost'):c.cleanup_completed_copies('npc',self.generation,'parent.request',fn)
   self.assertFalse(p.exists())
   result=c.cleanup_completed_copies('npc',self.generation,'parent.request',fn)
  self.assertEqual(len(calls),2);self.assertTrue(result['complete']);self.assertTrue(unique.exists())
  with c.locked()as db:self.assertEqual(db.execute('SELECT state FROM completed_copy_cleanup').fetchone()['state'],'released')

 def test_batch_retries_release_network_failure(self):
  self.fail_before_release=True
  self.test_batch_retries_release_after_response_loss()
