"""PDS-owned read-only Filex transport. Workers never receive its credentials or paths."""
import hashlib, http.cookiejar, json, os, re, stat, urllib.parse, urllib.request
from pathlib import Path

CONFIG=Path.home()/'Library/Application Support/NPCGen/pds-filex-source.json'
ORIGIN='http://127.0.0.1:5212'
PREFIX='published-files/binari/'
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value):return hashlib.sha256(canonical(value)).hexdigest()
def require(value,error):
 if not value:raise ValueError(error)
def private_read(path):
 p=Path(path);require(p.is_absolute() and p.resolve()==p and not p.is_symlink(),'FILEX_CONFIG_PATH')
 with os.fdopen(os.open(p,os.O_RDONLY|os.O_NOFOLLOW),'rb')as f:
  st=os.fstat(f.fileno());require(stat.S_ISREG(st.st_mode) and st.st_uid==os.getuid() and not st.st_mode&0o077,'FILEX_CONFIG_PRIVATE');return f.read()
class FilexRead:
 def __init__(self,config=None):
  c=config if config is not None else json.loads(private_read(CONFIG))
  require(c.get('schemaVersion')=='pds-filex-source.v1' and c.get('projectId')=='binari','FILEX_SOURCE_CONFIG')
  self.origin=c['endpoint'].rstrip('/');self.prefix=c['sourcePrefix'].rstrip('/')+'/'
  # Existing owner-local credential provider only. Remote credentials require a separate binding.
  require(self.origin==ORIGIN and c.get('credentialProvider')=='local-first-run','FILEX_CREDENTIAL_DESTINATION')
  require(self.prefix.startswith('Files://') and '..'not in self.prefix.split('/'),'FILEX_SOURCE_PREFIX')
  class NoRedirect(urllib.request.HTTPRedirectHandler):
   def redirect_request(self,*args,**kwargs):raise ValueError('FILEX_REDIRECT_DENIED')
  self.jar=http.cookiejar.CookieJar();self.token=None
  self.op=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(self.jar),NoRedirect())
 def call(self,method,path,body=None,limit=None):
  headers={'Origin':self.origin}
  if body is not None:headers['Content-Type']='application/json'
  if self.token:headers['Authorization']='Bearer '+self.token
  with self.op.open(urllib.request.Request(self.origin+path,data=body,headers=headers,method=method),timeout=30)as r:
   require(r.status==200,'FILEX_READ_FAILED');raw=r.read() if limit is None else r.read(limit+1)
  if limit is not None:require(len(raw)<=limit,'FILEX_SIZE_MISMATCH')
  return raw
 def __enter__(self):
  raw=private_read(Path.home()/'Workspaces/Filex/data/app/.first-run.txt').decode()
  email=re.search(r'^Email:\s+(.+)$',raw,re.M);password=re.search(r'^Password:\s+(.+)$',raw,re.M)
  require(email is not None and password is not None,'FILEX_EXISTING_CREDENTIALS_REQUIRED')
  result=json.loads(self.call('POST','/api/auth/login',canonical({'email':email.group(1).strip(),'password':password.group(1).strip()})))
  self.token=result.get('token');require(isinstance(self.token,str)and bool(self.token),'FILEX_LOGIN_FAILED');return self
 def __exit__(self,*args):
  try:
   if self.token:self.call('POST','/api/auth/logout',b'{}')
  finally:self.token=None;self.jar.clear()
 def get(self,key,limit=None):
  require(key.startswith((PREFIX,'canonical-files/binari/knowledge/')) and all(re.fullmatch('[A-Za-z0-9][A-Za-z0-9._-]*',p) and p not in ('.','..') for p in key.split('/')),'FILEX_READ_KEY')
  return self.call('GET','/api/files/manager?'+urllib.parse.urlencode({'action':'download','path':self.prefix+key}),limit=limit)
