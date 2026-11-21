"""Candidate worker->PDS binary pair boundary; Request handles metadata only.
No default listener/auth grants or filesystem paths. Processor must be the
server-owned original pair validator/finalizer with PDS-owned staging.
"""
import hashlib,json,struct
from http.server import BaseHTTPRequestHandler
from .filex_read import canonical,digest,require
from .media_validation import png
PNG_FILES=('source.png','output.png')
JSON_FILES=('provider-claim.json','result.json')
MAX_META=2*1024*1024;MAX_PNG=32*1024*1024;MAX_BODY=4+MAX_META+2*MAX_PNG
SCOPE=('requestId','attemptId','fencingToken','capability','projectId','targetId','expectedRevision','requestHash')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def encode(metadata,files):
 raw=canonical(metadata);require(len(raw)<=MAX_META,'POSE_METADATA_LIMIT');return struct.pack('>I',len(raw))+raw+b''.join(files[n]for n in PNG_FILES)
def decode(body):
 require(isinstance(body,bytes)and 4<len(body)<=MAX_BODY,'POSE_BODY_LIMIT');n=struct.unpack('>I',body[:4])[0];require(0<n<=MAX_META and n+4<=len(body),'POSE_METADATA_LIMIT')
 m=json.loads(body[4:4+n]);require(isinstance(m,dict)and set(m)=={'claim','packet','metadataFiles'}and isinstance(m['claim'],dict)and isinstance(m['packet'],dict)and set(m['packet'])=={'manifest'},'POSE_UPLOAD_SCHEMA')
 manifest=m['packet']['manifest'];require(isinstance(manifest,dict)and isinstance(manifest.get('files'),dict)and set(manifest['files'])==set(PNG_FILES+JSON_FILES),'POSE_UPLOAD_FILES')
 require(isinstance(m['metadataFiles'],dict)and set(m['metadataFiles'])==set(JSON_FILES),'POSE_METADATA_FILES');files={};offset=4+n
 for name in PNG_FILES:
  entry=manifest['files'][name];require(isinstance(entry,dict)and set(entry)=={'bytes','sha256'}and type(entry['bytes'])is int and 0<entry['bytes']<=MAX_PNG,'POSE_FILE_SIZE');size=entry['bytes'];raw=body[offset:offset+size];offset+=size;require(len(raw)==size and sha(raw)==entry['sha256'],'POSE_FILE_HASH');png(raw);files[name]=raw
 require(offset==len(body),'POSE_TRAILING_BYTES')
 for name in JSON_FILES:
  value=m['metadataFiles'][name];require(isinstance(value,str),'POSE_JSON_TEXT');raw=value.encode('utf-8');require(len(raw)<=MAX_META and manifest['files'][name]=={'bytes':len(raw),'sha256':sha(raw)},'POSE_JSON_HASH');require(isinstance(json.loads(raw),dict),'POSE_JSON_OBJECT');files[name]=raw
 return m,files

class DirectPoseDirections:
 def __init__(self,authority,processor,readback,authenticated):
  require(all(callable(v)for v in (authority,processor,readback,authenticated)),'POSE_SERVER_PORTS_REQUIRED');self.authority,self.processor,self.readback,self.authenticated=authority,processor,readback,authenticated
 def reservation(self,claim,packet_hash):
  require(isinstance(claim,dict)and isinstance(packet_hash,str)and len(packet_hash)==64,'POSE_RESERVATION_INPUT')
  b=self.authority('POST','/v1/npc-pose-direction-reservations/read',{'claim':claim,'packetHash':packet_hash})
  require(isinstance(b,dict)and set(b)=={'reservation','record','parentGuard'},'POSE_RESERVATION_BINDING');r=b['reservation'];record=b['record'];g=b['parentGuard']
  require(isinstance(r,dict)and r.get('reserved')is True and r.get('publicationHash')==packet_hash and all(r.get(k)==claim.get(v)for k,v in (('requestId','request_id'),('attemptId','attempt_id'),('fencingToken','fencing_token'),('capability','capability'),('expectedRevision','expected_revision'))),'POSE_RESERVED_SCOPE')
  params=claim.get('parameters',{})
  require(r['capability']=='image.generate'and r.get('requestHash')==digest(params)and params.get('schemaVersion')=='ngs.pose-direction-request.v1','POSE_ORIGINAL_REQUEST')
  require(isinstance(record,dict)and set(record)=={'request','state','step'},'POSE_ORIGINAL_RECORD')
  request,state,step=record['request'],record['state'],record['step'];parent=params.get('parentRequestId');direction=params.get('direction')
  require(request.get('requestId')==parent and step.get('direction')==direction and claim['request_id']==parent+'.'+direction,'POSE_CHILD_BINDING')
  require(isinstance(g,dict)and set(g)=={'npcId','generationId','requestId','leaseToken'}and g['npcId']==request.get('npcId')==r.get('targetId')and g['requestId']==parent and r.get('projectId')==request.get('projectId')=='binari','POSE_ORIGINAL_GUARD')
  if getattr(self,"guard_ingress",False):
   from .direct_guard import verify_binding
   verify_binding(claim,b)
  return b
 def verify(self,value,claim,packet_hash):
  require(isinstance(value,dict)and set(value)=={'requestId','attemptId','fencingToken','packetHash','sourceReceipt','wire'}and all(value.get(k)==claim.get(v)for k,v in (('requestId','request_id'),('attemptId','attempt_id'),('fencingToken','fencing_token'))),'POSE_RECEIPT_SCOPE')
  require(value['packetHash']==packet_hash,'POSE_RECEIPT_PACKET');wire=value['wire'];receipt=value['sourceReceipt']
  require(isinstance(wire,dict)and wire.get('requestId')==claim['request_id']and all(isinstance(wire.get(k),dict)and 'base64'not in wire[k]for k in ('artifact','sourceArtifact')),'POSE_METADATA_WIRE')
  slot=wire['slot'];artifact=wire['artifact'];source=wire['sourceArtifact']
  expected=digest({k:v for k,v in wire.items()if k not in ('expectedSourceVersion','expectedCharacterHash')})
  fields={'requestId':claim['request_id'],'inputHash':expected,'npcId':wire['npcId'],'slotKey':'/'.join(slot[k]for k in ('posePreset','referenceType','direction')),'assetId':artifact['sha256']+'.png','sourceArtifactId':source['sha256']+'.png','sha256':artifact['sha256'],'bytes':artifact['bytes'],'verified':True,'source':'filex'}
  require(isinstance(receipt,dict)and all(receipt.get(k)==v for k,v in fields.items()),'POSE_SOURCE_RECEIPT');return value
 def read(self,p):
  require(isinstance(p,dict)and set(p)=={'claim','packetHash'},'POSE_READ_SCHEMA');self.reservation(p['claim'],p['packetHash']);return self.verify(self.readback(p['claim'],p['packetHash']),p['claim'],p['packetHash'])
 def publish(self,body):
  m,files=decode(body);h=digest(m['packet']);b=self.reservation(m['claim'],h)
  # No Request endpoint receives any PNG or local file path. The processor
  # revalidates prompt/reference/pixel/alpha/CAS using server original metadata.
  result=self.verify(self.processor(m['claim'],m['packet'],files,b),m['claim'],h)
  require(self.read({'claim':m['claim'],'packetHash':h})==result,'POSE_DURABLE_READBACK');return result

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
    require(not self.headers.get('Origin')and not self.headers.get('Transfer-Encoding'),'POSE_HTTP_HEADERS')
    publish=self.path=='/v1/npc-pose-directions/publish';require(publish or self.path=='/v1/npc-pose-directions/read','POSE_HTTP_PATH')
    require(self.headers.get('Content-Type')==('application/octet-stream'if publish else'application/json'),'POSE_CONTENT_TYPE')
    n=int(self.headers.get('Content-Length','0'));require(0<n<=(MAX_BODY if publish else MAX_META),'POSE_BODY_LIMIT');self.connection.settimeout(60);body=self.rfile.read(n);require(len(body)==n,'POSE_TRUNCATED');v=service.publish(body)if publish else service.read(json.loads(body));raw=canonical(v)
    self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
   except (ValueError,TypeError,KeyError):self.send_error(409)
   except Exception:self.send_error(503)
 return Handler
