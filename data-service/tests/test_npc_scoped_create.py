import base64,copy,json,pathlib,tempfile,threading,unittest,sys,urllib.request,urllib.error
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]/'filex-writer'))
from server import handler
from npc_store import NPCStore
from fenced_store import FencedOwnedNPCStore
from project_data_service.filex_npc_files import encode,PREFIX
from project_data_service.filex_npc_store import FilexNPCStore
from project_data_service.filex_npc_authority import FilexCurrentNPC
from project_data_service.npc_ownership import make_transport
from project_data_service.filex_read import digest
from project_data_service.domain_contracts import legacy_http as native
from project_data_service.domain_contracts.profile_model import blank_profile,TRAIT_GROUPS
from project_data_service.http_server import Handler

def character(nid,rid):
 return {**dict.fromkeys(native.NPC_TEXT_FIELDS,''),'id':nid,'name':'Fixture','role':'주민','status':'proposal','chapters':['chapter01'],'sourceRefs':[],'traits':{g:dict.fromkeys(keys)for g,keys in TRAIT_GROUPS.items()},'numericProfile':blank_profile(),'portraits':[],'activePortraitId':None,'creationBasis':{'requestId':rid},'planningCategories':[{'id':'planning.fixture','name':'기획','content':''}]}

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=pathlib.Path(self.tmp.name).resolve();self.data=self.root/'data';self.data.mkdir();token=self.root/'token';token.write_text('isolated-'+'a'*40);token.chmod(0o600)
  old=character('character.old','old');old['largeExistingArtifact']='a'*(3*1024*1024)
  self.doc={'schemaVersion':1,'projectId':'ProjectBS','actId':'act01','masterRevision':1,'storyRevision':1,'characters':[old]}
  files=encode(self.doc);files['metadata.json']['_pdsCurrent']={'revision':1,'savedAt':'test','note':'seed','documentHash':digest(self.doc)}
  seed=NPCStore(self.data);seed.batch('seed','seed',[{'path':PREFIX+k,'expectedHash':None,'body':v}for k,v in files.items()],{PREFIX+k:None for k in files})
  self.writer=FencedOwnedNPCStore(self.data)
  self.filex=ThreadingHTTPServer(('127.0.0.1',0),handler(self.writer,token.read_text()));self.start(self.filex)
  config={'schemaVersion':'pds-filex-cas.v1','projectId':'binari','scope':'npc','enabled':True,'ownershipMode':'npc-owned-v1','endpoint':'http://127.0.0.1:'+str(self.filex.server_port),'tokenFile':str(token)}
  self.source=FilexCurrentNPC(FilexNPCStore(lambda:make_transport(config)))
  snap=self.source.current()
  with make_transport(config)as t:self.guard=t.prepare_create('character.new','batch.create',snap['sourceVersion'])
  self.pds=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.pds.npc_store=self.source;self.pds.app=SimpleNamespace(domains=SimpleNamespace(dispatch=lambda h:native.Handler.route(h)));self.start(self.pds)
  char=character('character.new','lifecycle.create');after=copy.deepcopy(self.doc);after['characters'].append(char);after['generationEpoch']='batch.create'
  self.body={'requestId':'lifecycle.create','character':char,'expectedRevision':1,'beforeDocumentHash':digest(self.doc),'documentHash':digest(after)};self.body['inputHash']=digest(self.body)
 def start(self,http):
  thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
  self.addCleanup(lambda:(http.shutdown(),http.server_close(),thread.join()))
 def tearDown(self):pass
 def call(self,body=None,guard=True):
  data=json.dumps(body or self.body).encode();self.assertLess(len(data),65536)
  headers={'Content-Type':'application/json'}
  if guard:headers['X-NPC-Ownership-Guard']=base64.b64encode(json.dumps(self.guard).encode()).decode()
  req=urllib.request.Request('http://127.0.0.1:'+str(self.pds.server_port)+'/api/npcs/create-character',data=data,headers=headers)
  return json.load(urllib.request.urlopen(req))
 def test_actual_pds_filex_create_and_replay_preserves_others(self):
  before=self.writer.snapshot();result=self.call();self.assertTrue(result['verified']);self.assertEqual(result['documentHash'],self.body['documentHash'])
  self.assertEqual(result['requestId'],'lifecycle.create');self.assertEqual(result['publicationReceipt']['requestId'],'batch.create')
  after=self.writer.snapshot();changed={k for k in after['hashes']if after['hashes'][k]!=before['hashes'].get(k)}
  self.assertTrue(all(k=='metadata.json'or k.startswith('character.new/')for k in changed));self.assertEqual(self.writer.generation('character.new')['state'],'active')
  self.assertEqual(self.source.current()['document']['characters'][0],self.doc['characters'][0])
  self.assertEqual(self.call(),result)
  # Replay must not re-read/recompose even after current-source observation fails.
  original=self.source._read;self.source._read=lambda:(_ for _ in ()).throw(AssertionError('replay reads current'))
  try:self.assertEqual(self.call(),result)
  finally:self.source._read=original
 def test_invalid_hash_guard_and_stale_source_fail_closed(self):
  with self.assertRaises(urllib.error.HTTPError):self.call(guard=False)
  for field in ('beforeDocumentHash','documentHash'):
   bad=copy.deepcopy(self.body);bad[field]='0'*64;bad['inputHash']=digest({k:v for k,v in bad.items()if k!='inputHash'})
   with self.assertRaises(urllib.error.HTTPError):self.call(bad)
  self.assertEqual(self.source.current()['revision'],1)
 def test_filex_commit_response_loss_recovers_receipt_without_rewrite(self):
  original=self.source.store.apply
  def lost(*args,**kwargs):
   original(*args,**kwargs);self.source.store.apply=original;raise OSError('simulated response loss')
  self.source.store.apply=lost
  with self.assertRaises(urllib.error.HTTPError):self.call()
  self.assertEqual(self.source.current()['revision'],2)
  result=self.call();self.assertEqual(result['revision'],2);self.assertTrue(result['verified'])
 def test_replay_wrong_guard_token_denied(self):
  self.call();self.guard={**self.guard,'lifecycleToken':'0'*64}
  with self.assertRaises(urllib.error.HTTPError):self.call()
  self.assertEqual(self.source.current()['revision'],2)
 def test_replay_conflict_does_not_replace_target(self):
  self.call();bad=copy.deepcopy(self.body);bad['character']['name']='changed';bad['inputHash']=digest({k:v for k,v in bad.items()if k!='inputHash'})
  with self.assertRaises(urllib.error.HTTPError):self.call(bad)
  self.assertEqual(self.source.current()['revision'],2)
if __name__=='__main__':unittest.main()
