"""Canonical PNG registration through the existing fenced Filex NPC authority."""
import hashlib,hmac
from .filex_read import digest,require
from .pds_owned_transport import OwnedNPCTransport

class PortraitRegistrar:
 def __init__(self,source,token):
  require(isinstance(token,str)and len(token)>=32,'PORTRAIT_SERVICE_TOKEN');self.source,self.token=source,token
 def authenticated(self,header):return hmac.compare_digest(header or '','Bearer '+self.token)
 def verify(self,r):
  require(isinstance(r,dict)and r.get('schemaVersion')=='pds.portrait-asset.v1'and r.get('verified')is True and r.get('authority')=='filex','PORTRAIT_RECEIPT')
  require(digest({k:v for k,v in r.items()if k!='receiptHash'})==r.get('receiptHash'),'PORTRAIT_RECEIPT_HASH')
  a=r['asset'];data=self.source.asset_get(a['canonicalAssetId'])
  require(hashlib.sha256(data).hexdigest()==a['sha256']and len(data)==a['size']and a['canonicalAssetId']==a['sha256']+'.png','PORTRAIT_READBACK');return r
 def get(self,aid):
  with self.source.store.factory()as t:
   require(isinstance(t,OwnedNPCTransport),'PORTRAIT_FENCED_WRITER_REQUIRED');r=t.portrait_asset(aid)
  return None if r is None else self.verify(r)
 def register(self,meta,data):
  from .media_validation import png
  png(data)
  with self.source.store.factory()as t:
   require(isinstance(t,OwnedNPCTransport),'PORTRAIT_FENCED_WRITER_REQUIRED');r=t.register_portrait_asset(meta,data)
  self.verify(r)
  require(r['asset']['sha256']==hashlib.sha256(data).hexdigest()and r['asset']['size']==len(data),'PORTRAIT_RESULT_BINDING')
  require(self.get(r['asset']['id'])==r,'PORTRAIT_DURABLE_READBACK');return r
