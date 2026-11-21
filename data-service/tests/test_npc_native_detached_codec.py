import hashlib,io,json,sys,tempfile,threading,unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen
from urllib.parse import urlencode

from project_data_service.domains import DomainModule
from project_data_service.filex_npc_authority import FilexCurrentNPC, migration_files
from project_data_service.filex_npc_files import PREFIX, changed_files, decode, encode
from project_data_service.filex_npc_store import FilexNPCStore
from project_data_service.filex_read import digest
from project_data_service.http_server import Handler
from project_data_service.pds_owned_transport import OwnedNPCTransport

RAW='npc/animation-sequences/idle/walk/n.json'
ALPHA='npc/animation-alpha/idle/walk/n.json'
PROCESSED='npc/animation-processed/idle/walk.json'
DIRECTIONAL='npc/animation-processed-directions/idle/walk/'
LEGACY='npc/animation-frames/idle/walk.json'
DIRECTIONS=('n','ne','e','se','s','sw','w','nw')


class Tests(unittest.TestCase):
 def test_aggregate_legacy_edit_preserves_detached_documents_and_cas(self):
  before=encode({'characters':[{'id':'npc','role':'old'}]})
  before.update({RAW:{'schemaVersion':'ngs.animation-native-png-sequence.v1'},ALPHA:{'schemaVersion':'ngs.animation-alpha-output.v1'},
                 PROCESSED:{'schemaVersion':'ngs.animation-pixel-set-candidate.v1'},
                 DIRECTIONAL+'n.json':{'schemaVersion':'ngs.animation-pixel-direction-candidate.v1'}})
  document=decode(before)
  self.assertEqual(document,{'characters':[{'id':'npc','role':'old'}]})
  document['characters'][0]['role']='new'
  after=encode(document)
  basis={k:digest(v)for k,v in before.items()}
  plan=changed_files(before,after,basis)
  self.assertEqual(plan['files'],[{'path':PREFIX+'npc/metadata.json','body':{'id':'npc','role':'new'},'expectedHash':basis['npc/metadata.json']}])
  for key in (RAW,ALPHA,PROCESSED,DIRECTIONAL+'n.json'):
   self.assertNotIn(key,after)
   self.assertEqual(plan['readSet'][PREFIX+key],basis[key])
   with self.assertRaisesRegex(ValueError,'DETACHED_ANIMATION_OWNER_WRITE_REQUIRED'):
    changed_files(before,after|{key:{'changed':True}},basis)
  with self.assertRaisesRegex(ValueError,'NPC_SOURCE_CHANGED'):
   changed_files(before,after,basis|{RAW:'0'*64})

 def test_only_exact_detached_canonical_paths(self):
  before=encode({'characters':[{'id':'npc'}]})
  for path in ('npc/animation-sequences/idle/../n.json','npc/animation-alpha/idle/walk/north.json',
               'npc/animation-alpha/custom/walk/n.json','npc/animation-alpha/idle/walk/n/extra.json',
               'npc/animation-alpha/idle/walk/n.bin','npc/animation-alpha-evil/idle/walk/n.json',
               'npc/animation-processed/idle/walk/half.json','npc/animation-processed/idle/../walk.json',
               DIRECTIONAL+'north.json',DIRECTIONAL+'n/extra.json',DIRECTIONAL+'../n.json'):
   all_docs=before|{path:{}}
   with self.subTest(path=path),self.assertRaisesRegex(ValueError,'NPC_FILE_PATH_INVALID'):
    changed_files(all_docs,all_docs,{k:digest(v)for k,v in all_docs.items()})

 def server(self,handler):
  server=ThreadingHTTPServer(('127.0.0.1',0),handler)
  t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
  def cleanup():
   server.shutdown();server.server_close();t.join()
  self.addCleanup(cleanup)
  return server,'http://127.0.0.1:'+str(server.server_port)

 def test_actual_pds_http_reads_around_all_native_commits_and_keeps_existing_legacy_slot(self):
  from PIL import Image
  writer=str(Path(__file__).resolve().parents[2]/'filex-writer')
  sys.path.insert(0,writer);self.addCleanup(sys.path.remove,writer)
  from fenced_store import FencedOwnedNPCStore
  from server import handler
  from npc_store import encoded
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);tmp=Path(t.name).resolve()
  root=tmp/'filex';root.mkdir()
  initial={'revision':7,'savedAt':'2026-11-19T00:00:00Z','note':'fixture','document':{'characters':[{'id':'npc','role':'fixture'}]}}
  for k,v in migration_files(initial).items():
   p=root/k;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(encoded(v))
  store=FencedOwnedNPCStore(root)
  generation=store.bootstrap('npc',store.snapshot()['sourceVersion'])['generationId']
  guard=store.admit('npc',generation,'parent.fixture')
  token=tmp/'fixture-token';token.write_text('e'*32);token.chmod(0o600)
  _,filex=self.server(handler(store,'e'*32))
  config=dict(schemaVersion='pds-filex-cas.v1',projectId='binari',scope='npc',enabled=True,ownershipMode='npc-owned-v1',endpoint=filex,tokenFile=str(token))
  domains=DomainModule.__new__(DomainModule)
  domains.lock=threading.RLock();domains.enabled=True
  domains.story=None;domains.npcs=FilexCurrentNPC(FilexNPCStore(lambda:OwnedNPCTransport(config)))
  pds,origin=self.server(Handler);pds.app=SimpleNamespace(domains=domains)
  def get(path):
   with urlopen(origin+path,timeout=5)as r:
    self.assertEqual(r.status,200)
    return json.load(r)
  self.assertEqual(get('/api/npcs')['document'],initial['document'])
  self.assertNotIn(RAW,get('/v1/npcs/files')['documents'])
  def png(color,size=(4,3)):
   with io.BytesIO()as o:
    Image.new('RGBA',size,color).save(o,'PNG');return o.getvalue()
  def asset(data):
   return hashlib.sha256(data).hexdigest()+'.png'
  # Seed an already-published legacy full frame set in the exact same preset.
  # Its old index and owned trio must be unchanged after native publication.
  old_bytes=png('green');old_asset=asset(old_bytes)
  legacy={'schemaVersion':'ngs.npc-animation-frame-set.v1','requestId':'legacy.full','posePreset':'idle','presetId':'walk',
          'directions':{'n':{'frameCount':1,'fps':24,'frames':[{'index':0,'assetId':old_asset,'rawAssetId':old_asset,'alphaAssetId':old_asset}]}}}
  meta_key=PREFIX+'npc/metadata.json';legacy_key=PREFIX+LEGACY
  meta={**store.snapshot()['documents']['npc/metadata.json'],'animationFrameSetKeys':['idle/walk']}
  prior=store.snapshot()['hashes']['npc/metadata.json']
  old_guard=store.admit('npc',generation,'legacy.parent')
  with OwnedNPCTransport(config,{**old_guard,'stageRequestId':'legacy.full'})as t:
   t.asset_put(old_asset,old_bytes)
   t.batch('legacy.full',digest(legacy),{'files':[{'path':legacy_key,'body':legacy,'expectedHash':None},
       {'path':meta_key,'body':meta,'expectedHash':prior}],'readSet':{legacy_key:None,meta_key:prior}})
  with OwnedNPCTransport(config,old_guard)as t:t.finish()
  legacy_doc=get('/api/npcs')['document']
  self.assertEqual(legacy_doc['characters'][0]['animationFrameSets'],{'idle/walk':legacy})
  def emit(rid,path,schema,alpha=False,raw_aid=None,fields=None):
   data=png('red'if alpha else'blue')
   aid=asset(data);h=aid[:-4]
   entries=[dict(index=i,**({'rawAssetId':raw_aid,'alphaAssetId':aid}if alpha else{'assetId':aid,'sha256':h,'mimeType':'image/png'}))for i in range(2)]
   body={'schemaVersion':schema,'frames':entries,'frameCount':2,**(fields or {})}
   key=PREFIX+path
   with OwnedNPCTransport(config,{**guard,'stageRequestId':rid})as t:
    t.asset_put(aid,data)
    r=t.batch(rid,digest(body),{'files':[{'path':key,'body':body,'expectedHash':None}],'readSet':{key:None}})
    self.assertEqual(t.receipt(rid),r)
   return aid,data,body
  raw_id,raw,raw_body=emit('raw.n',RAW,'ngs.animation-native-png-sequence.v1')
  self.assertEqual(get('/api/npcs')['document'],legacy_doc)
  self.assertEqual(get('/v1/npcs/files')['documents'][RAW],raw_body)
  alpha_id,alpha,alpha_body=emit('alpha.n',ALPHA,'ngs.animation-alpha-output.v1',True,raw_id)
  self.assertEqual(get('/api/npcs')['document'],legacy_doc)
  snapshot=get('/v1/npcs/files')
  self.assertEqual({k:snapshot['documents'][k]for k in (RAW,ALPHA)},{RAW:raw_body,ALPHA:alpha_body})
  pixels=png('yellow');pixel_id=asset(pixels)
  processed={'schemaVersion':'ngs.animation-pixel-set-candidate.v1','directions':{
   d:{'frameCount':2,'frames':[{'index':i,'assetId':pixel_id,'rawAssetId':raw_id,'alphaAssetId':alpha_id}for i in range(2)]}
   for d in DIRECTIONS}}
  # The native schema cannot be committed to the old, indexed legacy slot.
  with OwnedNPCTransport(config,{**guard,'stageRequestId':'pixel.wrong-slot'})as t:
   prev=store.snapshot()['hashes'][LEGACY]
   with self.assertRaisesRegex(ValueError,'FILEX_CAS_HTTP_4'):
    t.batch('pixel.wrong-slot',digest(processed),{'files':[{'path':legacy_key,'body':processed,'expectedHash':prev}],'readSet':{legacy_key:prev}})
  key=PREFIX+PROCESSED
  with OwnedNPCTransport(config,{**guard,'stageRequestId':'pixel.all'})as t:
   t.asset_put(pixel_id,pixels)
   r=t.batch('pixel.all',digest(processed),{'files':[{'path':key,'body':processed,'expectedHash':None}],'readSet':{key:None}})
   self.assertEqual(r['hashes'],{key:digest(processed)})
  self.assertEqual(get('/api/npcs')['document'],legacy_doc)
  snap=get('/v1/npcs/files')
  self.assertEqual(snap['documents'][LEGACY],legacy)
  self.assertEqual(snap['documents']['npc/metadata.json']['animationFrameSetKeys'],['idle/walk'])
  self.assertEqual(snap['documents'][PROCESSED],processed)
  self.assertEqual(snap['documents'][RAW],raw_body)
  self.assertEqual(snap['documents'][ALPHA],alpha_body)
  slots=store.manifests()['npc']['slots']
  self.assertEqual(slots['assets/animation-frames/idle/walk/n/pixel-0.png']['assetId'],old_asset)
  self.assertEqual(slots['assets/animation-sequences/idle/walk/n/raw-0.png']['assetId'],raw_id)
  self.assertEqual(slots['assets/animation-alpha/idle/walk/n/alpha-0.png']['assetId'],alpha_id)
  self.assertEqual({k:v['assetId']for k,v in slots.items()if k.startswith('assets/animation-processed/')},
                   {f'assets/animation-processed/idle/walk/{d}/pixel-{i}.png':pixel_id for d in DIRECTIONS for i in range(2)})
  for aid,expect in ((raw_id,raw),(alpha_id,alpha),(pixel_id,pixels),(old_asset,old_bytes)):
   with urlopen(origin+'/assets/portraits/'+aid,timeout=5)as r:self.assertEqual(r.read(),expect)
  lineage=get('/v1/npcs/ownership/publications?'+urlencode({'npcId':'npc','generationId':generation,'parentRequestId':'parent.fixture'}))
  self.assertEqual({r['stageRequestId'] for r in lineage['receipts']},{'raw.n','alpha.n','pixel.all'})

  # A directional candidate is a distinct, fenced slot. Its exact current
  # input direction is checked under the same write lock as the output CAS.
  def source(direction):
   fields=dict(npcId='npc',generationId=generation,posePreset='idle',presetId='walk',
               direction=direction,sourceBindingHash='b'*64,fps=24,width=4,height=3)
   raw_id,_,raw=emit('v2.raw.'+direction,RAW.replace('/n.json','/'+direction+'.json'),
       'ngs.animation-native-png-sequence.v1',fields=fields)
   alpha_id,_,alpha=emit('v2.alpha.'+direction,ALPHA.replace('/n.json','/'+direction+'.json'),
       'ngs.animation-alpha-output.v1',True,raw_id,fields={**fields,'rawManifestHash':digest(raw)})
   return raw_id,alpha_id,raw,alpha
  # NE uses the same pose/preset and is independent of the existing shared N output.
  raw_id,alpha_id,raw_basis,alpha_basis=source('ne')
  pixels=png('orange',(2,2)); pixel_asset=asset(pixels)
  r_id='animation.pixel-direction.fixture.ne'
  body=dict(schemaVersion='ngs.animation-pixel-direction-candidate.v1',requestId=r_id,npcId='npc',
      generationId=generation,posePreset='idle',presetId='walk',direction='ne',
      rawRetention='retain-native-sequence-independently',rawManifestHash=digest(raw_basis),
      alphaManifestHash=digest(alpha_basis),sourceBindingHash='b'*64,normalizationHash='c'*64,
      frameCount=2,fps=24,canvas=dict(width=2,height=2,sourceWidth=4,sourceHeight=3,
      sourceCanvasWidth=4,sourceCanvasHeight=3,paddingPixels=0,marginRatio=0.,scale=.5,scaleX=.5,
      scaleY=2/3,offsetX=0,offsetY=0,unionBounds=[0,0,4,3],pivot={'x':1,'y':1}),
      pixelPolicy=dict(pixelScale=.5,resizeBeforeQuantization=True,resizeFilter='BOX',colors=32,
      alphaThreshold=128,dither=False,paletteScope='preset-frozen-shared',palette=[10,20,30]*32),
      frames=[dict(index=i,sourceTimestamp=i/24,assetId=pixel_asset,rawAssetId=raw_id,
                   alphaAssetId=alpha_id,sourceBounds=[1,1,3,2])for i in range(2)])
  path=DIRECTIONAL+'ne.json'; key=PREFIX+path
  def payload(target,value):
   return {'files':[{'path':target,'body':value,'expectedHash':None}],'readSet':{target:None}}
  with OwnedNPCTransport(config,guard)as t:
   with self.assertRaisesRegex(ValueError,'FILEX_CAS_HTTP_4'):
    t.batch(r_id,digest(body),payload(key,body))
  with OwnedNPCTransport(config,{**guard,'stageRequestId':r_id})as t:
   t.asset_put(pixel_asset,pixels)
   for target,bad in ((key,body|{'rawManifestHash':'0'*64}),
                      (key,body|{'direction':'nw'}),
                      (key,body|{'canvas':body['canvas']|{'width':3}}),
                      (key,body|{'generationId':'foreign.generation'}),
                      (key,body|{'pixelPolicy':body['pixelPolicy']|{'paletteScope':'all-directions-all-frames'}}),
                      (PREFIX+DIRECTIONAL+'w.json',body),
                      (PREFIX+'npc/planning/directional.json',body)):
    with self.subTest(target=target),self.assertRaisesRegex(ValueError,'FILEX_CAS_HTTP_4'):
     t.batch(r_id,digest(bad),payload(target,bad))
   r=t.batch(r_id,digest(body),payload(key,body))
   self.assertEqual(r['hashes'],{key:digest(body)})
   self.assertEqual(t.batch(r_id,digest(body),payload(key,body)),r)
  self.assertEqual(get('/api/npcs')['document'],legacy_doc)
  snapshot=get('/v1/npcs/files')
  self.assertEqual({k:snapshot['documents'][k]for k in (LEGACY,PROCESSED,path)},
                   {LEGACY:legacy,PROCESSED:processed,path:body})
  slots=store.manifests()['npc']['slots']
  self.assertEqual(slots['assets/animation-processed/idle/walk/ne/pixel-0.png']['assetId'],pixel_id)
  self.assertEqual({k:v['assetId']for k,v in slots.items()if k.startswith('assets/animation-processed-directions/')},
                   {f'assets/animation-processed-directions/idle/walk/ne/pixel-{i}.png':pixel_asset for i in range(2)})
  with urlopen(origin+'/assets/portraits/'+pixel_asset,timeout=5)as response:self.assertEqual(response.read(),pixels)
  # A valid newer raw commit can change the source. Old exact receipt stays
  # replayable, but new write intent from its stale basis must be refused.
  raw_key=PREFIX+RAW.replace('/n.json','/ne.json')
  changed=raw_basis|{'requestId':'raw.ne.newer'}
  with OwnedNPCTransport(config,{**guard,'stageRequestId':'raw.ne.newer'})as t:
   old_hash=digest(raw_basis)
   t.batch('raw.ne.newer',digest(changed),{'files':[{'path':raw_key,'body':changed,'expectedHash':old_hash}],
                                        'readSet':{raw_key:old_hash}})
  with OwnedNPCTransport(config,{**guard,'stageRequestId':r_id})as t:
   self.assertEqual(t.batch(r_id,digest(body),payload(key,body)),r)
  with OwnedNPCTransport(config,{**guard,'stageRequestId':r_id+'.stale'})as t:
   stale=body|{'requestId':r_id+'.stale'}
   h=digest(body)
   with self.assertRaisesRegex(ValueError,'FILEX_CAS_HTTP_4'):
    t.batch(r_id+'.stale',digest(stale),{'files':[{'path':key,'body':stale,'expectedHash':h}],'readSet':{key:h}})
   self.assertIsNone(t.receipt(r_id+'.stale'))
  self.assertEqual(get('/v1/npcs/files')['documents'][path],body)
