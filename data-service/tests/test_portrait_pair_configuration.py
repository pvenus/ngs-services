import copy,hashlib,json,unittest,urllib.request,urllib.error
import test_portrait_assets as fixture
import test_portrait_pair_direct as wire
from project_data_service.portrait_pair_configuration import Assets,Selection,owned_scope
from project_data_service.npc_ownership import GUARD
from project_data_service.filex_read import digest
class Tests(unittest.TestCase):
 setUp=fixture.Tests.setUp
 start=fixture.Tests.start
 def test_ports_actual_filex_cas(self):
  assets=Assets(self.pds.portrait_registrar,self.guard,'child.pair');port=Selection(self.source)
  with owned_scope(self.guard,'child.pair'):
   a=assets.register(self.data,'binari','child.pair',{'model':'native'});assets.verify(a,{'sha256':hashlib.sha256(self.data).hexdigest(),'size':len(self.data)})
   b=assets.register(fixture.png((0,0,255,255)),'binari','child.pair.pixel',{'model':'pixel'})
   self.assertEqual(port.register_portrait(self.data),a['canonicalAssetId'])
   after=copy.deepcopy(self.doc);after['characters'][0].update({'portraits':[{'assetId':x['canonicalAssetId'],'label':'fixture','sourceRef':'File:'+x['id']}for x in (a,b)],'activePortraitId':a['canonicalAssetId'],'portraitSets':{'representative':{'portraitAssetId':a['canonicalAssetId'],'pixelAssetId':b['canonicalAssetId'],'referencePortraitAssetId':None,'requestId':'child.pair'}}})
   plan={'document':after,'expectedRevision':1,'beforeDocumentHash':digest(self.doc),'documentHash':digest(after),'posePreset':'representative','note':'fixture pair publication'}
   r=port.publish_portrait(plan,'child.pair','character.old');self.assertEqual(r['document'],after);self.assertEqual(port.publish_portrait(plan,'child.pair','character.old'),r)
  self.assertIsNone(GUARD.get());self.assertEqual(assets.publication_receipt(r['publicationReceipt']['requestId']),r['publicationReceipt'])
 def test_actual_main_mux_opt_in_auth(self):
  w=wire.Tests();w.setUp();base='http://127.0.0.1:'+str(self.pds.server_port)
  def post(token='existing-image-ingress'):
   return urllib.request.urlopen(urllib.request.Request(base+'/v1/npc-portrait-pairs/publish',data=w.body,headers={'Authorization':'Bearer '+token,'Content-Type':'application/octet-stream',**({}if token=='wrong'else{'X-NPC-Ownership-Guard':__import__('base64').b64encode(json.dumps(w.binding['parentGuard']).encode()).decode(),'X-NPC-Stage-Request-Id':w.claim['request_id']})}))
  with self.assertRaises(urllib.error.HTTPError)as e:post()
  self.assertEqual(e.exception.code,503);self.pds.direct_portrait_pairs=w.service
  with self.assertRaises(urllib.error.HTTPError)as e:post('wrong')
  self.assertEqual(e.exception.code,403);self.assertFalse(w.calls)
  with post()as r:self.assertEqual(json.load(r),w.result)
 def test_actual_pinned_factory_and_bad_pin(self):
  import sys,importlib,tempfile,os
  from pathlib import Path
  from types import SimpleNamespace
  from project_data_service.portrait_pair_configuration import MODULES,configured
  sys.path[:0]=['/Users/fixture/Workspaces/NGS/staging/resident-handoff-runtime-20261118','/Users/fixture/Workspaces/Workers/staging/request-pair-direct-20261119']
  pins={}
  for name in MODULES:
   m=importlib.import_module(name);p=Path(m.__file__).resolve();pins[name]={'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
  with tempfile.TemporaryDirectory()as temp:
   root=Path(temp).resolve();cfg=root/'config.json';value={'schemaVersion':'pds.direct-portrait-pair.v1','enabled':True,'stagingRoot':str(root),'sourcePins':pins};cfg.write_text(json.dumps(value));cfg.chmod(0o600)
   media=SimpleNamespace(authority=lambda *a:None,authenticated=lambda h:h=='existing')
   service=configured(cfg,media=media,registrar=self.pds.portrait_registrar);self.assertTrue(service.authenticated('existing'))
   value['sourcePins'][MODULES[0]]['sha256']='0'*64;cfg.write_text(json.dumps(value))
   with self.assertRaises(ValueError):configured(cfg,media=media,registrar=self.pds.portrait_registrar)
 def test_scope_exception_restores_guard(self):
  with self.assertRaises(RuntimeError):
   with owned_scope(self.guard,'child.pair'):raise RuntimeError('fixture')
  self.assertIsNone(GUARD.get())
if __name__=='__main__':unittest.main()
