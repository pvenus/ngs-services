import base64,copy,hashlib,json,struct,unittest,urllib.request,urllib.error,zlib
import test_npc_scoped_create as fixture
from project_data_service.filex_read import digest
from project_data_service.npc_ownership import request_scope

def png(color):
 def chunk(tag,raw):return struct.pack('>I',len(raw))+tag+raw+struct.pack('>I',zlib.crc32(tag+raw)&0xffffffff)
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0'+bytes(color)))+chunk(b'IEND',b'')
class Tests(unittest.TestCase):
 def setUp(self):
  fixture.Tests.setUp(self)
  generation=self.writer.bootstrap('character.old',self.writer.snapshot()['sourceVersion'])['generationId']
  self.guard=self.writer.admit('character.old',generation,'batch.portrait')
  self.headers={'Content-Type':'application/json','X-NPC-Ownership-Guard':base64.b64encode(json.dumps(self.guard).encode()).decode()}
  assets=[]
  with request_scope(self.headers):
   for color in ((255,0,0,255),(0,0,255,255)):
    raw=png(color);aid=hashlib.sha256(raw).hexdigest()+'.png';self.source.asset_put(aid,raw);assets.append(aid)
  patch={'portraits':[{'assetId':aid,'label':'fixture','sourceRef':'fixture'}for aid in assets],'activePortraitId':assets[0],'portraitSets':{'representative':{'portraitAssetId':assets[0],'pixelAssetId':assets[1],'referencePortraitAssetId':None,'requestId':'batch.portrait'}}}
  after=copy.deepcopy(self.doc);after['characters'][0].update(patch)
  self.body={'requestId':'batch.portrait','npcId':'character.old','posePreset':'representative','expectedRevision':1,'beforeDocumentHash':digest(self.doc),'documentHash':digest(after),'note':'fixture portrait pair','patch':patch};self.rehash(self.body)
 start=fixture.Tests.start
 def rehash(self,body):body['inputHash']=digest({k:v for k,v in body.items()if k!='inputHash'})
 def call(self,body=None,heads=None):
  raw=json.dumps(body or self.body).encode();self.assertLess(len(raw),65536)
  req=urllib.request.Request('http://127.0.0.1:'+str(self.pds.server_port)+'/api/npcs/publish-portrait',data=raw,headers=heads or self.headers)
  return json.load(urllib.request.urlopen(req))
 def test_target_metadata_only_and_exact_replay(self):
  before=self.writer.snapshot();r=self.call();self.assertTrue(r['verified']);self.assertEqual(r['documentHash'],self.body['documentHash'])
  after=self.writer.snapshot();changed={k for k in after['hashes']if after['hashes'][k]!=before['hashes'].get(k)}
  self.assertEqual(changed,{'metadata.json','character.old/metadata.json'})
  self.assertEqual(self.source.current()['document']['characters'][0]['largeExistingArtifact'],self.doc['characters'][0]['largeExistingArtifact'])
  self.assertEqual(self.call(),r)
  old=self.source._read;self.source._read=lambda:(_ for _ in ()).throw(AssertionError('replay source read'))
  try:self.assertEqual(self.call(),r)
  finally:self.source._read=old
 def test_commit_response_loss_replays_same_receipt(self):
  original=self.source.store.apply
  def lost(*a,**kw):original(*a,**kw);self.source.store.apply=original;raise OSError('fixture lost response')
  self.source.store.apply=lost
  with self.assertRaises(urllib.error.HTTPError):self.call()
  self.assertEqual(self.source.current()['revision'],2);self.assertEqual(self.call()['revision'],2)
 def test_wrong_actor_guard_hash_stale_and_unrelated_slots(self):
  for field in ('beforeDocumentHash','documentHash'):
   bad=copy.deepcopy(self.body);bad[field]='0'*64;self.rehash(bad)
   with self.assertRaises(urllib.error.HTTPError):self.call(bad)
  bad=copy.deepcopy(self.body);bad['patch']['portraitSets']['idle']=copy.deepcopy(bad['patch']['portraitSets']['representative']);self.rehash(bad)
  with self.assertRaises(urllib.error.HTTPError):self.call(bad)
  with self.assertRaises(urllib.error.HTTPError):self.call(heads={'Content-Type':'application/json'})
  self.assertEqual(self.source.current()['revision'],1)
 def test_forged_or_released_guard_cannot_publish(self):
  for change in ({'leaseToken':'0'*64},{'npcId':'character.other'}):
   guard={**self.guard,**change};heads={**self.headers,'X-NPC-Ownership-Guard':base64.b64encode(json.dumps(guard).encode()).decode()}
   with self.assertRaises(urllib.error.HTTPError):self.call(heads=heads)
  bad=copy.deepcopy(self.body);bad['requestId']='unrelated.stage';self.rehash(bad)
  with self.assertRaises(urllib.error.HTTPError):self.call(bad)
  self.pds.app.domains.npcs=self.source
  req=urllib.request.Request('http://127.0.0.1:'+str(self.pds.server_port)+'/v1/npcs/ownership/finish',data=json.dumps({'guard':self.guard}).encode(),headers={'Content-Type':'application/json'})
  self.assertTrue(json.load(urllib.request.urlopen(req))['released'])
  with self.assertRaises(urllib.error.HTTPError):self.call()
  self.assertEqual(self.source.current()['revision'],1)
 def test_idle_preserves_representative_and_active_slot(self):
  self.call();current=self.source.current();body=copy.deepcopy(self.body)
  body.update(posePreset='idle',expectedRevision=2,beforeDocumentHash=digest(current['document']))
  body['patch']['portraitSets']['idle']={**body['patch']['portraitSets']['representative'],'referencePortraitAssetId':body['patch']['activePortraitId']}
  after=copy.deepcopy(current['document']);after['characters'][0].update(body['patch']);body['documentHash']=digest(after);self.rehash(body)
  self.assertTrue(self.call(body)['verified'])
  npc=self.source.current()['document']['characters'][0]
  self.assertEqual(npc['portraitSets']['representative'],self.body['patch']['portraitSets']['representative'])
  self.assertEqual(npc['activePortraitId'],self.body['patch']['activePortraitId'])
 def test_same_request_cannot_publish_a_second_payload(self):
  self.call();current=self.source.current();bad=copy.deepcopy(self.body)
  bad.update(expectedRevision=2,beforeDocumentHash=digest(current['document']),note='second payload attempt')
  bad['patch']['portraits'][0]['label']='changed'
  after=copy.deepcopy(current['document']);after['characters'][0].update(bad['patch']);bad['documentHash']=digest(after);self.rehash(bad)
  with self.assertRaises(urllib.error.HTTPError):self.call(bad)
  self.assertEqual(self.source.current()['revision'],2)
 def test_legacy_missing_portrait_sets_stays_absent(self):
  body=copy.deepcopy(self.body);body['posePreset']=None;body['patch'].pop('portraitSets')
  body['patch']['portraits']=body['patch']['portraits'][:1]
  after=copy.deepcopy(self.doc);after['characters'][0].update(body['patch']);body['documentHash']=digest(after);self.rehash(body)
  self.assertTrue(self.call(body)['verified']);self.assertNotIn('portraitSets',self.source.current()['document']['characters'][0])
