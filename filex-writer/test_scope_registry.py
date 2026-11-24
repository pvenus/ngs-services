import base64,json,os,tempfile,threading,unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from scope_registry import Scope,bind_scopes,load_registry,unified_handler
from test_prop_package_http import make

class UnifiedFixture(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        self.data=self.root/'store';self.data.mkdir(mode=0o700)
        self.secret=self.root/'secret';self.secret.mkdir(mode=0o700)
        self.scope=load_registry()
        for scope in self.scope:
            (self.data/scope.folder).mkdir(mode=0o700)
        self.tokens={key:(key+'-')*24 for key in ('npc','concept','motion','media')}
        for key,token in self.tokens.items():
            folder=self.secret/key;folder.mkdir(mode=0o700);p=folder/'writer_token';p.write_text(token+'\n');p.chmod(0o600)
        maps=bind_scopes(self.scope,self.data,self.secret,verify_mounts={str(self.data/key.folder) for key in self.scope})
        s=ThreadingHTTPServer(('127.0.0.1',0),unified_handler(maps))
        thread=threading.Thread(target=s.serve_forever,daemon=True);thread.start()
        def stop():s.shutdown();s.server_close();thread.join()
        self.addCleanup(stop);self.origin=f'http://127.0.0.1:{s.server_port}'
    def call(self,path,token=None,body=None,status=200,extra=None):
        headers={} if token is None else {'Authorization':'Bearer '+self.tokens[token]}
        data=None if body is None else json.dumps(body).encode()
        if data is not None:headers['Content-Type']='application/json'
        q=Request(self.origin+path,data=data,headers={**headers,**(extra or {})})
        try:
            with urlopen(q,timeout=5) as r:self.assertEqual(r.status,status);return r.read()
        except HTTPError as e:
            with e:self.assertEqual(e.code,status);return e.read()
    def test_one_listener_all_runtime_roles_and_no_legacy_or_cross_scope_fallback(self):
        roles={'npc':('npc','npcs'),'concept':('concept','concept'),'motion':('motion','motion-presets'),
          'media':('media','media-artifacts'),'audio':('concept','audio'),'props':('concept','props')}
        for key,(auth,folder) in roles.items():
            with self.subTest(key=key):
                path=f'/v1/scopes/{key}/capabilities'
                caps=json.loads(self.call(path,auth))
                self.assertEqual(caps['namespace'],'canonical-files/binari/'+folder+'/')
                for bearer in self.tokens:
                    if bearer!=auth:self.call(path,bearer,status=401)
        self.call('/v1/capabilities','concept',status=404)
        self.call('/v1/scopes/not-approved/capabilities','concept',status=404)
        self.call('/v1/scopes/props/snapshot','concept',status=404)
        self.call('/v1/scopes/props/workflow/audio','concept',{},status=404)
        self.call('/v1/scopes/audio/workflow/props','concept',{},status=404)
        self.call('/v1/scopes/props/capabilities','concept',status=401,extra={'Origin':'https://foreign.example'})
        self.call('/v1/scopes/props/%2e%2e/concept/snapshot','concept',status=404)
        self.call('/v1/scopes/concept/workflow/props','concept',{},status=404)
    def test_scoped_real_prop_commit_and_concept_audio_npc_motion_media_isolation(self):
        before=set((self.data/'concept').iterdir())
        e=make('3');body={key:e[key] for key in ('candidate','manifest','media')}
        answer=json.loads(self.call('/v1/scopes/props/workflow/props','concept',body))
        self.assertEqual(answer['resultRef'],e['candidate']['resultRef'])
        pin={'propId':e['manifest']['propId'],'revision':1,'sha256':e['candidate']['manifestSha256']}
        proof=json.loads(self.call('/v1/scopes/props/workflow/props/read','concept',pin))
        self.assertEqual(proof['manifest'],e['manifest'])
        self.assertEqual(set((self.data/'concept').iterdir()),before)
        for key in ('audio','npcs','motion-presets','media-artifacts'):
            self.assertFalse(list((self.data/key).iterdir()))
        self.call('/v1/scopes/concept/snapshot','concept')
        self.call('/v1/scopes/npc/snapshot','npc')
        self.call('/v1/scopes/motion/snapshot','motion')
        self.call('/v1/scopes/media/media-artifacts/assets/'+'f'*64+'.png','media',status=404)
        self.call('/v1/scopes/audio/workflow/audio/read','concept',{'conceptId':'invalid-test'},status=404)
    def test_motion_registration_and_array_only_reusable_concept_profile(self):
        path='canonical-files/binari/motion-presets/presets/fixture.json'
        packet={'requestId':'scoped-fixture','inputHash':'1'*64,
          'readSet':{path:None},'files':[{'path':path,'expectedHash':None,'body':{'fixture':'motion'}}]}
        ack=json.loads(self.call('/v1/scopes/motion/batch','motion',packet))
        self.assertEqual(ack['status'],'committed')
        self.assertTrue((self.data/'motion-presets'/'presets'/'fixture.json').is_file())
        self.call('/v1/scopes/concept/batch','concept',packet,status=400)
        assert (self.data/'concept'/'presets'/'fixture.json').exists() is False
        # Adding a sibling with the same reviewed policy changes only the
        # registry and mount set. It does not add a server/port.
        alt=Scope('region-concept','region-concept','concept','concept')
        (self.data/alt.folder).mkdir(mode=0o700)
        out=bind_scopes((*self.scope,alt),self.data,self.secret)
        self.assertIn(alt.id,out)
    def test_missing_or_wrong_secret_mount_refuses_start(self):
        req={str(self.data/key.folder) for key in self.scope}
        with self.assertRaisesRegex(ValueError,'FILEX_SCOPE_MOUNT_REQUIRED'):
            bind_scopes(self.scope,self.data,self.secret,verify_mounts=req|{str(self.data)})
        with self.assertRaisesRegex(ValueError,'FILEX_SCOPE_MOUNT_REQUIRED'):
            bind_scopes(self.scope,self.data,self.secret,verify_mounts=req-{str(self.data/'props')})
        with self.assertRaisesRegex(ValueError,'FILEX_SCOPE_MOUNT_UNKNOWN'):
            bind_scopes(self.scope,self.data,self.secret,verify_mounts=req|{str(self.data/'unknown')})
        (self.secret/'motion'/'writer_token').chmod(0o644)
        with self.assertRaisesRegex(ValueError,'FILEX_SCOPE_TOKEN_INVALID'):
            bind_scopes(self.scope,self.data,self.secret,verify_mounts=req)

if __name__=='__main__':unittest.main()
