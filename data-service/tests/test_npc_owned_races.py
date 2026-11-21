"""Synthetic deterministic race acceptance; no models or operational content."""
import base64,copy,hashlib,io,json,pathlib,struct,subprocess,sys,threading,unittest,zlib
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from test_npc_owned_authority import Tests as Fixture
from project_data_service.http_server import Handler
from project_data_service.domain_contracts.legacy_http import Handler as DomainHandler
from project_data_service.npc_ownership import request_scope,make_transport
from project_data_service.filex_read import digest
from server import Conflict

def png(color):
 def chunk(k,b):return struct.pack('>I',len(b))+k+b+struct.pack('>I',zlib.crc32(k+b)&0xffffffff)
 raw=b''.join(b'\0'+bytes(color)*8 for _ in range(8))
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',8,8,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')

CHILD=r'''
import hashlib,json,pathlib,struct,sys,urllib.request,zlib
# No producer path, Filex path or writer credential is received.
b=urllib.request.urlopen(sys.argv[1]).read();assert hashlib.sha256(b).hexdigest()==sys.argv[2]
pathlib.Path('input.png').write_bytes(b);print('downloaded',flush=True)
assert sys.stdin.readline().strip()=='finish'
assert b[:8]==b'\x89PNG\r\n\x1a\n';pos=8;raw=b''
while pos<len(b):
 n=struct.unpack('>I',b[pos:pos+4])[0];kind=b[pos+4:pos+8];data=b[pos+8:pos+8+n];pos+=12+n
 if kind==b'IHDR':assert struct.unpack('>IIBBBBB',data)==(8,8,8,6,0,0,0)
 if kind==b'IDAT':raw+=data
raw=zlib.decompress(raw);assert len(raw)==8*33
# Deterministic half-resolution nearest-neighbor RGBA fixture worker.
out=b''
for y in range(0,8,2):
 row=raw[y*33:(y+1)*33];assert row[0]==0
 out+=b'\0'+b''.join(row[1+x*4:1+(x+1)*4] for x in range(0,8,2))
def chunk(k,b):return struct.pack('>I',len(b))+k+b+struct.pack('>I',zlib.crc32(k+b)&0xffffffff)
result=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',4,4,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(out))+chunk(b'IEND',b'')
pathlib.Path('output.png').write_bytes(result);print(json.dumps({'inputHash':hashlib.sha256(b).hexdigest(),'outputHash':hashlib.sha256(result).hexdigest(),'width':4,'height':4}),flush=True)
'''
class Races(unittest.TestCase):
 setUp=Fixture.setUp
 tearDown=Fixture.tearDown
 headers=Fixture.headers
 def seed(self):
  b=png((100,50,200,127));aid=hashlib.sha256(b).hexdigest()+'.png'
  with request_scope(self.headers()):
   self.source.asset_put(aid,b);doc=copy.deepcopy(self.doc);doc['characters'][0]['portraitSets']={'combat':{'pixelAssetId':aid}}
   saved=self.source.save(1,'initial pixel fixture',doc)
  return aid,saved
 def child(self,aid):
  http=ThreadingHTTPServer(('127.0.0.1',0),Handler);http.app=SimpleNamespace(domains=SimpleNamespace(npcs=self.source,dispatch=lambda h:DomainHandler.route(h)));http.npc_store=self.source
  thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
  self.addCleanup(lambda:(http.shutdown(),http.server_close(),thread.join()))
  workspace=self.root/'independent-downstream';workspace.mkdir()
  child=subprocess.Popen([sys.executable,'-c',CHILD,'http://127.0.0.1:'+str(http.server_port)+'/assets/portraits/'+aid,aid[:-4]],cwd=workspace,env={'PATH':'/usr/bin:/bin'},stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  def reap_child():
   if child.poll() is None:child.kill()
   child.communicate(timeout=5)
  self.addCleanup(reap_child)
  self.assertEqual(child.stdout.readline().strip(),'downloaded')
  return child,workspace
 def finish_child(self,child,workspace):
  out,err=child.communicate('finish\n',timeout=10);self.assertEqual(child.returncode,0,err);trace=json.loads(out)
  self.assertEqual((trace['width'],trace['height']),(4,4));return (workspace/'output.png').read_bytes(),trace
 def test_late_downstream_cannot_replace_newer_slot(self):
  aid,pinned=self.seed();child,workspace=self.child(aid)
  replacement=png((10,20,30,255));newaid=hashlib.sha256(replacement).hexdigest()+'.png';newdoc=copy.deepcopy(pinned['document']);newdoc['characters'][0]['portraitSets']['combat']['pixelAssetId']=newaid
  with request_scope(self.headers('newer.stage')):
   self.source.asset_put(newaid,replacement);new=self.source.save(2,'newer pixel replacement',newdoc)
  result,trace=self.finish_child(child,workspace);self.assertEqual(trace['inputHash'],aid[:-4]);lateaid=hashlib.sha256(result).hexdigest()+'.png'
  late=copy.deepcopy(pinned['document']);late['characters'][0]['portraitSets']['combat']['pixelAssetId']=lateaid
  with request_scope(self.headers('late.stage')):
   self.source.asset_put(lateaid,result);self.assertIsNone(self.source.save(2,'late old-source result',late))
  current=self.source.current();self.assertEqual(current['revision'],new['revision']);self.assertEqual(current['document'],newdoc);self.assertEqual(self.source.asset_get(newaid),replacement)
 def test_active_delete_blocked_then_retired_result_cannot_resurrect(self):
  aid,pinned=self.seed();child,workspace=self.child(aid)
  with self.assertRaises(Conflict):self.writer.prepare_delete('npc',self.generation,'delete.race','f'*64)
  # Controlled terminal retirement, not TTL stealing: simulate a late already-retired completion.
  self.writer.finish(self.guard);self.writer.prepare_delete('npc',self.generation,'delete.race','f'*64)
  snap=self.writer.snapshot();meta=copy.deepcopy(snap['documents']['metadata.json']);meta.update(characterOrder=[],generationEpoch='delete.race');meta['_pdsCurrent']={'revision':3,'savedAt':'fixture','note':'delete','documentHash':digest({'characters':[],'generationEpoch':'delete.race'})}
  deleted=self.writer.delete_character('delete.race','h','npc',snap['sourceVersion'],meta,{'generationId':self.generation})
  current=self.source.current()
  self.assertEqual(current['document']['characters'],[])
  with request_scope(self.headers('late.before.restore')):
   with self.assertRaises(ValueError):self.source.asset_put(aid,png((100,50,200,127)))
   with self.assertRaises(ValueError):self.source.save(3,'late resurrection attempt',pinned['document'])
  self.assertEqual(self.source.current(),current)
  with make_transport(self.config) as t:g=t.prepare_restore('npc','restore.race',deleted['backupId'],current['sourceVersion'])
  with request_scope({'X-NPC-Ownership-Guard':base64.b64encode(json.dumps(g).encode()).decode()}):self.source.restore_character('restore.race','rh',deleted['backupId'],3)
  before=self.source.current();result,trace=self.finish_child(child,workspace);lateaid=hashlib.sha256(result).hexdigest()+'.png'
  with request_scope(self.headers('retired.late')):
   with self.assertRaises(ValueError):self.source.asset_put(lateaid,result)
   with self.assertRaises(ValueError):self.source.save(before['revision'],'retired generation write',before['document'])
  self.assertEqual(self.source.current(),before);self.assertNotEqual(self.writer.generation('npc')['generationId'],self.generation);self.assertEqual(self.source.asset_get(aid),png((100,50,200,127)))
del Fixture
if __name__=='__main__':unittest.main()
