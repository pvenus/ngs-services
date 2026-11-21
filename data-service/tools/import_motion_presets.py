"""One-time explicit source migration through PDS; original files are never changed.
Prepare JSON requests first; --apply uses approved running loopback PDS only.
"""
import argparse,base64,hashlib,json,sys,urllib.request
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--policy-file',required=True);p.add_argument('--output',required=True);p.add_argument('--apply',action='store_true');p.add_argument('--pds-port',type=int,default=8030);a=p.parse_args()
m=json.loads(Path(a.manifest).read_text());policies=json.loads(Path(a.policy_file).read_text());out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
for policy in policies:
 source=next(x for x in m['presets']if x['id']==policy['sourcePresetId']);assets={};rows=[]
 for row in source['directions']:
  raw=Path(row['videoPath']).read_bytes();h=hashlib.sha256(raw).hexdigest()
  if row.get('videoSha256',row.get('sha256',h))!=h:raise ValueError('source MP4 hash changed')
  aid=h+'.mp4';assets[aid]=base64.b64encode(raw).decode();rows.append({'direction':row['direction'],'assetId':aid,'sha256':h,'bytes':len(raw),'frameCount':row['frameCount'],'fps':row['fps'],'cycleCount':row['cycleCount']})
 preset={'schemaVersion':'ngs.motion-preset.v1','presetId':policy['presetId'],'posePreset':source['posePreset'],'label':policy['label'],'policy':policy['policy'],'directions':rows}
 req={'requestId':'motion.migration.'+hashlib.sha256(json.dumps(preset,sort_keys=True).encode()).hexdigest(),'expectedHash':None,'preset':preset,'assets':assets}
 target=out/(policy['presetId']+'.json');target.write_text(json.dumps(req,ensure_ascii=False));target.chmod(0o600)
 if a.apply:
  class NoRedirect(urllib.request.HTTPRedirectHandler):
   def redirect_request(self,*args,**kwargs):raise ValueError('migration redirect denied')
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect());request=urllib.request.Request('http://127.0.0.1:'+str(a.pds_port)+'/v1/motion-presets/register',data=json.dumps(req).encode(),headers={'Content-Type':'application/json'})
  with opener.open(request,timeout=120)as response:print(policy['presetId'],response.status,response.read().decode())
 else:print(policy['presetId'],'prepared',len(rows),'directions')
