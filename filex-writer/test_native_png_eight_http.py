"""Actual generic HTTP storage; synthetic images/masks, never a model claim."""
import io,json,os,sys,tempfile,threading,unittest,hashlib
from pathlib import Path
from PIL import Image
sys.path[:0]=[os.environ['WORKERS_NATIVE_PNG_SOURCE'],os.environ['NGS_NATIVE_PNG_SOURCE'],str(Path(__file__).resolve().parents[1]/'data-service')]
from workers.resident.backends.animation_native_png import build_native_png_manifest,DIRECTIONS
from workers.resident.backends.frame_native_manifests import build_alpha_manifest,build_pixel_manifest
from workers.resident.backends.frame_processing import process_set
from local.services.animation_requests.native_png_dag import alpha_dependency,palette_canvas_readiness_v1
from local.services.animation_requests.native_png_workflow import NativePNGWorkflow,BATCH_SCHEMA
from local.services.request_pipeline.common import CommonRequests
from project_data_service.pds_owned_transport import OwnedNPCTransport
from fenced_store import FencedOwnedNPCStore
from npc_store import encoded,NPC_PREFIX,digest
from server import handler
from http.server import ThreadingHTTPServer
class Tests(unittest.TestCase):
 def test_eight_directions_cross_executor_durable_chain(self):
  with tempfile.TemporaryDirectory()as temp:
   root=Path(temp).resolve();(root/'npc').mkdir();(root/'metadata.json').write_bytes(encoded({'characterOrder':['npc']}));(root/'npc/metadata.json').write_bytes(encoded({'id':'npc'}))
   store=FencedOwnedNPCStore(root);gen=store.bootstrap('npc',store.snapshot()['sourceVersion'])['generationId']
   token=root/'token';token.write_text('f'*32);token.chmod(0o600)
   server=ThreadingHTTPServer(('127.0.0.1',0),handler(store,'f'*32));thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   cfg=dict(schemaVersion='pds-filex-cas.v1',projectId='binari',scope='npc',enabled=True,ownershipMode='npc-owned-v1',endpoint='http://127.0.0.1:'+str(server.server_port),tokenFile=str(token))
   raws={};alphas={};raw_bytes={}
   def publish(rid,key,manifest,assets):
    with OwnedNPCTransport(cfg,dict(guard,stageRequestId=rid))as t:
     for aid,data in assets.items():t.asset_put(aid,data)
     result=t.batch(rid,digest(manifest),dict(files=[dict(path=key,expectedHash=None,body=manifest)],readSet={key:None}))
     self.assertEqual(t.receipt(rid),result)
     return result
   def read(key,rid):
    with OwnedNPCTransport(cfg)as t:return t.snapshot()['documents'][key.removeprefix(NPC_PREFIX)],t.receipt(rid)
   common=CommonRequests(root/'requests.sqlite')
   def read_raw(task):
    key=NPC_PREFIX+'npc/animation-sequences/idle/walk/'+task['direction']+'.json'
    with OwnedNPCTransport(cfg)as t:
     receipt=t.receipt(task['requestId'])
     if receipt is None:return None
     return dict(manifest=t.snapshot()['documents'][key.removeprefix(NPC_PREFIX)],receipt=receipt)
   def read_alpha(dep):
    key=NPC_PREFIX+'npc/animation-alpha/idle/walk/'+dep['direction']+'.json'
    with OwnedNPCTransport(cfg)as t:
     receipt=t.receipt(dep['requestId'])
     if receipt is None:return None
     return dict(manifestId=key,manifest=t.snapshot()['documents'][key.removeprefix(NPC_PREFIX)],receipt=receipt)
   workflow=NativePNGWorkflow(common,read_raw=read_raw,read_alpha=read_alpha);workflow.initialize()
   admission=workflow.admit('owner','isolated.eight',dict(schemaVersion=BATCH_SCHEMA,npcId='npc',generationId=gen,posePreset='idle',presetId='walk',policyVersion='shared.v1',sourceBindingHashes={d:hashlib.sha256(d.encode()).hexdigest()for d in DIRECTIONS}))
   parent=admission['requestId'];plan=workflow.select(parent,'batch');scope=plan['scope'];bindings=plan['bindings'];guard=store.admit('npc',gen,parent)
   self.assertEqual(workflow.tick(parent,'owner')['state'],'waiting')
   try:
    for number,d in enumerate(DIRECTIONS):
     bind=bindings[d];self.assertEqual(common.row(bind['requestId'])['state'],'queued')
     b={**{k:scope[k]for k in ('npcId','generationId','posePreset','presetId')},**bind}
     with tempfile.TemporaryDirectory()as producer:
      payloads=[]
      for i in range(2):
       out=io.BytesIO();Image.new('RGB',(16,16),(20+number*15,40+i*10,60)).save(out,format='PNG');data=out.getvalue();p=Path(producer)/f'{i}.png';p.write_bytes(data);payloads.append(p.read_bytes())
      m=build_native_png_manifest(b,payloads,frame_count=2,fps=24);key=NPC_PREFIX+'npc/animation-sequences/idle/walk/'+d+'.json'
      publish(bind['requestId'],key,m,{row['assetId']:data for row,data in zip(m['frames'],payloads)})
      raw_bytes.update({row['assetId']:data for row,data in zip(m['frames'],payloads)})
     self.assertFalse(Path(producer).exists());m,r=read(key,bind['requestId']);raws[d]={'manifest':m,'receipt':r};dep=alpha_dependency(scope,bind,m,r);workflow.tick(parent,'owner');self.assertEqual(workflow.select(dep['requestId'],'alpha'),dep)
     self.assertEqual(palette_canvas_readiness_v1(scope,bindings,raws,alphas)['state'],'waiting') if len(bindings)==8 else None
     with tempfile.TemporaryDirectory()as alpha_executor:
      raw=[];modified=[]
      with OwnedNPCTransport(cfg)as t:
       for entry in m['frames']:
        data=t.asset_get(entry['assetId']);raw.append(data);im=Image.open(io.BytesIO(data)).convert('RGBA');mask=Image.new('L',im.size,0);mask.paste(255,(3,3,13,13));im.putalpha(mask);out=io.BytesIO();im.save(out,format='PNG');modified.append(out.getvalue())
      a=build_alpha_manifest(m,raw,modified,dependency_hash=digest(dep),raw_manifest_id=key,alpha_request_id=dep['requestId'],inference=dict(engine='fixture',modelId='synthetic-mask',modelDigest='c'*64,policyHash='d'*64,inferenceRequestId=dep['requestId'],fixture=True))
      ak=NPC_PREFIX+'npc/animation-alpha/idle/walk/'+d+'.json';publish(dep['requestId'],ak,a,{entry['alphaAssetId']:data for entry,data in zip(a['frames'],modified)})
     self.assertFalse(Path(alpha_executor).exists());a,ar=read(ak,dep['requestId']);alphas[d]=dict(manifestId=ak,manifest=a,receipt=ar)
    ready=palette_canvas_readiness_v1(scope,bindings,raws,alphas);self.assertEqual(ready['state'],'ready');queued=workflow.tick(parent,'owner');self.assertEqual(queued['state'],'ready');pixel_id=queued['pixelRequestId'];self.assertEqual(common.row(pixel_id)['state'],'queued')
    with tempfile.TemporaryDirectory()as pixel_executor:
     folder=Path(pixel_executor);inputs={}
     with OwnedNPCTransport(cfg)as t:
      for d in DIRECTIONS:
       a=alphas[d]['manifest'];rawpaths=[];alphapaths=[]
       for i,f in enumerate(a['frames']):
        for field,paths in [('rawAssetId',rawpaths),('alphaAssetId',alphapaths)]:
         p=folder/(d+field+str(i)+'.png');p.write_bytes(t.asset_get(f[field]));paths.append(str(p))
       inputs[d]=dict(rawFrames=rawpaths,alphaFrames=alphapaths,fps=24,frameCount=2,sourceAssetId=raws[d]['manifest']['requestId'])
     result=process_set(inputs,folder/'output',pixel_scale=.5);final=build_pixel_manifest(result,{d:alphas[d]['manifest']for d in DIRECTIONS});fk=NPC_PREFIX+'npc/animation-processed/idle/walk.json'
     publish(pixel_id,fk,final,{sha+'.png':Path(path).read_bytes()for sha,path in result['artifacts'].items()})
    final,receipt=read(fk,pixel_id);self.assertEqual(final['pixelPolicy']['paletteScope'],'all-directions-all-frames');self.assertEqual(final['canvas']['scale'],.5)
    self.assertFalse(common.db.execute("SELECT 1 FROM common_requests WHERE worker IS NOT NULL OR fence != 0").fetchone())
    self.assertEqual(common.db.execute('SELECT count(*) FROM npc_native_png_queue').fetchone()[0],18)
    with OwnedNPCTransport(cfg)as t:
     for aid,data in raw_bytes.items():self.assertEqual(t.asset_get(aid),data)
     for row in final['directions'].values():
      for f in row['frames']:
       im=Image.open(io.BytesIO(t.asset_get(f['assetId'])));self.assertLessEqual(len(im.convert('RGB').getcolors(1<<24)),32);self.assertLessEqual(set(im.getchannel('A').getdata()),{0,255})
   finally:common.close();server.shutdown();server.server_close();thread.join()
