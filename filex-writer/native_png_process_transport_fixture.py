"""Test-only multi-process metadata/data planes for the formal daemon chain.
Synthetic inference remains in the parent fixture; never a deployment launcher.
"""
import json,os,subprocess,sys,threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[os.environ['WORKERS_NATIVE_PNG_SOURCE'],os.environ['NGS_NATIVE_PNG_SOURCE'],str(ROOT/'data-service')]

def serve(mode,path):
 from http.server import ThreadingHTTPServer
 c=json.loads(Path(path).read_text())
 if mode=='authority':
  from local.services.request_pipeline.common import CommonRequests
  from workers.resident.adapters.common_authority import CommonAuthorityBridge
  from workers.resident.adapters.publication_authority import handler
  from workers.resident.deployment.native_png_storage import NativePNGStorage
  from workers.resident.deployment.native_png_publication import NativePNGPublicationAuthority
  class Authority:
   def authorize(self,claim,action,metadata):
    common=CommonRequests(c['common'])
    try:
     def original(claim):
      saved=json.loads(Path(c['session']).read_text());guard=saved['guard'];p=claim.parameters
      if saved.get('released') or (guard['npcId'],guard['generationId'],guard['requestId'])!=(p['npcId'],p['generationId'],p['parentRequestId']):raise ValueError('FIXTURE_ORIGINAL_GUARD')
      return dict(guard=guard,scopeRequestId=guard['requestId'],stageRequestId=claim.request_id)
     bridge=CommonAuthorityBridge(common,threading.RLock(),lambda _:None,lambda *_:None,{'video-global':1,'cpu-image':1},verify_stopped=lambda _:False,guard_resolver=original,native_png=True)
     from workers.resident.adapters.failure_fence import FailureFence
     bridge.failure_fence=FailureFence(bridge,lambda *_:original(claim)['guard'])
     storage=NativePNGStorage(bridge,lambda *_:(_ for _ in ()).throw(AssertionError('Request bytes forbidden')),enabled=True)
     result=NativePNGPublicationAuthority(storage).authorize(claim,action,metadata)
     with open(c['audit'],'a')as f:f.write(json.dumps({'pid':os.getpid(),'action':action,'stage':claim.request_id,'metadata':metadata})+'\n')
     return result
    finally:common.close()
  server=ThreadingHTTPServer(('127.0.0.1',0),handler(None,['a'*32],owned_operation_authority=Authority()))
 elif mode=='pds':
  from project_data_service.http_server import Handler
  from project_data_service.owned_operation_http import OwnedOperationsHTTP
  from project_data_service.media_artifacts import Transport
  from project_data_service.pds_owned_transport import OwnedNPCTransport
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
  from types import SimpleNamespace
  from project_data_service.domains import DomainModule as Domains
  from project_data_service.filex_npc_authority import FilexCurrentNPC
  from project_data_service.filex_npc_store import FilexNPCStore
  domains=Domains.__new__(Domains);domains.enabled=True;domains.lock=threading.RLock();domains.story=None
  domains.npcs=FilexCurrentNPC(FilexNPCStore(lambda:OwnedNPCTransport(c['filex'])))
  server.app=SimpleNamespace(domains=domains)
  server.owned_npc_operations=OwnedOperationsHTTP(Transport(c['authority'],c['token']),lambda:OwnedNPCTransport(c['filex']))
 else:raise AssertionError(mode)
 print(json.dumps({'endpoint':'http://127.0.0.1:'+str(server.server_port),'pid':os.getpid()}),flush=True)
 server.serve_forever()

class ProcessTransport:
 def __init__(self,root,filex_config,common_path,session_path,guard_for):
  self.root=root;self.processes=[];self.logs=[];self.audit=root/'process-metadata.jsonl'
  token=root/'fixture-publication-token';token.write_text('a'*32);token.chmod(0o600)
  cfg={'filex':filex_config,'common':str(common_path),'session':str(session_path),'token':str(token),'audit':str(self.audit)}
  try:
   authority=self.start('authority',cfg);cfg['authority']=authority['endpoint'];pds=self.start('pds',cfg)
   from workers.resident.deployment.native_png_http_storage import NativePNGHTTPStorage
   self.storage=NativePNGHTTPStorage(pds['endpoint'],guard_for);self.endpoint=pds['endpoint'];self.session_path=session_path
   self.pids=(authority['pid'],pds['pid']);assert len(set((*self.pids,os.getpid())))==3
  except BaseException:self.close();raise
 def start(self,mode,cfg):
  p=self.root/(mode+'-process.json');p.write_text(json.dumps(cfg));log=(self.root/(mode+'-process.log')).open('w');self.logs.append(log)
  proc=subprocess.Popen([sys.executable,'-B',__file__,mode,str(p)],stdout=subprocess.PIPE,stderr=log,text=True);self.processes.append(proc)
  import selectors
  with selectors.DefaultSelector()as selector:
   selector.register(proc.stdout,selectors.EVENT_READ)
   if not selector.select(20):raise RuntimeError('Fixture subprocess start timeout: '+mode)
  line=proc.stdout.readline()
  if not line:raise RuntimeError((self.root/(mode+'-process.log')).read_text())
  return json.loads(line)
 def read(self,path):
  import urllib.request
  with self.storage.opener.open(self.endpoint+path,timeout=60)as r:return r.read()
 def reader(self):
  from workers.resident.deployment.native_png_readback import NativePNGReadback
  def guard():
   saved=json.loads(Path(self.session_path).read_text())
   if saved.get('released'):raise ValueError('FIXTURE_SESSION_RELEASED')
   return saved['guard']
  return NativePNGReadback(self.endpoint,guard,self.stage_request_id)
 def read_asset(self,aid):
  return self.reader().asset(aid)
 def read_document(self,mid):
  reader=self.reader();g=reader._guard()
  assert reader.generation(g['npcId'])['generationId']==g['generationId']
  result=reader.document(mid)
  for bad in ('canonical-files/binari/npcs/neighbor/animation-sequences/idle/walk/n.json',
              'canonical-files/binari/npcs/'+g['npcId']+'/../animation-sequences/idle/walk/n.json'):
   try:reader.document(bad)
   except ValueError as exc:assert str(exc)=='NATIVE_READ_SCOPE'
   else:raise AssertionError('Neighbor document accepted')
  return result
 def verify(self):
  rows=[json.loads(line)for line in self.audit.read_text().splitlines()]
  writes=[r for r in rows if r['action']=='commit'];assert len({r['stage']for r in writes})==17
  assert all(set(r['metadata'])=={'path','inputHash','manifestHash','assetIds'}for r in writes)
  assert {r['pid']for r in rows}=={self.pids[0]}
  return len(writes)
 def close(self):
  for p in reversed(self.processes):
   if p.poll()is None:p.terminate()
   try:p.wait(5)
   except subprocess.TimeoutExpired:p.kill();p.wait()
   p.stdout.close()
  for log in self.logs:log.close()
  self.processes=[];self.logs=[]

if __name__=='__main__':serve(sys.argv[1],sys.argv[2])
