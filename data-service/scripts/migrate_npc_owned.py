"""Reviewed owned-slot migration via PDS only; default validates and prints plan.
No service/configuration changes, credential reads, legacy deletion, or raw Filex writes.
"""
import argparse,base64,hashlib,json,re,sys,urllib.request,urllib.parse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from project_data_service.filex_read import digest,require

def prepare_plan(preflight,request_id):
 require(preflight.get('schemaVersion')=='npc-owned-migration.preflight.v1' and preflight.get('blockers')==[],'MIGRATION_PREFLIGHT_BLOCKED')
 require(re.fullmatch(r'[A-Za-z0-9._:-]{1,180}',request_id),'MIGRATION_REQUEST_ID')
 original=dict(preflight);reviewed=original.pop('planHash')
 require(hashlib.sha256(json.dumps(original,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()==reviewed,'MIGRATION_REVIEWED_PLAN_HASH')
 inputs=preflight['importInputs'];assets=[]
 for aid,item in sorted(inputs.items()):
  require(re.fullmatch(r'[a-f0-9]{64}\.(png|jpg|webp|mp4)',aid) and item['sha256']==aid.split('.')[0],'MIGRATION_INPUT_ID')
  assets.append({'assetId':aid,'sha256':item['sha256']})
 plan={'npcId':preflight['npcId'],'requestId':request_id,'expectedSourceVersion':preflight['sourceVersion'],'changedDocuments':preflight['changedDocuments'],'importAssets':assets,'reviewedPlanHash':reviewed}
 plan['planHash']=digest(plan);return plan

def input_bytes(item):
 p=Path(item['path']);require(p.is_absolute() and p.resolve()==p and not p.is_symlink(),'MIGRATION_INPUT_PATH')
 data=p.read_bytes();require(hashlib.sha256(data).hexdigest()==item['sha256'] and len(data)==item['bytes'],'MIGRATION_INPUT_CHANGED');return data

class Client:
 def __init__(self,url):
  parsed=urllib.parse.urlsplit(url)
  require(parsed.scheme=='http' and parsed.hostname=='127.0.0.1' and parsed.port and not parsed.username and not parsed.password and parsed.path in ('','/') and not parsed.query and not parsed.fragment,'MIGRATION_LOOPBACK_PDS_ONLY')
  self.url=url.rstrip('/')
  class NoRedirect(urllib.request.HTTPRedirectHandler):
   def redirect_request(self,*args,**kwargs):raise ValueError('MIGRATION_REDIRECT_DENIED')
  self.op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
 def call(self,phase,payload):
  req=urllib.request.Request(self.url+'/v1/npcs/ownership/migration/'+phase,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
  with self.op.open(req,timeout=600) as r:return json.load(r)

def apply(client,plan,inputs):
 # Verify all imported bytes before creating a durable fence. Recheck at upload.
 for item in inputs.values():input_bytes(item)
 prepared=client.call('prepare',plan)
 token=prepared['token'];require(isinstance(token,str) and bool(token),'MIGRATION_TOKEN_MISSING')
 if prepared.get('state')!='committed':
  for aid,item in sorted(inputs.items()):
   data=input_bytes(item)
   result=client.call('asset',{'requestId':plan['requestId'],'token':token,'assetId':aid,'base64':base64.b64encode(data).decode()})
   require(result.get('sha256')==item['sha256'],'MIGRATION_UPLOAD_UNVERIFIED')
 receipt=client.call('commit',{'requestId':plan['requestId'],'token':token})
 require(receipt.get('status')=='committed' and receipt.get('backupVerified') is True and receipt.get('requestId')==plan['requestId'],'MIGRATION_COMMIT_UNVERIFIED')
 return receipt

def main():
 p=argparse.ArgumentParser();p.add_argument('--plan',type=Path,required=True);p.add_argument('--request-id',required=True);p.add_argument('--pds',default='http://127.0.0.1:8030');p.add_argument('--apply',action='store_true');p.add_argument('--reviewed-plan-hash');a=p.parse_args()
 preflight=json.loads(a.plan.read_bytes());plan=prepare_plan(preflight,a.request_id)
 for item in preflight['importInputs'].values():input_bytes(item)
 summary={'npcId':plan['npcId'],'requestId':plan['requestId'],'sourceVersion':plan['expectedSourceVersion'],'reviewedPlanHash':plan['reviewedPlanHash'],'wirePlanHash':plan['planHash'],'importCount':len(plan['importAssets']),'applied':False}
 if not a.apply:print(json.dumps(summary));return
 require(a.reviewed_plan_hash==plan['reviewedPlanHash'],'EXPLICIT_REVIEWED_PLAN_HASH_REQUIRED')
 result=apply(Client(a.pds),plan,preflight['importInputs'])
 # Never print the reservation credential returned by prepare.
 print(json.dumps({**summary,'applied':True,'receipt':{k:v for k,v in result.items()if k!='token'}}))
if __name__=='__main__':main()
