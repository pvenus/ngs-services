"""Opt-in PDS gateway for immutable Filex media publication.
No source-root access or destination paths are exposed to workers.
"""
import base64,hashlib,hmac,json,re,urllib.request,urllib.parse,urllib.error
from .filex_read import canonical,digest,private_read,require
SCOPE=('requestId','attemptId','fencingToken','capability','projectId','targetId','expectedRevision','requestHash')
MAX_BODY=46*1024*1024
class Transport:
 def __init__(self,endpoint,token_file):
  u=urllib.parse.urlsplit(endpoint)
  require(u.scheme in ('http','https')and u.hostname and not u.username and not u.password and u.path in ('','/')and not u.query and not u.fragment,'MEDIA_ENDPOINT')
  require(u.scheme=='https'or u.hostname in ('127.0.0.1','localhost','::1'),'MEDIA_TLS_REQUIRED')
  self.endpoint=endpoint.rstrip('/');self.token=private_read(token_file).decode().strip();require(len(self.token)>=32,'MEDIA_TOKEN')
  class NoRedirect(urllib.request.HTTPRedirectHandler):
   def redirect_request(self,*args,**kwargs):raise ValueError('MEDIA_REDIRECT_DENIED')
  self.op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 def __call__(self,method,path,body=None):
  try:
   req=urllib.request.Request(self.endpoint+path,data=None if body is None else canonical(body),method=method,headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
   with self.op.open(req,timeout=60)as r:
    require(r.status==200,'MEDIA_REMOTE_STATUS');raw=r.read(MAX_BODY+1)
   require(len(raw)<=MAX_BODY,'MEDIA_RESPONSE_LIMIT');return json.loads(raw)
  except urllib.error.HTTPError as e:
   if e.code==404:return None
   raise ValueError('MEDIA_REMOTE_'+str(e.code))from None
class MediaArtifacts:
 def __init__(self,authority,writer,ingress_token):
  require(isinstance(ingress_token,str)and len(ingress_token)>=32,'MEDIA_INGRESS_TOKEN')
  self.authority,self.writer,self.token=authority,writer,ingress_token
 @classmethod
 def configured(cls,path):
  c=json.loads(private_read(path));require(set(c)=={'schemaVersion','enabled','ingressTokenFile','authorityEndpoint','authorityTokenFile','writerEndpoint','writerTokenFile'}and c['schemaVersion']=='pds.media-artifacts.v1'and c['enabled']is True,'MEDIA_CONFIG')
  return cls(Transport(c['authorityEndpoint'],c['authorityTokenFile']),Transport(c['writerEndpoint'],c['writerTokenFile']),private_read(c['ingressTokenFile']).decode().strip())
 def authenticated(self,value):return hmac.compare_digest(value or '', 'Bearer '+self.token)
 def receipt_path(self,request,attempt):
  require(all(isinstance(x,str)and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]{0,199}',x)for x in (request,attempt)),'MEDIA_IDS')
  return '/v1/media-artifacts/receipts/'+request+'/'+attempt
 def verify(self,r,p=None):
  require(isinstance(r,dict)and r.get('schemaVersion')=='pds.media-artifact-receipt.v1'and r.get('status')=='committed'and r.get('authority')=='filex'and r.get('verified')is True,'MEDIA_RECEIPT')
  require(digest({k:v for k,v in r.items()if k!='receiptHash'})==r.get('receiptHash'),'MEDIA_RECEIPT_HASH')
  a=r['artifact'];require(set(a)=={'assetId','sha256','bytes','mimeType'}and a['assetId']==a['sha256']+('.wav'if a['mimeType']=='audio/wav'else '.png'),'MEDIA_RECEIPT_ASSET')
  if p is not None:
   require(all(r.get(k)==p[k]for k in SCOPE)and r.get('publicationHash')==digest(p)and r.get('provenance')==p['provenance']and all(a[k]==p['artifact'][k]for k in ('sha256','bytes','mimeType')),'MEDIA_RECEIPT_BINDING')
  result=self.asset(a['assetId']);require(result['sha256']==a['sha256']and result['bytes']==a['bytes'],'MEDIA_READBACK_BINDING');return r
 def asset(self,asset):
  require(isinstance(asset,str)and re.fullmatch('[a-f0-9]{64}\\.(wav|png)',asset),'MEDIA_ASSET_ID')
  r=self.writer('GET','/v1/media-artifacts/assets/'+asset);require(isinstance(r,dict)and r.get('assetId')==asset,'MEDIA_ASSET_MISSING')
  data=base64.b64decode(r['base64'],validate=True);require(0<len(data)<=32*1024*1024 and len(data)==r['bytes']and hashlib.sha256(data).hexdigest()==r['sha256']==asset.split('.')[0],'MEDIA_ASSET_HASH');return r
 def receipt(self,request,attempt):
  r=self.writer('GET',self.receipt_path(request,attempt))
  if r is None:return None
  require(r.get('requestId')==request and r.get('attemptId')==attempt,'MEDIA_RECEIPT_TARGET');return self.verify(r)
 def publish(self,p):
  require(isinstance(p,dict)and set(p)==set(SCOPE)|{'artifact','provenance','claim'},'MEDIA_SCHEMA')
  path=self.receipt_path(p['requestId'],p['attemptId']);a=p['artifact']
  require(isinstance(a,dict)and set(a)=={'sha256','bytes','mimeType','base64'},'MEDIA_ARTIFACT')
  require(type(a['bytes'])is int and 0<a['bytes']<=32*1024*1024 and isinstance(a['base64'],str)and len(a['base64'])<=44*1024*1024,'MEDIA_SIZE')
  data=base64.b64decode(a['base64'],validate=True);require(len(data)==a['bytes']and hashlib.sha256(data).hexdigest()==a['sha256'],'MEDIA_HASH')
  require((p['capability']=='music.generate'and a['mimeType']=='audio/wav')or(p['capability']in ('image.generate','image.edit')and a['mimeType']=='image/png'),'MEDIA_CAPABILITY')
  from .media_validation import validate
  validate(p) # Before reserving authority: malformed outputs cannot pin an attempt.
  publication=digest(p)
  reservation=self.authority('POST','/v1/publications/prepare',{'claim':p['claim'],'publicationHash':publication})
  require(isinstance(reservation,dict)and reservation.get('reserved')is True and reservation.get('publicationHash')==publication and all(reservation.get(k)==p[k]for k in SCOPE),'MEDIA_AUTHORITY_BINDING')
  r=self.writer('POST','/v1/media-artifacts/publish',p);self.verify(r,p)
  # Read exact durable receipt independently before releasing the reservation.
  require(self.writer('GET',path)==r,'MEDIA_RECEIPT_READBACK')
  done=self.authority('POST','/v1/publications/finish',{'claim':p['claim'],'publicationHash':publication,'receipt':r})
  require(isinstance(done,dict)and done.get('committed')is True and done.get('publicationHash')==publication and done.get('requestId')==p['requestId'],'MEDIA_FINISH_UNCONFIRMED')
  return r
