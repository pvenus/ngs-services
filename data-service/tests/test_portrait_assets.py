import base64,copy,hashlib,json,unittest,urllib.request,urllib.error
import test_npc_scoped_create as fixture
from test_npc_scoped_portrait import png
from project_data_service.portrait_assets import PortraitRegistrar
from project_data_service.filex_read import digest
class Tests(unittest.TestCase):
 def setUp(self):
  fixture.Tests.setUp(self)
  generation=self.writer.bootstrap('character.old',self.writer.snapshot()['sourceVersion'])['generationId']
  self.guard=self.writer.admit('character.old',generation,'parent.pair')
  self.pds.portrait_registrar=PortraitRegistrar(self.source,'s'*40)
  self.data=png((255,0,0,255));self.meta={'runId':'child.pair','projectId':'ProjectBS','sha256':hashlib.sha256(self.data).hexdigest(),'size':len(self.data),'provenance':{'model':'native'}}
 start=fixture.Tests.start
 def call(self,meta=None,data=None,guard=None,auth=True):
  h={'Content-Type':'image/png','X-Portrait-Metadata':base64.b64encode(json.dumps(meta or self.meta).encode()).decode(),'X-NPC-Ownership-Guard':base64.b64encode(json.dumps(guard or self.guard).encode()).decode(),'X-NPC-Stage-Request-Id':'child.pair'}
  if auth:h['Authorization']='Bearer '+'s'*40
  req=urllib.request.Request('http://127.0.0.1:'+str(self.pds.server_port)+'/v1/portrait-assets/register',data=self.data if data is None else data,headers=h)
  return json.load(urllib.request.urlopen(req))
 def test_raw_register_replay_existing_canonical_readback(self):
  before=self.writer.snapshot();r=self.call();self.assertEqual(self.call(),r)
  a=r['asset'];self.assertEqual(a['id'],'asset.'+hashlib.sha256(b'binari:child.pair').hexdigest()[:48]);self.assertEqual(self.source.asset_get(a['canonicalAssetId']),self.data)
  req=urllib.request.Request('http://127.0.0.1:'+str(self.pds.server_port)+a['downloadPath'],headers={'Authorization':'Bearer '+'s'*40})
  self.assertEqual(urllib.request.urlopen(req).read(),self.data);self.assertEqual(before,self.writer.snapshot())
  self.assertFalse((self.root/'second-originals').exists())
 def test_alias_lookup_and_cas_moves_same_bytes_to_owned_slot(self):
  from project_data_service.npc_ownership import request_scope
  r=self.call();a=r['asset'];base='http://127.0.0.1:'+str(self.pds.server_port)
  self.assertEqual(json.load(urllib.request.urlopen(base+'/api/assets/'+a['id'])),a)
  pd=png((0,0,255,255));pa=self.call(dict(self.meta,runId='child.pair.pixel',sha256=hashlib.sha256(pd).hexdigest(),size=len(pd)),pd)['asset']
  patch={'portraits':[{'assetId':v['canonicalAssetId'],'label':'fixture','sourceRef':'File:'+v['id']}for v in (a,pa)],'activePortraitId':a['canonicalAssetId'],'portraitSets':{'representative':{'portraitAssetId':a['canonicalAssetId'],'pixelAssetId':pa['canonicalAssetId'],'referencePortraitAssetId':None,'requestId':'child.pair'}}}
  after=copy.deepcopy(self.doc);after['characters'][0].update(patch)
  body={'requestId':'child.pair','npcId':'character.old','posePreset':'representative','expectedRevision':1,'beforeDocumentHash':digest(self.doc),'documentHash':digest(after),'note':'fixture pair publication','patch':patch};body['inputHash']=digest(body)
  h={'Content-Type':'application/json','X-NPC-Ownership-Guard':base64.b64encode(json.dumps(self.guard).encode()).decode(),'X-NPC-Stage-Request-Id':'child.pair'}
  try:saved=json.load(urllib.request.urlopen(urllib.request.Request(base+'/api/npcs/publish-portrait',data=json.dumps(body).encode(),headers=h)))
  except urllib.error.HTTPError as exc:raise AssertionError(exc.read().decode())from exc
  self.assertTrue(saved['verified']);self.writer.finish(self.guard)
  cid=saved['publicationReceipt']['requestId']
  rr=json.load(urllib.request.urlopen(urllib.request.Request(base+'/v1/npc-portrait-pairs/receipts/'+cid,headers={'Authorization':'Bearer '+'s'*40})))
  self.assertEqual(rr,saved['publicationReceipt']);self.assertEqual(self.pds.portrait_registrar.get(a['id']),r)
  self.assertFalse((self.root/'data/.pending-assets'/a['canonicalAssetId']).exists())
 def test_hash_and_auth_and_stage_rejected(self):
  for kw in ({'auth':False},{'meta':dict(self.meta,sha256='0'*64)},{'meta':dict(self.meta,runId='another')},{'data':b'invalid'}):
   with self.assertRaises(urllib.error.HTTPError):self.call(**kw)
 def test_same_run_new_bytes_conflict(self):
  self.call();data=png((0,255,0,255));meta=dict(self.meta,size=len(data),sha256=hashlib.sha256(data).hexdigest())
  with self.assertRaises(urllib.error.HTTPError):self.call(meta,data)
 def test_stale_guard_cannot_write_but_read_remains(self):
  r=self.call();self.writer.finish(self.guard)
  with self.assertRaises(urllib.error.HTTPError):self.call()
  self.assertEqual(self.pds.portrait_registrar.get(r['asset']['id']),r)
 def test_interrupted_receipt_pins_intent_and_exact_retry_recovers(self):
  write=self.writer.write
  def fail_receipt(path,data):
   if path.parent.name=='.portrait-assets'and path.suffix=='.json':raise OSError('fixture interrupted')
   return write(path,data)
  self.writer.write=fail_receipt
  try:
   with self.assertRaises(urllib.error.HTTPError)as error:self.call()
   self.assertEqual(error.exception.code,503)
  finally:self.writer.write=write
  data=png((0,255,0,255));meta=dict(self.meta,size=len(data),sha256=hashlib.sha256(data).hexdigest())
  with self.assertRaises(urllib.error.HTTPError):self.call(meta,data)
  self.assertTrue(self.call()['verified'])
 def test_bad_png_does_not_create_intent(self):
  data=b'bad';meta=dict(self.meta,sha256=hashlib.sha256(data).hexdigest(),size=len(data))
  with self.assertRaises(urllib.error.HTTPError):self.call(meta,data)
  self.assertFalse((self.root/'data/.portrait-assets').exists())
if __name__=='__main__':unittest.main()
