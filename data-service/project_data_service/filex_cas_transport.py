"""Authenticated HTTP-only bridge to the Filex-side single-current-file service."""
import base64,json,urllib.parse,urllib.request,urllib.error
from pathlib import Path
from .filex_read import private_read,canonical,require
CONFIG=Path.home()/'Library/Application Support/NPCGen/pds-filex-cas.json'
class AudioPrewriteRejected(ValueError):pass

class FilexCASTransport:
 def __init__(self,config=None):
  try:c=config if config is not None else json.loads(private_read(CONFIG))
  except FileNotFoundError:raise ValueError('KNOWLEDGE_ATOMIC_CAS_TRANSPORT_REQUIRED')from None
  require(c.get('schemaVersion')=='pds-filex-cas.v1'and c.get('projectId')=='binari'and c.get('enabled')is True,'FILEX_CAS_CONFIG_INVALID')
  self.endpoint=c['endpoint'].rstrip('/');u=urllib.parse.urlsplit(self.endpoint)
  require(u.scheme in ('http','https')and bool(u.hostname)and not u.username and not u.password and not u.path and not u.query and not u.fragment,'FILEX_CAS_ENDPOINT_INVALID')
  require(u.hostname in ('127.0.0.1','localhost','::1')or u.scheme=='https','FILEX_CAS_TLS_REQUIRED')
  self.token=private_read(c['tokenFile']).decode().strip();require(bool(self.token),'FILEX_CAS_TOKEN_REQUIRED')
  class NoRedirect(urllib.request.HTTPRedirectHandler):
   def redirect_request(self,*args,**kwargs):raise ValueError('FILEX_CAS_REDIRECT_DENIED')
  self.op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 def __enter__(self):return self
 def __exit__(self,*args):self.token=None
 def call(self,method,path,body=None,missing=False,*,timeout=60):
  headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'}
  try:
   with self.op.open(urllib.request.Request(self.endpoint+path,data=canonical(body)if body is not None else None,method=method,headers=headers),timeout=timeout)as r:
    require(r.status==200,'FILEX_CAS_HTTP_ERROR');asset_read=method=='GET'and path.startswith('/v1/asset?');raw=r.read()if asset_read else r.read(16*1024*1024+1)
  except urllib.error.HTTPError as e:
   if missing and e.code==404:return None
   if e.code==422 and method=='POST' and path in ('/v1/workflow/audio','/v1/workflow/audio/read'):
    try:failure=json.loads(e.read(4097))
    except (ValueError,OSError):failure=None
    if failure=={'error':'WORKFLOW_AUDIO_REJECTED_BEFORE_WRITE'}:raise AudioPrewriteRejected()from None
   if e.code==409:raise ValueError('KNOWLEDGE_SOURCE_CHANGED')from None
   raise ValueError('FILEX_CAS_HTTP_'+str(e.code))from None
  require(asset_read or len(raw)<=16*1024*1024,'FILEX_CAS_RESPONSE_LIMIT');return json.loads(raw)
 def capabilities(self):return self.call('GET','/v1/capabilities')
 def snapshot(self):return self.call('GET','/v1/snapshot')
 def receipt(self,request_id):return self.call('GET','/v1/requests/'+urllib.parse.quote(request_id,safe=''),missing=True)
 def compare_and_swap(self,key,expected,body,*,request_id,input_hash,expected_source_version):
  return self.call('POST','/v1/cas',{'key':key,'expectedBase64':base64.b64encode(expected).decode()if expected is not None else None,'base64':base64.b64encode(body).decode(),'requestId':request_id,'inputHash':input_hash,'expectedSourceVersion':expected_source_version})
