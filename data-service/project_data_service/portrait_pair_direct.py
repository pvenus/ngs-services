"""Candidate worker->PDS binary pair boundary; Request handles metadata only.
No default listener/auth grants or filesystem paths. Processor must be the
server-owned original pair validator/finalizer with PDS-owned staging.
"""
import hashlib,json,struct
from http.server import BaseHTTPRequestHandler
from .filex_read import canonical,digest,require
from .media_validation import png
PNG_FILES=('output.png','pixel-output.png','pixel-source.png')
JSON_FILES=('provider-proof.json','pixel-claim.json','pixel-result.json')
MAX_META=2*1024*1024;MAX_PNG=32*1024*1024;MAX_BODY=4+MAX_META+3*MAX_PNG
SCOPE=('requestId','attemptId','fencingToken','capability','projectId','targetId','expectedRevision','requestHash')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def encode(metadata,files):
 raw=canonical(metadata);require(len(raw)<=MAX_META,'PAIR_METADATA_LIMIT');return struct.pack('>I',len(raw))+raw+b''.join(files[n]for n in PNG_FILES)
def decode(body):
 require(isinstance(body,bytes)and 4<len(body)<=MAX_BODY,'PAIR_BODY_LIMIT');n=struct.unpack('>I',body[:4])[0];require(0<n<=MAX_META and n+4<=len(body),'PAIR_METADATA_LIMIT')
 m=json.loads(body[4:4+n]);require(isinstance(m,dict)and set(m)=={'claim','packet','metadataFiles'}and isinstance(m['claim'],dict)and isinstance(m['packet'],dict)and set(m['packet'])=={'manifest'},'PAIR_UPLOAD_SCHEMA')
 manifest=m['packet']['manifest'];require(isinstance(manifest,dict)and isinstance(manifest.get('files'),dict)and set(manifest['files'])==set(PNG_FILES+JSON_FILES),'PAIR_UPLOAD_FILES')
 require(isinstance(m['metadataFiles'],dict)and set(m['metadataFiles'])==set(JSON_FILES),'PAIR_METADATA_FILES');files={};offset=4+n
 for name in PNG_FILES:
  entry=manifest['files'][name];require(isinstance(entry,dict)and set(entry)=={'bytes','sha256'}and type(entry['bytes'])is int and 0<entry['bytes']<=MAX_PNG,'PAIR_FILE_SIZE');size=entry['bytes'];raw=body[offset:offset+size];offset+=size;require(len(raw)==size and sha(raw)==entry['sha256'],'PAIR_FILE_HASH');png(raw);files[name]=raw
 require(offset==len(body),'PAIR_TRAILING_BYTES')
 for name in JSON_FILES:
  value=m['metadataFiles'][name];require(isinstance(value,str),'PAIR_JSON_TEXT');raw=value.encode('utf-8');require(len(raw)<=MAX_META and manifest['files'][name]=={'bytes':len(raw),'sha256':sha(raw)},'PAIR_JSON_HASH');require(isinstance(json.loads(raw),dict),'PAIR_JSON_OBJECT');files[name]=raw
 return m,files

class DirectPortraitPairs:
 def __init__(self,authority,processor,readback,authenticated):
  require(all(callable(v)for v in (authority,processor,readback,authenticated)),'PAIR_SERVER_PORTS_REQUIRED');self.authority,self.processor,self.readback,self.authenticated=authority,processor,readback,authenticated
 def reservation(self,claim,packet_hash):
  require(isinstance(claim,dict)and isinstance(packet_hash,str)and len(packet_hash)==64,'PAIR_RESERVATION_INPUT')
  b=self.authority('POST','/v1/npc-portrait-pair-reservations/read',{'claim':claim,'packetHash':packet_hash})
  require(isinstance(b,dict)and set(b)=={'reservation','record','parentGuard'},'PAIR_RESERVATION_BINDING');r=b['reservation'];record=b['record'];g=b['parentGuard']
  require(isinstance(r,dict)and r.get('reserved')is True and r.get('publicationHash')==packet_hash and all(r.get(k)==claim.get(v)for k,v in (('requestId','request_id'),('attemptId','attempt_id'),('fencingToken','fencing_token'),('capability','capability'),('expectedRevision','expected_revision'))),'PAIR_RESERVED_SCOPE')
  require(r['capability']=='image.generate'and record==claim.get('parameters')and r.get('requestHash')==digest(record)and record.get('schemaVersion')=='npc-workflow-portrait-single.v1','PAIR_ORIGINAL_RECORD')
  npc=record.get('generationRequest',record.get('request',{})).get('characterRef',{}).get('npcId')
  require(isinstance(g,dict)and set(g)=={'npcId','generationId','requestId','leaseToken'}and g['npcId']==npc==r.get('targetId')and g['requestId']==record.get('parentId')and r.get('projectId')=='binari','PAIR_ORIGINAL_GUARD')
  if getattr(self,"guard_ingress",False):
   from .direct_guard import verify_binding
   verify_binding(claim,b)
  return b
 def verify(self,value,claim,packet_hash):
  require(isinstance(value,dict)and set(value)=={'requestId','attemptId','fencingToken','packetHash','parentReceipt','canonicalReceipt'}and all(value.get(k)==claim.get(v)for k,v in (('requestId','request_id'),('attemptId','attempt_id'),('fencingToken','fencing_token'))),'PAIR_RECEIPT_SCOPE')
  require(value['packetHash']==packet_hash,'PAIR_RECEIPT_PACKET');p=value['parentReceipt'];c=value['canonicalReceipt']
  require(isinstance(p,dict)and p.get('sourceApplied')is True and p.get('pairReady')is True and p.get('publicationScope')=='registered-npc-portrait-pair'and p.get('publication',{}).get('authority')=='filex'and p.get('publication',{}).get('verified')is True,'PAIR_PARENT_RECEIPT')
  require(isinstance(c,dict)and c.get('status')=='committed'and c.get('resultMetadata',{}).get('scopedPortrait',{}).get('requestId')==claim['request_id'],'PAIR_FILEX_RECEIPT');return value
 def read(self,p):
  require(isinstance(p,dict)and set(p)=={'claim','packetHash'},'PAIR_READ_SCHEMA');self.reservation(p['claim'],p['packetHash']);return self.verify(self.readback(p['claim'],p['packetHash']),p['claim'],p['packetHash'])
 def publish(self,body):
  m,files=decode(body);h=digest(m['packet']);b=self.reservation(m['claim'],h)
  # No Request endpoint receives any PNG or local file path. The processor
  # revalidates prompt/reference/pixel/alpha/CAS using server original metadata.
  result=self.verify(self.processor(m['claim'],m['packet'],files,b),m['claim'],h)
  require(self.read({'claim':m['claim'],'packetHash':h})==result,'PAIR_DURABLE_READBACK');return result

def handler(service):
 service.guard_ingress=True
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*a):pass
  def do_POST(self):
   from .direct_guard import ingress
   try:
    with ingress(self):return self.guarded_post()
   except (ValueError,TypeError,KeyError):self.send_error(403)
  def guarded_post(self):
   try:
    require(not self.headers.get('Origin')and not self.headers.get('Transfer-Encoding'),'PAIR_HTTP_HEADERS')
    publish=self.path=='/v1/npc-portrait-pairs/publish';require(publish or self.path=='/v1/npc-portrait-pairs/read','PAIR_HTTP_PATH')
    require(self.headers.get('Content-Type')==('application/octet-stream'if publish else'application/json'),'PAIR_CONTENT_TYPE')
    n=int(self.headers.get('Content-Length','0'));require(0<n<=(MAX_BODY if publish else MAX_META),'PAIR_BODY_LIMIT');self.connection.settimeout(60);body=self.rfile.read(n);require(len(body)==n,'PAIR_TRUNCATED');v=service.publish(body)if publish else service.read(json.loads(body));raw=canonical(v)
    self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
   except (ValueError,TypeError,KeyError):self.send_error(409)
   except Exception:self.send_error(503)
 return Handler
