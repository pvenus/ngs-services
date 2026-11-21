"""Actual generic HTTP storage; synthetic images/masks, never a model claim."""
import io,json,os,sys,tempfile,threading,unittest,hashlib,copy,base64,importlib.util
from pathlib import Path
from dataclasses import replace
from PIL import Image
sys.path[:0]=[os.environ['WORKERS_NATIVE_PNG_SOURCE'],os.environ['NGS_NATIVE_PNG_SOURCE'],str(Path(__file__).resolve().parents[1]/'data-service')]
from workers.resident.backends.animation_native_png import build_native_png_manifest,DIRECTIONS
from workers.resident.backends.frame_native_manifests import build_alpha_manifest,build_pixel_manifest
from workers.resident.backends.frame_processing import process_set
from local.services.animation_requests.native_png_dag import alpha_dependency,palette_canvas_readiness_v1
from local.services.animation_requests.native_png_workflow import NativePNGWorkflow,BATCH_SCHEMA
from local.services.npc_ownership import Session
from local.services.npc_scratch_registry import RegistryFactory
from local.services.npc_scratch_runtime.coordinator import Coordinator
from local.services.animation_requests.native_png_closeout import close_published
from local.services.request_pipeline.common import CommonRequests
from workers.resident.adapters.common_authority import CommonAuthorityBridge
from workers.resident.contracts import PROFILES,Receipt
from workers.resident.adapters.native_png_inputs import freeze_inputs,read_claim_inputs
from project_data_service.pds_owned_transport import OwnedNPCTransport
from fenced_store import FencedOwnedNPCStore
from npc_store import encoded,NPC_PREFIX,digest
from server import handler
from http.server import ThreadingHTTPServer
class Tests(unittest.TestCase):
 def test_actual_resident_claim_renew_complete_all_directions(self):
  self.run_native_chain(use_server_storage=False)

 def test_native_request_server_storage_to_existing_owned_http(self):
  self.run_native_chain(use_server_storage=True)

 def test_native_registered_workspace_coverage_and_closeout(self):
  self.run_native_chain(use_server_storage=True,use_native_host=True)

 def test_native_ordinary_daemon_full_stages_with_fixture_inference(self):
  self.run_native_chain(use_server_storage=True,use_native_host=True,use_daemon=True)

 def test_ordinary_daemon_stops_at_definitive_changed_source_without_claim(self):
  self.run_native_chain(use_server_storage=True,use_native_host=True,stale_preflight=True)

 def test_native_daemon_recovers_lost_raw_commit_response_without_reexecution(self):
  self.run_native_chain(use_server_storage=True,use_native_host=True,use_daemon=True,recovery="raw")

 def test_native_daemon_recovers_lost_alpha_commit_response_without_reexecution(self):
  self.run_native_chain(use_server_storage=True,use_native_host=True,use_daemon=True,recovery="alpha")

 def test_formal_one_stop_native_animation_to_original_pixel_handoff(self):
  self.run_native_chain(use_server_storage=True,use_native_host=True,use_daemon=True,formal=True)

 def test_formal_seventeen_stage_pds_and_request_process_transport(self):
  self.run_native_chain(use_server_storage=True,use_native_host=True,use_daemon=True,formal=True,process_storage=True)

 def run_native_chain(self,*,use_server_storage,use_native_host=False,use_daemon=False,stale_preflight=False,recovery=None,formal=False,process_storage=False):
  with tempfile.TemporaryDirectory()as temp:
   root=Path(temp).resolve();canonical_root=root/'canonical';canonical_root.mkdir();(canonical_root/'npc').mkdir();(canonical_root/'metadata.json').write_bytes(encoded({'characterOrder':['npc']}));(canonical_root/'npc/metadata.json').write_bytes(encoded({'id':'npc'}))
   if process_storage:(canonical_root/'metadata.json').write_bytes(encoded({'characterOrder':['npc'],'_pdsCurrent':{'revision':1,'savedAt':'fixture','note':'fixture'}}))
   store=FencedOwnedNPCStore(canonical_root);gen=store.bootstrap('npc',store.snapshot()['sourceVersion'])['generationId']
   token=root/'token';token.write_text('f'*32);token.chmod(0o600)
   server=ThreadingHTTPServer(('127.0.0.1',0),handler(store,'f'*32));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   cfg=dict(schemaVersion='pds-filex-cas.v1',projectId='binari',scope='npc',enabled=True,ownershipMode='npc-owned-v1',endpoint='http://127.0.0.1:'+str(server.server_port),tokenFile=str(token))
   raws={};alphas={};raw_bytes={};via_server={};process_transport=None
   def publish(claim,key,manifest,assets):
    rid=claim.request_id
    files=[dict(path=key,expectedHash=None,body=manifest)];read_set={key:None}
    if use_server_storage:
     for aid,data in assets.items():storage.upload_asset(claim,aid,data)
     result=storage.commit(claim,claim.expected_revision,files,read_set)
     self.assertEqual(storage.receipt(claim),result)
     self.assertEqual(storage.commit(claim,claim.expected_revision,files,read_set),result)
     self.assertEqual(result['inputHash'],claim.expected_revision)
     via_server[rid]=result
     return result
    with OwnedNPCTransport(cfg,dict(guard,stageRequestId=rid))as t:
     for aid,data in assets.items():t.asset_put(aid,data)
     result=t.batch(rid,digest(manifest),dict(files=files,readSet=read_set))
     self.assertEqual(t.receipt(rid),result)
     return result
   def read(key,rid):
    with OwnedNPCTransport(cfg)as t:return t.snapshot()['documents'][key.removeprefix(NPC_PREFIX)],t.receipt(rid)
   common=CommonRequests(root/'requests.sqlite')
   def read_raw(task):
    key=NPC_PREFIX+'npc/animation-sequences/idle/walk/'+task['direction']+'.json'
    with OwnedNPCTransport(cfg)as t:
     receipt=t.receipt(task['requestId'])
     if receipt is None:return None
     return dict(manifest=t.snapshot()['documents'][key.removeprefix(NPC_PREFIX)],receipt=receipt)
   def read_alpha(dep):
    key=NPC_PREFIX+'npc/animation-alpha/idle/walk/'+dep['direction']+'.json'
    with OwnedNPCTransport(cfg)as t:
     receipt=t.receipt(dep['requestId'])
     if receipt is None:return None
     return dict(manifestId=key,manifest=t.snapshot()['documents'][key.removeprefix(NPC_PREFIX)],receipt=receipt)
   workflow=NativePNGWorkflow(common,read_raw=read_raw,read_alpha=read_alpha);workflow.initialize()
   # Actual existing context validation over fixture PDS read port. No inference.
   spec=importlib.util.spec_from_file_location('native_source_fixture',Path(os.environ['WORKERS_NATIVE_PNG_SOURCE'])/'tests/test_animation_context_read_race.py')
   mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);source=mod.Tests();source.setUp()
   out=io.BytesIO();Image.new('RGB',(384,384),'red').save(out,format='PNG');reference=out.getvalue();aid=hashlib.sha256(reference).hexdigest()+'.png'
   for view in source.npc['poseViews']['idle']['pixel'].values():view['assetId']=aid
   client=source.loop.client(None);original_json=client.json;motion=b'fixture-original-motion-reference';motion_hash=hashlib.sha256(motion).hexdigest()
   def json_read(path,payload=None):
    if path.startswith('/v1/motion-presets/'):
     return dict(posePreset='idle',presetId='walk',direction=path.rsplit('/',1)[1],cycleCount=1,base64=base64.b64encode(motion).decode(),sha256=motion_hash,assetId=motion_hash+'.mp4',bytes=len(motion),frameCount=2,fps=24,policy={'motionInstruction':'walk','framingPolicy':{},'equipmentPolicy':{}})
    return original_json(path,payload)
   client.json=json_read;client.asset=lambda asset:reference
   def generation(npc):
    with OwnedNPCTransport(cfg)as t:g=t.get_generation(npc)
    return {k:g[k]for k in ('npcId','generationId','state')}
   requests={d:dict(schemaVersion='ngs.npc-animation-request.v1',projectId='binari',npcId='npc',posePreset='idle',presetId='walk',direction=d,referenceType='pixel',intent='walk')for d in reversed(DIRECTIONS)}
   def resolve(request,generation_id):return freeze_inputs(client,request,generation_id=generation_id,read_generation=generation)[0]
   def verify(request,frozen):
    from workers.resident.adapters.native_png_inputs import verify_frozen_for_selection
    return verify_frozen_for_selection(client,request,frozen,read_generation=generation)
   key='one.stop.fixture.animation_walk.1' if formal else 'isolated.eight'
   parent='animation.native.'+digest(['owner',key])[:40]
   if not formal:
    admission=workflow.admit_frozen('owner',key,dict(schemaVersion=BATCH_SCHEMA,npcId='npc',generationId=gen,posePreset='idle',presetId='walk',policyVersion='shared.v1',directionRequests=requests),resolve_frozen=resolve)
    self.assertEqual(parent,admission['requestId']);plan=workflow.select(parent,'batch');scope=plan['scope'];bindings=plan['bindings']
   def ownership_call(method,path,payload):
    with OwnedNPCTransport(cfg,payload.get('guard'))as t:
     if path=='/v1/npcs/ownership/admit':return t.admit(payload['npcId'],payload['generationId'],payload['requestId'])
     if path=='/v1/npcs/ownership/finish':return t.finish()
     raise AssertionError(path)
   session=Session(root/'session','npc',parent,ownership_call)
   if not formal:session.pin(gen);guard=session.enter()
   class PDSGeneration:
    def get_generation(self,npc):
     with OwnedNPCTransport(cfg)as t:g=t.get_generation(npc)
     return {k:g[k]for k in ('npcId','generationId','state')}
   managed={}
   if use_native_host:
    managed={'registered':root/'registered'}
    managed['registered'].mkdir()
   ledger=Coordinator(root/'ledger.sqlite',managed,root/'quarantine',PDSGeneration())
   registry={'schemaVersion':'npc-producer-registry.v1','hosts':['isolated-test-host'],
             'producers':['animation','frames']if use_native_host else['isolated-sync-executor']}
   # Explicit fixture coverage: every sync temporary execution has returned and
   # its TemporaryDirectory is gone before closeout. No production host claim.
   def coverage(g):
    return dict(complete=True,unresolvedPaths=[],workspaces=[],coveredHosts=registry['hosts'],coveredProducers=registry['producers'],producerRegistryHash=digest(registry),**{k:g[k]for k in ('npcId','generationId','requestId')})
   def publications(g):
    receipts=[]
    for row in common.db.execute("SELECT request_id,phase FROM npc_native_png_queue WHERE parent_id=? AND phase!='batch'",(parent,)):
     task=workflow.select(row['request_id'],row['phase']);obs=readback(row['phase'],task)
     manifest=obs['manifest'];assets=[f['assetId']for d in DIRECTIONS for f in manifest['directions'][d]['frames']] if row['phase']=='pixel' else [f['assetId' if row['phase']=='raw' else 'alphaAssetId']for f in manifest['frames']]
     workflow.completion_proof(row['phase'],row['request_id'],receipt_asset_ids=assets,receipt_revision=digest(manifest),readback=readback)
     receipts.append(dict(obs['receipt'],npcId='npc',verified=True))
    return dict(complete=len(receipts)==17,requestId=parent,receipts=receipts)
   factory=RegistryFactory(ledger,{},registry,lambda *a:None,coverage,publications,lambda g:None)
   owner_producer=None if formal else factory(guard)
   if not use_native_host:owner_producer.start()
   stopped=set()
   def readback(phase,task):
    kind={'raw':'animation-sequences','alpha':'animation-alpha','pixel':'animation-processed'}[phase]
    key=NPC_PREFIX+'npc/'+kind+'/idle/walk'+('/'+task['direction'] if phase!='pixel' else '')+'.json'
    m,r=read(key,task['requestId']);return dict(manifestId=key,manifest=m,receipt=r)
   def proof(claim,receipt):
    phase={'animation.generate':'raw','image.process':'alpha','frames.extract':'pixel'}[claim.capability]
    return workflow.completion_proof(phase,claim.request_id,receipt_asset_ids=receipt.asset_refs,receipt_revision=receipt.revision,readback=readback)
   prepared={'selection':None}
   if use_server_storage:
    from project_data_service.npc_owned_operations import OwnedNPCOperations
    from workers.resident.deployment.native_png_requests import NativePNGRequests
    from workers.resident.deployment.native_png_storage import NativePNGStorage
    def original_guard_for_claim(claim):
     self.assertFalse(common.db.in_transaction)
     task=claim.parameters;self.assertEqual((task['npcId'],task['generationId'],task['parentRequestId']),('npc',gen,parent))
     pin=json.loads(session.path.read_text())
     self.assertEqual((pin['npcId'],pin['generationId'],pin['requestId']),('npc',gen,parent))
     self.assertIsNot(pin.get('released'),True)
     return {'guard':pin['guard'],'scopeRequestId':parent,'stageRequestId':claim.request_id}
    authority=NativePNGRequests(workflow,threading.RLock(),proof,{'video-global':1,'cpu-image':1},
                                verify_frozen=verify,verify_stopped=lambda c:c.resource_grant in stopped,
                                guard_resolver=original_guard_for_claim)
    def operation_factory(bound,stage_request_id):
     self.assertFalse(common.db.in_transaction)
     self.assertEqual(bound,guard)
     return OwnedNPCOperations(lambda:OwnedNPCTransport(cfg),guard=bound,stage_request_id=stage_request_id)
    storage=NativePNGStorage(authority.authority,operation_factory,enabled=True)
    if process_storage:
     from native_png_process_transport_fixture import ProcessTransport
     process_transport=ProcessTransport(root,cfg,root/'requests.sqlite',session.path,lambda claim:original_guard_for_claim(claim)['guard'])
     self.addCleanup(process_transport.close)
     storage=process_transport.storage
   else:
    authority=CommonAuthorityBridge(common,threading.RLock(),lambda p:workflow.select_prepared_frozen_resident(p.role,prepared['selection'])if p.role=='animation'else workflow.select_for_resident(p.role),proof,{'video-global':1,'cpu-image':1},verify_stopped=lambda c:c.resource_grant in stopped,native_png=True)
   authority.initialize_schema()
   if use_native_host:
    from dataclasses import asdict
    from urllib.parse import parse_qs,urlsplit
    from local.services.animation_requests.native_png_host import NativePNGHostBinding
    from local.services.npc_scratch_registry import digest as registry_digest
    from workers.resident.adapters.animation_workspace import AnimationWorkspace
    from workers.resident.adapters.frame_workspace import HalfFrameWorkspace,save
    from workers.resident.adapters.attempt_directory_lock import directory_read_cohort
    from workers.resident.contracts import Claim
    from workers.resident.deployment.native_png_host_observer import NativePNGHostObserver
    from workers.resident.terminal_evidence import SourcePin
    route_map={role:{'hostId':'isolated-test-host','rootKey':'registered','scratchRoot':str(managed['registered']/role)}
               for role in ('animation','postprocess')}
    for route in route_map.values():Path(route['scratchRoot']).mkdir()
    workspaces={'animation':AnimationWorkspace(route_map['animation']['scratchRoot'],None),
                'postprocess':HalfFrameWorkspace(route_map['postprocess']['scratchRoot'],None)}
    worker_src=Path(os.environ['WORKERS_NATIVE_PNG_SOURCE'])
    pins=[]
    for entry in ('deployment/native_png_host_observer.py','deployment/role_factory.py','adapters/animation_workspace.py','adapters/frame_workspace.py','adapters/attempt_directory_lock.py'):
     f=worker_src/'workers/resident'/entry;pins.append(SourcePin(str(f),hashlib.sha256(f.read_bytes()).hexdigest()))
    def cleaned(claim):
     return workflow.verify_attempt_cleanup(asdict(claim))
    observer=NativePNGHostObserver(authority.authority,routes=route_map,producer_registry_hash=registry_digest(registry),
             source_pins=pins,verify_cleaned=cleaned,enabled=True)
    def terminal(dto):
     claim=Claim(**{**dto,'input_refs':tuple(dto['input_refs'])})
     return cleaned(claim) and authority.verify_stopped(claim) is True
    hide_receipt={'value':False}
    def pds_receipt_list(method,url):
     self.assertFalse(common.db.in_transaction);self.assertEqual((method,urlsplit(url).path),('GET','/v1/npcs/ownership/publications'))
     q=parse_qs(urlsplit(url).query)
     with OwnedNPCTransport(cfg)as t:value=t.publication_receipts(q['npcId'][0],q['generationId'][0],q['parentRequestId'][0])
     if hide_receipt['value']:value={**value,'receipts':value['receipts'][:-1]}
     return value
    host=NativePNGHostBinding(workflow,session=session,coordinator=ledger,roots=managed,registry=registry,routes=route_map,
         observe_role=observer,verify_terminal=terminal,readback=readback,call=pds_receipt_list,inventory_cohort=directory_read_cohort,enabled=True)
    if formal:
     from local.services.npc_one_stop.native_png import NativeFormalAdapters
     formal_input={'character':{'npcId':'npc'},'intent':'walk','includeCombat':False}
     def prepare_original(src,actor,rid,incarnation):
      nonlocal guard
      self.assertEqual((src['direction'],actor,rid,incarnation),('all','owner',parent,'fixture.incarnation'))
      session.pin(gen);guard=session.enter()
      return session,gen,resolve
     def canonical_current():
      self.assertFalse(common.db.in_transaction)
      with OwnedNPCTransport(cfg)as t:
       snap=t.snapshot()
      return {'authority':'filex','sourceVersion':snap['sourceVersion'],
          'document':{'characters':[{'id':'npc','creationBasis':{'requestId':'fixture.incarnation'}}]}}
     adapter=NativeFormalAdapters(root/'formal-children',call=lambda *_:self.fail('legacy fallback'),
       read=canonical_current,native_png=True,workflow=workflow,prepare=prepare_original,host_for=lambda *_:host)
     admitted=adapter.accept('animation_walk',formal_input,key,'owner',[])
     self.assertEqual(admitted,{'requestId':parent,'channel':'animation-native'})
     self.assertEqual(adapter.accept('animation_walk',formal_input,key,'owner',[]),admitted)
     self.assertEqual(adapter.advance('animation_walk',parent,'owner')['state'],'pending')
     plan=workflow.select(parent,'batch');scope=plan['scope'];bindings=plan['bindings']
    else:
     host.start()
     self.assertEqual(host.close('owner','finish.fixture')['state'],'waiting')
   else:
    self.assertEqual(close_published(workflow,parent,'owner','finish.fixture',readback=readback,session=session,producer=owner_producer)['state'],'waiting')
   self.assertEqual(common.row(parent)['state'],'queued')
   def finish(claim,manifest,assets):
    reported=Receipt(claim.request_id,claim.attempt_id,claim.fencing_token,tuple(assets),digest(manifest),True)
    self.assertTrue(authority.renew(claim));authority.checkpoint(claim,reported)
    if use_native_host:
     role='animation'if claim.capability=='animation.generate'else'postprocess'
     w=workspaces[role];folder=w._folder(claim)
     folder.mkdir(mode=0o700);save(folder/'claim.json',asdict(claim));save(folder/'fixture-output.json',manifest)
     if len(stopped)==0:
      with self.assertRaises(ValueError):host.factory.claim(guard,'animation-job',str(folder.with_name('unissued')))
      # Hold the actual inventory cohort while the original EX cleanup starts.
      # It must not delete either file before Coordinator has registered bytes.
      from unittest.mock import patch
      done=threading.Event();begun=threading.Event();results=[];register=ledger.register
      def cleanup():
       begun.set()
       try:results.append(w.cleanup(claim,None))
       finally:done.set()
      worker=threading.Thread(target=cleanup)
      def race(*args):
       if not worker.is_alive() and not begun.is_set():worker.start()
       self.assertTrue(begun.wait(2));self.assertFalse(done.wait(.03))
       self.assertTrue(folder.is_dir())
       return register(*args)
      try:
       with patch.object(ledger,'register',side_effect=race):observed=host.refresh()
      finally:
       if begun.is_set():worker.join(2)
      self.assertEqual(results,[True])
     else:
      observed=host.refresh()
      self.assertTrue(w.cleanup(claim,None))
     self.assertFalse(folder.exists())
     self.assertIn(str(folder),[x['path']for x in observed['workspaces']])
     with ledger.locked()as db:self.assertEqual(db.execute('SELECT count(*) FROM owned WHERE request=? AND path=?',(parent,str((folder/'claim.json').relative_to(managed['registered'])))).fetchone()[0],1)
    stopped.add(claim.resource_grant);authority.complete(claim,reported,True)
    if claim.capability=='frames.extract':
     pending=host.close('owner','finish.fixture') if use_native_host else workflow.finish_resident(parent,'owner','finish.fixture',readback=readback)
     self.assertEqual(pending['state'],'waiting')
    authority.release_resource(claim)
    self.assertEqual(common.row(claim.request_id)['state'],'completed')
   self.assertEqual(workflow.tick_resident(parent,'owner')['state'],'waiting')
   def recover_publication(claim,folder,workspace,fetch_asset):
    from workers.resident.deployment.native_png_recovery import select_native_recovery
    from workers.resident.adapters.reconciliation import ReconciliationSupervisor
    bridge=authority.authority
    reads=[];receipt_visible=[False]
    def scoped_readback(phase,task):
     self.assertFalse(common.db.in_transaction)
     self.assertEqual(task,claim.parameters)
     doc=readback(phase,task)
     scoped=operation_factory(guard,claim.request_id).receipt(claim.request_id)
     self.assertEqual(doc['receipt'],scoped)
     reads.append(scoped)
     return doc if receipt_visible[0] else None
    self.assertIsNone(select_native_recovery({},{}))
    reader=select_native_recovery({'nativePNG':True},dict(authority=bridge,workflow=workflow,workspace=workspace,readback=scoped_readback,read_asset=fetch_asset))
    cleaned_paths=[]
    def cleanup(original):
     self.assertEqual(original,claim);cleaned_paths.append(folder)
     return workspace.cleanup(original,folder)
    supervisor=ReconciliationSupervisor(bridge,reader,cleanup,verify_recovery_publication=reader.verify)
    supervisor.initialize_schema()
    stopped.remove(claim.resource_grant)
    self.assertEqual(supervisor.tick(),[(claim.request_id,'unknown')])
    self.assertEqual(cleaned_paths,[])
    self.assertTrue(folder.exists())
    self.assertEqual(reads,[])
    clock=common.clock
    common.clock=lambda:clock()+600
    stopped.add(claim.resource_grant)
    self.assertEqual(supervisor.tick(),[(claim.request_id,'unknown')])
    self.assertEqual(cleaned_paths,[])
    self.assertTrue(folder.exists())
    receipt_visible[0]=True
    common.clock=lambda:clock()+1200
    intent=folder/'publication-intent.json';exact_intent=intent.read_bytes()
    intent.write_bytes(encoded({**json.loads(exact_intent),'manifestHash':'0'*64}))
    self.assertEqual(supervisor.tick(),[(claim.request_id,'unknown')])
    self.assertTrue(folder.exists())
    self.assertEqual(cleaned_paths,[])
    self.assertEqual(common.row(claim.request_id)['state'],'blocked')
    intent.write_bytes(exact_intent)
    common.clock=lambda:clock()+1800
    self.assertEqual(supervisor.tick(),[(claim.request_id,'completed')])
    observed_count=len(reads);transition_count=common.db.total_changes
    self.assertEqual(supervisor.tick(),[])
    self.assertEqual(len(reads),observed_count)
    self.assertEqual(common.db.total_changes,transition_count)
    self.assertEqual(cleaned_paths,[folder])
    self.assertGreaterEqual(len(reads),2)
   def daemon_chain():
    from unittest.mock import patch
    from workers.resident.adapters.failure_fence import FailureFence
    from workers.resident.adapters.native_png_inputs import stage_raw_inputs
    from workers.resident.deployment.composition import compose
    from workers.resident.deployment.native_png_ports import build_native_ports
    phase_map={'animation.generate':'raw','image.process':'alpha','frames.extract':'pixel'}
    seen={'raw':[],'alpha':[],'pixel':[]}
    authority.authority.failure_fence=FailureFence(authority.authority,lambda *_:guard)
    authority.authority.failure_fence.initialize_schema()
    def fetch_document(mid):
     self.assertFalse(common.db.in_transaction)
     if process_transport is not None:return process_transport.read_document(mid)
     with OwnedNPCTransport(cfg)as t:
      m=t.snapshot()['documents'][mid.removeprefix(NPC_PREFIX)]
      return {'manifest':m,'receipt':t.receipt(m['requestId'])}
    def fetch_asset(aid):
     self.assertFalse(common.db.in_transaction)
     if process_transport is not None:return process_transport.read_asset(aid)
     with OwnedNPCTransport(cfg)as t:return t.asset_get(aid)
    def raw_source(claim):
     return stage_raw_inputs(client,claim,read_generation=generation,assert_current=authority.renew)
    def frozen_source(claim):
     task=claim.parameters
     return freeze_inputs(client,task['directionRequest'],generation_id=gen,read_generation=generation)[0]
    class FixtureSession:
     def __init__(self,*_):self.stopped_groups=[]
     def unload(self):pass
    class FixtureResidency:
     def __init__(self):self.active={};self.calls=[]
     def acquire(self,profile,worker):
      role=profile.role
      if role in self.active:raise AssertionError('duplicate fixture model grant')
      lease=SimpleNamespace(role=role,worker_id=worker,grant_id='fixture-model.'+str(len(self.calls)),token='fixture-only',epoch=1,memory_bytes=1)
      self.calls.append(role);self.active[role]=lease;return lease
     def renew(self,lease):return self.active.get(lease.role)==lease
     def release(self,lease):
      if self.active.pop(lease.role)!=lease:raise AssertionError('fixture model grant changed')
    proof_marker={'fixture':'synchronous-no-external-process'}
    class FixtureEvidence:
     def record_execution(self,claim,worker,proof):
      # Production evidence is not claimed for synthetic Wan/alpha runners.
      expected={'outcome':'joined','processGroups':[],'returnCodes':[]} if claim.capability=='frames.extract'else proof_marker
      self_outer.assertEqual(proof,expected)
      stopped.add(claim.resource_grant)
     def record_model(self,lease,worker,proof):
      self_outer.assertIn(proof,[proof_marker,{'outcome':'never_started','processGroups':[],'returnCodes':[]}])
    self_outer=self
    from types import SimpleNamespace
    fixture_model=root/'fixture-model.invalid';fixture_model.write_text('not an inference model')
    model_root=root/'fixture-models';model_root.mkdir()
    actual_exec=str(Path(sys.executable).resolve());harmless_exec='/usr/bin/true'
    frame_config=dict(python=actual_exec,ffmpeg=harmless_exec,ffprobe=harmless_exec,module_root=str(model_root),
                       matting_model=str(fixture_model),tracking_model=str(fixture_model))
    settings_common={'nativePNG':True,'modelResidencyRoot':str(model_root)}
    residency=FixtureResidency()
    common_bindings=dict(requests=authority,storage=storage,read_document=fetch_document,read_asset=fetch_asset,raw_source=raw_source,
         refresh_host=host.refresh,residency=residency,evidence=FixtureEvidence(),animation_config='fixture-unused',
         native_source_reader=frozen_source,full_frame_config=frame_config,alpha_inference=dict(engine='fixture',modelId='synthetic-mask',
            modelDigest='c'*64,policyHash='d'*64,fixture=True))
    def fake_wan(out,job,session,cancel,*,decoded_output):
     self.assertFalse(cancel.is_set())
     claim=ports['animation'].backend.active_claim
     self.assertEqual(Path(job['referencePath']).read_bytes(),reference)
     self.assertEqual(Path(job['motionPath']).read_bytes(),motion)
     decoded=out/'frames';decoded.mkdir()
     number=DIRECTIONS.index(claim.parameters['direction'])
     for i in range(job['frameCount']):
      Image.new('RGB',(16,16),(20+number*15,40+i*10,60)).save(decoded/f'{i:05d}.png',format='PNG')
     return decoded_output(out,job,{})
    def fake_alpha(source,job,cancel):
     self.assertFalse(cancel.is_set());value=json.loads(source.read_text())
     out=job/'alpha';out.mkdir()
     results=[]
     for i,h in enumerate(value['rawSha256']):
      raw=job/'raw'/f'frame-{i:08d}.png'
      self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(),h)
      im=Image.open(raw).convert('RGBA');mask=Image.new('L',im.size,0);mask.paste(255,(3,3,13,13));im.putalpha(mask)
      p=out/f'frame-{i:08d}.png';im.save(p,format='PNG')
      results.append(hashlib.sha256(p.read_bytes()).hexdigest())
     return dict(modelKey=value['modelKey'],sourceSha256=value['sequenceHash'],frameCount=len(results),alphaSha256=results)
    resident={};ports={}
    path=worker_src/'workers/resident/deployment/native_png_ports.py'
    def compose_role(role):
     settings={**settings_common,'scratchRoot':route_map[role]['scratchRoot']}
     config=dict(schemaVersion='resident-deployment.v1',role=role,workerId=role+'.fixture.daemon',
         factoryPath=str(path),factorySha256=hashlib.sha256(path.read_bytes()).hexdigest(),
         capabilities=list(PROFILES[role].capabilities),activationApproved=True,settings=settings)
     def build(role,settings):
      port=build_native_ports(role,settings,common_bindings);ports[role]=port
      return port
     resident[role]=compose(config,role,loader=build)
     backend=ports[role].backend;orig=backend.execute
     def wrapped(claim,folder,cancel,execute=orig):
      if process_transport is not None:process_transport.stage_request_id=claim.request_id
      artifacts=execute(claim,folder,cancel)
      m=json.loads(artifacts[0].read_text())
      if claim.capability=='animation.generate':
       for f,p in zip(m['frames'],artifacts[1:]):raw_bytes[f['assetId']]=p.read_bytes()
      seen[phase_map[claim.capability]].append((claim,folder));return artifacts
     backend.execute=wrapped
     if role=='animation':
      backend.session_factory=FixtureSession
      backend.execution_evidence=lambda claim:proof_marker if seen['raw'] and seen['raw'][-1][0]==claim else None
      backend.model_unload_evidence=lambda _:proof_marker
     else:
      backend.alpha._run=fake_alpha
      backend.alpha.execution_evidence=lambda claim:proof_marker if seen['alpha'] and seen['alpha'][-1][0]==claim else None
    for role in ('animation','postprocess'):compose_role(role)
    with patch('workers.resident.backends.animation.binding',return_value={}),patch('workers.resident.backends.animation_native_backend.run',side_effect=fake_wan):
     if recovery:
      from workers.resident.runtime import ReconciliationRequired
      if recovery=='alpha':
       self.assertTrue(resident['animation'].poll_once())
       self.assertEqual(workflow.tick_resident(parent,'owner')['state'],'waiting')
      role='animation' if recovery=='raw' else 'postprocess'
      model_calls=['animation'] if recovery=='raw' else ['animation','postprocess']
      expected_runs={'raw':1,'alpha':int(recovery=='alpha'),'pixel':0}
      original_commit=storage.commit
      def lost_commit(*args,**kwargs):
       result=original_commit(*args,**kwargs)
       self.assertEqual(result['status'],'committed')
       raise TimeoutError('fixture lost the committed response')
      with patch.object(storage,'commit',side_effect=lost_commit)as lost:
       with self.assertRaises(ReconciliationRequired):resident[role].poll_once()
       self.assertEqual(lost.call_count,1)
      self.assertTrue(resident[role].stop.is_set())
      self.assertEqual({p:len(v)for p,v in seen.items()},expected_runs)
      self.assertEqual(residency.calls,model_calls)
      claim,folder=seen[recovery][0]
      self.assertIn(claim.resource_grant,stopped)
      self.assertTrue(folder.is_dir())
      self.assertEqual(common.row(claim.request_id)['state'],'blocked')
      self.assertIsNone(common.db.execute('SELECT 1 FROM resident_publication_checkpoints WHERE grant_id=?',(claim.resource_grant,)).fetchone())
      before=readback(recovery,claim.parameters)
      with patch.object(store,'put_asset',side_effect=AssertionError('recovery must not upload'))as no_upload,patch.object(store,'batch',side_effect=AssertionError('recovery must not commit'))as no_commit:
       recover_publication(claim,folder,ports[role].pds,fetch_asset)
       no_upload.assert_not_called();no_commit.assert_not_called()
      self.assertEqual({p:len(v)for p,v in seen.items()},expected_runs)
      self.assertEqual(residency.calls,model_calls)
      self.assertEqual(readback(recovery,claim.parameters),before)
      self.assertFalse(folder.exists())
      self.assertEqual(common.row(claim.request_id)['state'],'completed')
      self.assertEqual(common.db.execute('SELECT released FROM resident_resource_grants WHERE grant_id=?',(claim.resource_grant,)).fetchone()[0],1)
      self.assertGreater(common.row(claim.request_id)['fence'],int(claim.fencing_token))
      detail=common.view(claim.request_id)['events'][-1]['details']
      self.assertEqual(detail,{'reconciled':True,'sourceAttemptId':claim.attempt_id,'sourceFence':claim.fencing_token,'cleanupDone':True,'generationCalls':0})
      self.assertEqual(common.db.execute('SELECT count(*) FROM resident_resource_grants WHERE request_id=?',(claim.request_id,)).fetchone()[0],1)
      self.assertEqual(common.db.execute("SELECT count(*) FROM resident_publication_checkpoints WHERE grant_id=?",(claim.resource_grant,)).fetchone()[0],1)
      self.assertEqual(common.row(parent)['state'],'queued')
      self.assertTrue(workflow._resident_completed(claim.request_id,before))
      manifest=before['manifest']
      asset_key='assetId' if recovery=='raw' else 'alphaAssetId'
      host._checkpoint(claim.request_id,before,[f[asset_key]for f in manifest['frames']])
      _,children,_=host._children(guard)
      issued=host._issued(children)
      self.assertEqual([c['resource_grant'] for c in issued[role]],[claim.resource_grant])
      # The official DAG must consume the recovered original receipt, even though
      # the finalizing fence has no generation grant of its own.
      self.assertEqual(workflow.tick_resident(parent,'owner')['state'],'waiting')
      d=claim.parameters['direction']
      plan=workflow.select(parent,'batch')
      raw,alpha,deps,_=workflow._read_dependencies(parent,plan,require_resident=True)
      self.assertEqual(set(raw),{d})
      self.assertEqual(set(deps),{d})
      self.assertEqual(set(alpha),{d} if recovery=='alpha' else set())
      # A missing/changed durable reconciliation can never stand in for a grant.
      original=common.db.execute('SELECT last_code FROM resident_reconciliations WHERE grant_id=?',(claim.resource_grant,)).fetchone()[0]
      with common.tx():common.db.execute("UPDATE resident_reconciliations SET last_code='FIXTURE_TAMPER' WHERE grant_id=?",(claim.resource_grant,))
      self.assertFalse(workflow._resident_completed(claim.request_id))
      with common.tx():common.db.execute("UPDATE resident_reconciliations SET last_code=? WHERE grant_id=?",(original,claim.resource_grant))
      self.assertTrue(workflow._resident_completed(claim.request_id,before))
      self.assertTrue(cleaned(claim))
      self.assertFalse(cleaned(replace(claim,attempt_id='999')))
      self.assertEqual(residency.active,{})
      compose_role(role) # restart only the stopped ordinary daemon, after the original attempt finalized
      if recovery=='raw':self.assertTrue(resident['postprocess'].poll_once())
     for _ in range(7 if recovery else len(DIRECTIONS)):
      self.assertTrue(resident['animation'].poll_once())
      if formal:self.assertEqual(adapter.advance('animation_walk',parent,'owner')['state'],'pending')
      else:self.assertEqual(workflow.tick_resident(parent,'owner')['state'],'waiting')
      self.assertTrue(resident['postprocess'].poll_once())
     if formal:self.assertEqual(adapter.advance('animation_walk',parent,'owner')['state'],'pending')
     else:
      queued=workflow.tick_resident(parent,'owner');self.assertEqual(queued['state'],'ready')
     self.assertTrue(resident['postprocess'].poll_once())
    self.assertEqual(residency.calls.count('animation'),8)
    self.assertEqual(residency.calls.count('postprocess'),8)
    self.assertEqual(residency.active,{})
    for phase,expected in (('raw',8),('alpha',8),('pixel',1)):
     self.assertEqual(len(seen[phase]),expected)
     for claim,path in seen[phase]:
      self.assertFalse(path.exists())
      self.assertEqual(common.row(claim.request_id)['state'],'completed')
      doc=readback(phase,claim.parameters)
      via_server[claim.request_id]=doc['receipt']
      if phase=='raw':raws[claim.parameters['direction']]={k:doc[k]for k in ('manifest','receipt')}
      if phase=='alpha':alphas[claim.parameters['direction']]=doc
    self.assertEqual(common.db.execute('SELECT count(*) FROM resident_failure_grants WHERE publication_started=1').fetchone()[0],17)
    pixel_claim=seen['pixel'][0][0];pid=pixel_claim.request_id
    final,receipt=read(NPC_PREFIX+'npc/animation-processed/idle/walk.json',pid)
    return palette_canvas_readiness_v1(scope,bindings,raws,alphas),pid,pixel_claim,final,receipt
   try:
    if stale_preflight:
     from workers.resident.runtime import ResidentWorker
     class NoExecution:
      def __getattr__(self,key):raise AssertionError('preflight-only request attempted worker execution: '+key)
     worker=ResidentWorker(PROFILES['animation'],'fixture.stale',authority,NoExecution(),NoExecution())
     source.errors=['WORKER_CONTEXT_SOURCE_CHANGED']
     with self.assertRaisesRegex(ValueError,'WORKER_CONTEXT_SOURCE_CHANGED'):worker.poll_once()
     self.assertEqual(common.row(parent)['state'],'queued')
     source.change_input=True
     self.assertFalse(worker.poll_once())
     parent_row=common.view(parent)
     self.assertEqual((parent_row['state'],parent_row['stage'],parent_row['error']),('failed','source-preflight','ANIMATION_NATIVE_INPUT_CHANGED'))
     failed=[common.view(rid)for rid in admission['rawRequestIds']if common.row(rid)['state']=='failed']
     self.assertEqual(len(failed),1)
     self.assertEqual(parent_row['events'][-1]['details']['childFailureEventHash'],failed[0]['events'][-1]['event_hash'])
     self.assertTrue(all(row['fence']==0 and row['worker'] is None for row in [common.row(parent),*(common.row(rid)for rid in admission['rawRequestIds'])]))
     self.assertEqual(common.db.execute('SELECT count(*) FROM resident_resource_grants').fetchone()[0],0)
     with OwnedNPCTransport(cfg)as t:
      self.assertEqual(t.get_generation('npc')['activeLeases'],1)
      self.assertEqual(t.publication_receipts('npc',gen,parent)['receipts'],[])
     return
    if use_daemon:
     ready,pixel_id,pixel_claim,final,receipt=daemon_chain()
    else:
     for number in range(8):
      if not use_server_storage:prepared['selection']=workflow.prepare_frozen_resident('animation',verify_frozen=verify)
      raw_claim=authority.claim(PROFILES['animation'],'raw.fixture.worker');self.assertIsNotNone(raw_claim);d=raw_claim.parameters['direction'];self.assertTrue(authority.renew(raw_claim))
      if use_native_host and number==0:
       self.assertEqual(host.close('owner','finish.fixture')['state'],'waiting')
       with self.assertRaisesRegex(ValueError,'CLEANUP_REQUIRED'):host.refresh() # issued is not yet staged, not empty coverage
      self.assertIsNone(authority.claim(PROFILES['animation'],'second.video'))
      missing=Receipt(raw_claim.request_id,raw_claim.attempt_id,raw_claim.fencing_token,('0'*64+'.png',),'0'*64,True)
      with self.assertRaises((ValueError,KeyError)):authority.complete(raw_claim,missing,True)
      self.assertEqual(common.row(raw_claim.request_id)['state'],'processing')
      self.assertEqual(read_claim_inputs(client,raw_claim,read_generation=generation,assert_current=authority.renew)['reference'],reference)
      bind=bindings[d];self.assertEqual(common.row(bind['requestId'])['state'],'processing')
      b={**{k:scope[k]for k in ('npcId','generationId','posePreset','presetId')},**bind}
      with tempfile.TemporaryDirectory()as producer:
       payloads=[]
       for i in range(2):
        out=io.BytesIO();Image.new('RGB',(16,16),(20+number*15,40+i*10,60)).save(out,format='PNG');data=out.getvalue();p=Path(producer)/f'{i}.png';p.write_bytes(data);payloads.append(p.read_bytes())
       m=build_native_png_manifest(b,payloads,frame_count=2,fps=24);key=NPC_PREFIX+'npc/animation-sequences/idle/walk/'+d+'.json'
       if use_server_storage and number==0:
        self.assertIsNone(storage.receipt(raw_claim))
        with self.assertRaises(ValueError):storage.receipt(replace(raw_claim,fencing_token='777'))
        with self.assertRaises(ValueError):
         storage.commit(raw_claim,digest(m),[dict(path=key,body=m,expectedHash=None)],{key:None})
        for wrong in (key.replace('/npcs/npc/','/npcs/other/'),key.replace('/animation-sequences/','/animation-alpha/')):
         with self.assertRaises(ValueError):
          storage.commit(raw_claim,raw_claim.expected_revision,[dict(path=wrong,body=m,expectedHash=None)],{wrong:None})
        another=store.admit('npc',gen,'fixture.other-parent')
        try:
         authority.authority.guard_resolver=lambda c:{'guard':another,'scopeRequestId':'fixture.other-parent','stageRequestId':c.request_id}
         with self.assertRaisesRegex(ValueError,'NATIVE_PNG_STORAGE_SCOPE'):storage.receipt(raw_claim)
        finally:
         authority.authority.guard_resolver=original_guard_for_claim
         with OwnedNPCTransport(cfg,another)as t:self.assertTrue(t.finish()['released'])
        self.assertIsNone(storage.receipt(raw_claim))
       publish(raw_claim,key,m,{row['assetId']:data for row,data in zip(m['frames'],payloads)})
       raw_bytes.update({row['assetId']:data for row,data in zip(m['frames'],payloads)})
      self.assertFalse(Path(producer).exists());m,r=read(key,bind['requestId']);raws[d]={'manifest':m,'receipt':r};dep=alpha_dependency(scope,bind,m,r);self.assertEqual(workflow.tick_resident(parent,'owner')['state'],'waiting');finish(raw_claim,m,[f['assetId'] for f in m['frames']]);workflow.tick_resident(parent,'owner');self.assertEqual(workflow.select(dep['requestId'],'alpha'),dep);alpha_claim=authority.claim(PROFILES['postprocess'],'alpha.fixture.worker');self.assertEqual(alpha_claim.request_id,dep['requestId'])
      self.assertEqual(palette_canvas_readiness_v1(scope,bindings,raws,alphas)['state'],'waiting') if len(bindings)==8 else None
      with tempfile.TemporaryDirectory()as alpha_executor:
       raw=[];modified=[]
       with OwnedNPCTransport(cfg)as t:
        for entry in m['frames']:
         data=t.asset_get(entry['assetId']);raw.append(data);im=Image.open(io.BytesIO(data)).convert('RGBA');mask=Image.new('L',im.size,0);mask.paste(255,(3,3,13,13));im.putalpha(mask);out=io.BytesIO();im.save(out,format='PNG');modified.append(out.getvalue())
       a=build_alpha_manifest(m,raw,modified,dependency_hash=digest(dep),raw_manifest_id=key,alpha_request_id=dep['requestId'],inference=dict(engine='fixture',modelId='synthetic-mask',modelDigest='c'*64,policyHash='d'*64,inferenceRequestId=dep['requestId'],fixture=True))
       ak=NPC_PREFIX+'npc/animation-alpha/idle/walk/'+d+'.json';publish(alpha_claim,ak,a,{entry['alphaAssetId']:data for entry,data in zip(a['frames'],modified)})
      self.assertFalse(Path(alpha_executor).exists());a,ar=read(ak,dep['requestId']);alphas[d]=dict(manifestId=ak,manifest=a,receipt=ar);finish(alpha_claim,a,[f['alphaAssetId'] for f in a['frames']])
     ready=palette_canvas_readiness_v1(scope,bindings,raws,alphas);self.assertEqual(ready['state'],'ready');queued=workflow.tick_resident(parent,'owner');self.assertEqual(queued['state'],'ready');pixel_id=queued['pixelRequestId'];self.assertEqual(common.row(pixel_id)['state'],'queued');pixel_claim=authority.claim(PROFILES['postprocess'],'pixel.fixture.worker');self.assertEqual(pixel_claim.request_id,pixel_id)
     with tempfile.TemporaryDirectory()as pixel_executor:
      folder=Path(pixel_executor);inputs={}
      with OwnedNPCTransport(cfg)as t:
       for d in DIRECTIONS:
        a=alphas[d]['manifest'];rawpaths=[];alphapaths=[]
        for i,f in enumerate(a['frames']):
         for field,paths in [('rawAssetId',rawpaths),('alphaAssetId',alphapaths)]:
          p=folder/(d+field+str(i)+'.png');p.write_bytes(t.asset_get(f[field]));paths.append(str(p))
        inputs[d]=dict(rawFrames=rawpaths,alphaFrames=alphapaths,fps=24,frameCount=2,sourceAssetId=raws[d]['manifest']['requestId'])
      result=process_set(inputs,folder/'output',pixel_scale=.5);final=build_pixel_manifest(result,{d:alphas[d]['manifest']for d in DIRECTIONS});fk=NPC_PREFIX+'npc/animation-processed/idle/walk.json'
      publish(pixel_claim,fk,final,{sha+'.png':Path(path).read_bytes()for sha,path in result['artifacts'].items()})
     self.assertFalse(Path(pixel_executor).exists());final,receipt=read(fk,pixel_id)
    self.assertEqual(list(ready['directions'][i]['direction']for i in range(8)),list(DIRECTIONS))
    for d in DIRECTIONS:
     self.assertEqual([f['index']for f in final['directions'][d]['frames']],[0,1])
     self.assertEqual([f['sourceTimestamp']for f in final['directions'][d]['frames']],[0,1/24])
     self.assertEqual(final['directions'][d]['fps'],24)
    if not use_daemon:finish(pixel_claim,final,[f['assetId']for d in DIRECTIONS for f in final['directions'][d]['frames']])
    self.assertEqual(final['pixelPolicy']['paletteScope'],'all-directions-all-frames');self.assertEqual(final['canvas']['scale'],.5)
    if process_transport is not None:
     process_transport.stage_request_id=pixel_id
     pixel_task=dict(pixel_claim.parameters,requestId=pixel_id)
     product_pixel=process_transport.reader().readback('pixel',pixel_task)
     self.assertNotIn('requestId',final) # pixel manifest binds request through the receipt
     self.assertEqual(product_pixel['manifest'],final)
     self.assertEqual(product_pixel['receipt'],receipt)
     self.assertEqual(product_pixel['receipt']['stageRequestId'],pixel_id)
     self.assertEqual(product_pixel['receipt']['generationId'],gen)
     self.assertEqual(product_pixel['receipt']['parentRequestId'],parent)
     self.assertEqual(product_pixel['receipt']['hashes'],{product_pixel['manifestId']:digest(final)})
     with self.assertRaisesRegex(ValueError,'NATIVE_READ_RECEIPT_REQUIRED'):
      process_transport.reader().readback('pixel',dict(pixel_task,requestId='fixture.wrong-pixel-stage'))
     self.assertGreaterEqual(process_transport.verify(),17)
    if formal:
     self.assertEqual(adapter.view('animation_walk',parent,'owner')['state'],'pending')
     hide_receipt['value']=True
     with self.assertRaisesRegex(ValueError,'NPC_PUBLICATION_STAGE_MISMATCH'):adapter.advance('animation_walk',parent,'owner')
     self.assertEqual(common.row(parent)['state'],'completed')
     self.assertEqual(adapter.view('animation_walk',parent,'owner')['state'],'pending')
     with OwnedNPCTransport(cfg)as t:self.assertEqual(t.get_generation('npc')['activeLeases'],1)
     hide_receipt['value']=False
     published=adapter.advance('animation_walk',parent,'owner')
     self.assertEqual(published['state'],'published');self.assertEqual(published['receipt']['pixelRequestId'],pixel_id)
     before_count=common.db.total_changes
     frame_key='one.stop.fixture.frames_walk.1'
     bad=dict(published['receipt'],pixelReceiptHash='0'*64)
     with self.assertRaisesRegex(ValueError,'PREDECESSOR_RECEIPT'):
      adapter.accept('frames_walk',formal_input,frame_key,'owner',[bad])
     accepted_frame=adapter.accept('frames_walk',formal_input,frame_key,'owner',[published['receipt']])
     self.assertEqual(accepted_frame,{'requestId':pixel_id,'channel':'animation-native'})
     frame=adapter.view('frames_walk',pixel_id,'owner')
     self.assertEqual((frame['state'],frame['receipt']['nativeParentRequestId']),('published',parent))
     self.assertEqual(frame['receipt']['pixelReceiptHash'],digest(receipt))
     self.assertEqual(common.db.total_changes,before_count)
     self.assertEqual(common.db.execute("SELECT count(*) FROM common_requests WHERE state='completed'").fetchone()[0],18)
     with ledger.locked()as db:self.assertEqual(db.execute('SELECT count(*) FROM leases').fetchone()[0],0)
     with OwnedNPCTransport(cfg)as t:self.assertEqual(t.get_generation('npc')['activeLeases'],0)
     return
    if use_native_host:
     self.assertEqual(common.row(parent)['state'],'queued')
     hide_receipt['value']=True
     with self.assertRaisesRegex(ValueError,'NPC_PUBLICATION_STAGE_MISMATCH'):host.close('owner','finish.fixture')
     self.assertEqual(common.row(parent)['state'],'completed') # original Common close verified manifests; scoped release is still held
     with OwnedNPCTransport(cfg)as t:self.assertEqual(t.get_generation('npc')['activeLeases'],1)
     hide_receipt['value']=False
    finished=workflow.finish_resident(parent,'owner','finish.fixture',readback=readback)
    self.assertEqual(finished['state'],'completed')
    self.assertEqual(common.row(parent)['state'],'completed')
    self.assertEqual(workflow.finish_resident(parent,'owner','finish.fixture',readback=lambda *a:(_ for _ in ()).throw(AssertionError('no replay IO'))),finished)
    self.assertEqual(common.db.execute("SELECT count(*) FROM common_requests WHERE state='completed'").fetchone()[0],18)
    self.assertEqual(common.db.execute('SELECT count(*) FROM resident_resource_grants WHERE request_id=?',(parent,)).fetchone()[0],0)
    self.assertEqual(common.db.execute('SELECT count(*) FROM resident_resource_grants WHERE released=0').fetchone()[0],0)
    self.assertEqual(common.db.execute('SELECT count(*) FROM npc_native_png_queue').fetchone()[0],18)
    if use_server_storage:
     self.assertEqual(len(via_server),17)
     self.assertEqual(via_server[pixel_id],receipt)
     with self.assertRaises(ValueError):storage.receipt(pixel_claim) # completed claim no longer authorizes lookup
    if use_native_host:
     with ledger.locked()as db:
      if use_daemon:
       self.assertEqual(db.execute("SELECT count(*) FROM owned WHERE request=? AND path LIKE '%/claim.json'",(parent,)).fetchone()[0],17)
       self.assertGreater(db.execute('SELECT count(*) FROM owned WHERE request=?',(parent,)).fetchone()[0],34)
      else:self.assertEqual(db.execute('SELECT count(*) FROM owned WHERE request=?',(parent,)).fetchone()[0],34)
     closed=host.close('owner','finish.fixture')
     self.assertTrue(closed['ownershipReleased'])
     self.assertEqual(host.close('owner','finish.fixture'),closed)
    else:
     factory.coverage=lambda g:dict(coverage(g),complete=False)
     with self.assertRaises(ValueError):close_published(workflow,parent,'owner','finish.fixture',readback=readback,session=session,producer=owner_producer)
     with OwnedNPCTransport(cfg)as t:self.assertEqual(t.get_generation('npc')['activeLeases'],1)
     factory.coverage=coverage
     factory.receipts=lambda g:dict(complete=False,requestId=parent,receipts=[])
     with self.assertRaises(ValueError):close_published(workflow,parent,'owner','finish.fixture',readback=readback,session=session,producer=owner_producer)
     with OwnedNPCTransport(cfg)as t:self.assertEqual(t.get_generation('npc')['activeLeases'],1)
     factory.receipts=publications
     # Real Filex finish commits; lost HTTP delivery leaves journal unreleased.
     def lost_finish(method,path,payload):
      result=ownership_call(method,path,payload)
      if path.endswith('/finish'):raise TimeoutError('fixture response lost')
      return result
     session.call=lost_finish
     with self.assertRaises(TimeoutError):close_published(workflow,parent,'owner','finish.fixture',readback=readback,session=session,producer=owner_producer)
     self.assertNotEqual(json.loads(session.path.read_text()).get('released'),True)
     session.call=ownership_call
     closed=close_published(workflow,parent,'owner','finish.fixture',readback=readback,session=session,producer=owner_producer)
     self.assertTrue(closed['ownershipReleased'])
     self.assertEqual(close_published(workflow,parent,'owner','finish.fixture',readback=readback,session=session,producer=owner_producer),closed)
    with ledger.locked()as db:self.assertEqual(db.execute('SELECT count(*) FROM leases').fetchone()[0],0)
    with OwnedNPCTransport(cfg)as t:self.assertEqual(t.get_generation('npc')['activeLeases'],0)
    with OwnedNPCTransport(cfg,guard)as t:
     with self.assertRaisesRegex(ValueError,'^KNOWLEDGE_SOURCE_CHANGED$'):t.asset_put('0'*64+'.png',b'late')
    with OwnedNPCTransport(cfg)as t:
     for aid,data in raw_bytes.items():self.assertEqual(t.asset_get(aid),data)
     for row in final['directions'].values():
      for f in row['frames']:
       im=Image.open(io.BytesIO(t.asset_get(f['assetId'])));self.assertLessEqual(len(im.convert('RGB').getcolors(1<<24)),32);self.assertLessEqual(set(im.getchannel('A').getdata()),{0,255})
    if process_transport is not None:self.assertGreaterEqual(process_transport.verify(),17)
   finally:
    if process_transport is not None:process_transport.close()
    common.close();server.shutdown();server.server_close();thread.join()
