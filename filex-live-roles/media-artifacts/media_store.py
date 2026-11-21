"""Immutable request-attempt media originals. No NPC slot or music catalog mutation.
Root is a dedicated deployment binding, never supplied by a worker.
"""
import base64, hashlib, io, json, re, struct, wave, zlib
from server import Store, Conflict

from media_validation import SCOPE,wire,sha,require,validate

class MediaStore(Store):
 namespace='request-media-artifacts.v1'
 def internal(self,*parts):
  p=self.root
  for part in parts:
   p=p/part;require(not p.is_symlink(),'media symlink')
  return p
 def path(self,key):raise ValueError('generic media access forbidden')
 def snapshot(self):raise ValueError('generic media access forbidden')
 def cas(self,*a,**kw):raise ValueError('generic media access forbidden')
 def rid(self,request,attempt):
  require(all(isinstance(x,str)and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]{0,199}',x)for x in (request,attempt)),'media receipt ids')
  return sha(wire([request,attempt]))
 def media_receipt(self,request,attempt):
  with self.lock():return self.read_receipt(request,attempt)
 def read_receipt(self,request,attempt):
  p=self.internal('receipts',self.rid(request,attempt)+'.json');r=json.loads(p.read_bytes());h=r.pop('receiptHash');require(sha(wire(r))==h,'media receipt integrity');r['receiptHash']=h
  require(r['requestId']==request and r['attemptId']==attempt,'media receipt binding');self.read_asset(r['artifact']['assetId']);return r
 def read_asset(self,asset):
  require(isinstance(asset,str)and re.fullmatch('[a-f0-9]{64}\\.(wav|png)',asset),'media asset id')
  data=self.internal('assets',asset).read_bytes();require(sha(data)==asset.split('.')[0],'media asset integrity')
  return {'assetId':asset,'sha256':sha(data),'bytes':len(data),'base64':base64.b64encode(data).decode()}
 def media_asset(self,asset):
  with self.lock():return self.read_asset(asset)
 def media_publish(self,p):
  data,ext=validate(p);identity=sha(wire(p));rid=self.rid(p['requestId'],p['attemptId'])
  with self.lock():
   rp=self.internal('receipts',rid+'.json')
   if rp.exists():
    r=self.read_receipt(p['requestId'],p['attemptId'])
    if r['publicationHash']!=identity:raise Conflict('media idempotency conflict')
    return r
   # Intent persists before bytes. Interrupted attempts can only replay exact bytes.
   intent=self.internal('intents',rid+'.json')
   if intent.exists():
    if json.loads(intent.read_bytes())!={'publicationHash':identity}:raise Conflict('media intent conflict')
   else:self.write(intent,wire({'publicationHash':identity}))
   asset=p['artifact']['sha256']+'.'+ext;ap=self.internal('assets',asset)
   if ap.exists():require(ap.read_bytes()==data,'media asset collision')
   else:self.write(ap,data)
   require(ap.read_bytes()==data,'media readback')
   r={k:p[k]for k in SCOPE};r.update(schemaVersion='pds.media-artifact-receipt.v1',status='committed',authority='filex',verified=True,publicationHash=identity,artifact={k:v for k,v in p['artifact'].items()if k!='base64'},provenance=p['provenance']);r['artifact']['assetId']=asset;r['receiptHash']=sha(wire(r));self.write(rp,wire(r));return self.read_receipt(p['requestId'],p['attemptId'])
