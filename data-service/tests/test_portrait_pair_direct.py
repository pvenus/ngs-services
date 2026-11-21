import copy,hashlib,json,struct,threading,unittest,urllib.request,urllib.error,zlib
from http.server import ThreadingHTTPServer
from project_data_service.portrait_pair_direct import DirectPortraitPairs,encode,decode,handler,PNG_FILES,JSON_FILES
from project_data_service.filex_read import canonical,digest
def png():
 def chunk(k,b):return struct.pack('>I',len(b))+k+b+struct.pack('>I',zlib.crc32(k+b)&0xffffffff)
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\x11\x22\x33\xff'))+chunk(b'IEND',b'')
class Tests(unittest.TestCase):
 def setUp(self):
  self.files={n:png()for n in PNG_FILES}|{n:b'{"originalSpacing": true}'for n in JSON_FILES};record={'schemaVersion':'npc-workflow-portrait-single.v1','parentId':'parent','request':{'characterRef':{'npcId':'npc'}}}
  self.claim={'request_id':'child','attempt_id':'a1','fencing_token':'1','capability':'image.generate','expected_revision':'1','parameters':record};self.packet={'manifest':{'files':{n:{'sha256':hashlib.sha256(v).hexdigest(),'bytes':len(v)}for n,v in self.files.items()}}};self.meta={'claim':self.claim,'packet':self.packet,'metadataFiles':{n:self.files[n].decode()for n in JSON_FILES}};self.body=encode(self.meta,self.files);self.calls=[];self.processed=[]
  self.binding={'reservation':{'reserved':True,'publicationHash':digest(self.packet),'requestId':'child','attemptId':'a1','fencingToken':'1','capability':'image.generate','expectedRevision':'1','requestHash':digest(record),'targetId':'npc','projectId':'binari'},'record':record,'parentGuard':{'npcId':'npc','generationId':'a'*48,'requestId':'parent','leaseToken':'b'*64}}
  self.result={'requestId':'child','attemptId':'a1','fencingToken':'1','packetHash':digest(self.packet),'parentReceipt':{'sourceApplied':True,'pairReady':True,'publicationScope':'registered-npc-portrait-pair','publication':{'authority':'filex','verified':True}},'canonicalReceipt':{'status':'committed','resultMetadata':{'scopedPortrait':{'requestId':'child'}}}}
  def authority(method,path,p):self.calls.append((method,path,p));return self.binding
  def process(c,p,files,b):self.processed.append(files);return self.result
  self.service=DirectPortraitPairs(authority,process,lambda c,h:self.result,lambda h:h=='Bearer existing-image-ingress')
 def test_binary_roundtrip_metadata_only_authority(self):
  self.assertEqual(decode(self.body)[1],self.files);self.assertEqual(self.service.publish(self.body),self.result);self.assertEqual(self.processed,[self.files]);self.assertEqual(set(self.calls[0][2]),{'claim','packetHash'})
 def test_bad_bytes_paths_rejected_before_authority(self):
  for body in (self.body+b'x',self.body[:-1],self.body[:-1]+b'x'):
   with self.assertRaises(ValueError):self.service.publish(body)
  self.assertFalse(self.calls);m=copy.deepcopy(self.meta);m['metadataFiles']['../../escape']='{}'
  with self.assertRaises(ValueError):decode(encode(m,self.files))
 def test_wrong_scope_never_processes(self):
  self.binding['parentGuard']['npcId']='different'
  with self.assertRaises(ValueError):self.service.publish(self.body)
  self.assertFalse(self.processed)
 def test_actual_http_auth_publish_readback(self):
  server=ThreadingHTTPServer(('127.0.0.1',0),handler(self.service));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   def post(path,raw,content,token='existing-image-ingress'):
    req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+path,data=raw,headers={'Authorization':'Bearer '+token,'Content-Type':content,**({}if token=='wrong'else{'X-NPC-Ownership-Guard':__import__('base64').b64encode(json.dumps(self.binding['parentGuard']).encode()).decode(),'X-NPC-Stage-Request-Id':self.claim['request_id']})})
    with urllib.request.urlopen(req,timeout=3)as r:return json.load(r)
   with self.assertRaises(urllib.error.HTTPError):post('/v1/npc-portrait-pairs/publish',self.body,'application/octet-stream','wrong')
   self.assertFalse(self.calls);r=post('/v1/npc-portrait-pairs/publish',self.body,'application/octet-stream');self.assertEqual(r,post('/v1/npc-portrait-pairs/read',canonical({'claim':self.claim,'packetHash':digest(self.packet)}),'application/json'))
  finally:server.shutdown();server.server_close();thread.join()
