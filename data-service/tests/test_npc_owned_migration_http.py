"""Isolated PDS→authenticated writer migration; no operational data/config."""
import hashlib,importlib.util,json,pathlib,threading,unittest,urllib.request,urllib.error
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from test_npc_owned_authority import Tests as Base
from project_data_service.filex_read import digest
from project_data_service.http_server import Handler
from project_data_service.npc_ownership import make_transport
spec=importlib.util.spec_from_file_location('owned_cli',pathlib.Path(__file__).resolve().parents[1]/'scripts/migrate_npc_owned.py');cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)

class MigrationHTTP(Base):
 def setUp(self):
  super().setUp()
  self.pds=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.pds.app=SimpleNamespace(domains=SimpleNamespace(npcs=self.source));self.pt=threading.Thread(target=self.pds.serve_forever,daemon=True);self.pt.start();self.url='http://127.0.0.1:'+str(self.pds.server_port)
 def tearDown(self):self.pds.shutdown();self.pds.server_close();self.pt.join();super().tearDown()
 def plan(self):
  with make_transport(self.config)as t:t.guard=self.guard;t.finish()
  p={'npcId':'npc','requestId':'migration.http','expectedSourceVersion':self.writer.snapshot()['sourceVersion'],'changedDocuments':{},'importAssets':[]};p['planHash']=digest(p);return p
 def test_migration_http_replay_and_backup(self):
  client=cli.Client(self.url);plan=self.plan();receipt=cli.apply(client,plan,{})
  self.assertTrue(receipt['backupVerified']);self.assertEqual(receipt['status'],'committed');self.assertEqual(cli.apply(client,plan,{}),receipt)
  self.assertTrue((self.data/'npc/.owned-assets.json').exists());self.assertEqual(self.source.current()['revision'],2)
  status=json.load(urllib.request.urlopen(self.url+'/v1/npcs/ownership/migration/status?requestId=migration.http'));self.assertEqual(status['state'],'committed');self.assertNotIn('token',status);self.assertNotIn('plan',status)
 def test_admin_activation_then_admission_and_stale_denial(self):
  client=cli.Client(self.url);plan=self.plan();receipt=cli.apply(client,plan,{})
  # Test initial activation, not the setup fixture's pre-existing bootstrap.
  self.writer.safe(self.writer.state_path('npc')).unlink()
  payload={'requestId':plan['requestId'],'expectedSourceVersion':self.writer.snapshot()['sourceVersion'],'planHash':plan['planHash'],'backupManifestHash':receipt['backupManifestHash']}
  bad=dict(payload,expectedSourceVersion='0'*64)
  with self.assertRaises(urllib.error.HTTPError):client.call('activate',bad)
  result=client.call('activate',payload);self.assertTrue(result['adminActivated']);self.assertEqual(client.call('activate',payload),result)
  with make_transport(self.config)as t:
   guard=t.admit('npc',result['generationId'],'postmigration.parent');self.assertEqual(guard['generationId'],result['generationId'])
  # An unrelated legacy/global item is never touched by activation.
  self.assertEqual(self.writer.generation('npc')['activeLeases'],1)
 def test_binary_import_hash_and_retry(self):
  import base64,copy
  data=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aY1cAAAAASUVORK5CYII=')
  aid=hashlib.sha256(data).hexdigest()+'.png';local=self.root/'input.png';local.write_bytes(data)
  plan=self.plan();meta=copy.deepcopy(self.writer.snapshot()['documents']['npc/metadata.json']);meta['portraitSets']={'combat':{'pixelAssetId':aid}}
  plan['changedDocuments']={'metadata.json':meta};plan['importAssets']=[{'assetId':aid,'sha256':aid[:-4]}];plan['planHash']=digest({k:v for k,v in plan.items()if k!='planHash'})
  inputs={aid:{'path':str(local),'sha256':aid[:-4],'bytes':len(data)}};client=cli.Client(self.url)
  receipt=cli.apply(client,plan,inputs);self.assertEqual(cli.apply(client,plan,inputs),receipt)
  self.assertEqual((self.data/'npc/assets/portraits/combat/pixel.png').read_bytes(),data)
  _,backup=self.writer.verified_backup(receipt['backupId']);self.assertEqual(backup['imports/'+aid],data)
 def test_origin_cannot_prepare_and_bad_hash_rejected(self):
  plan=self.plan();req=urllib.request.Request(self.url+'/v1/npcs/ownership/migration/prepare',data=json.dumps(plan).encode(),headers={'Origin':self.url,'Content-Type':'application/json'})
  with self.assertRaises(urllib.error.HTTPError)as e:urllib.request.urlopen(req)
  self.assertEqual(e.exception.code,403)
  plan['planHash']='0'*64
  with self.assertRaises(urllib.error.HTTPError):cli.Client(self.url).call('prepare',plan)
 def test_active_parent_blocks_migration(self):
  with make_transport(self.config)as t:t.admit('npc',self.generation,'busy')
  with self.assertRaises(urllib.error.HTTPError):cli.Client(self.url).call('prepare',self.plan())

class PlanTests(unittest.TestCase):
 def test_endpoint_no_remote_credentials_redirect(self):
  for url in ['http://example.com:8030','http://user@127.0.0.1:8030','https://127.0.0.1:8030','http://127.0.0.1:8030?x=y']:
   with self.assertRaises(ValueError):cli.Client(url)
 def test_plan_hash_and_no_paths_on_wire(self):
  p={'schemaVersion':'npc-owned-migration.preflight.v1','npcId':'npc','sourceVersion':'a'*64,'changedDocuments':{},'importInputs':{'b'*64+'.png':{'sha256':'b'*64,'path':'/private/local.png','bytes':1}},'blockers':[]}
  p['planHash']=hashlib.sha256(json.dumps(p,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  wire=cli.prepare_plan(p,'migration.test');self.assertNotIn('/private',json.dumps(wire));self.assertEqual(wire['planHash'],digest({k:v for k,v in wire.items()if k!='planHash'}))
  p['npcId']='other'
  with self.assertRaisesRegex(ValueError,'HASH'):cli.prepare_plan(p,'migration.test')
if __name__=='__main__':unittest.main()
