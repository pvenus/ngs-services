"""Exercise the production PDS writer clients against a single socket."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from project_data_service import concept_filex, motion_presets
from project_data_service.filex_writer_binding import CONFIG_ENV, SCOPES, fingerprint, scope_config, scoped_path, writer
from project_data_service.media_artifacts import MediaArtifacts
from project_data_service.pds_owned_transport import OwnedNPCTransport
from project_data_service.workflow_prop_package import WRITER, bound_prop_writer
from project_data_service.filex_writer_binding import PROP_NAMESPACE


class ScopedClients(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        tokens = {}
        self.calls = []
        by_scope = []
        for scope in sorted(SCOPES):
            tokens[scope] = scope + '.fixture.' + 'x' * 42
            t = root / (scope + '.token')
            t.write_text(tokens[scope]); t.chmod(0o600)
            by_scope.append({'id': scope, 'tokenFile': str(t)})
        self.tokens = tokens

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):pass
            def dispatch(s):
                path = urlsplit(s.path)
                _, v1, scopes, scope, command = path.path.split('/', 4)
                assert (v1, scopes) == ('v1', 'scopes')
                token = s.headers.get('Authorization')
                if token != 'Bearer ' + tokens[scope]:
                    s.send_error(401);return
                raw = s.rfile.read(int(s.headers.get('Content-Length','0')))
                self.calls.append((scope, s.command, command, path.query,
                                  s.headers.get_content_type(), raw))
                data = {}
                ctype = 'application/json'
                if command == 'capabilities':
                    children = {'npc':'npcs','motion':'motion-presets'}
                    data = {'namespace':'canonical-files/binari/'+children.get(scope,scope)+'/',
                            'workflowPropPackageV1': scope == 'props',
                            'npcOwnedSlots': scope == 'npc', 'generationFence': scope == 'npc'}
                elif command == 'workflow/audio-separate/asset':
                    data = b'x' * 44;ctype = 'audio/wav'
                if not isinstance(data,bytes):data = json.dumps(data).encode()
                s.send_response(200);s.send_header('Content-Type',ctype)
                s.send_header('Content-Length',str(len(data)));s.end_headers();s.wfile.write(data)
            do_GET=do_POST=dispatch
        server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        runner = threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.02},daemon=True)
        runner.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.config = root / 'unified.json'
        self.manifest = {'schemaVersion':'pds-filex-writer.v1','projectId':'binari',
                         'endpoint':'http://127.0.0.1:'+str(server.server_port),'scopes':by_scope}
        self.write_config(self.manifest)
        env=patch.dict(os.environ, {CONFIG_ENV:str(self.config)})
        env.start();self.addCleanup(env.stop)
        # Existing ingress and Request-authority identities aren't writer URLs.
        self.media_config = root / 'media.json'
        self.media_config.write_text(json.dumps({
            'schemaVersion':'pds.media-artifacts.v1','enabled':True,
            'ingressTokenFile':by_scope[0]['tokenFile'],'authorityEndpoint':self.manifest['endpoint'],
            'authorityTokenFile':by_scope[0]['tokenFile'],
            'writerEndpoint':'http://127.0.0.1:1','writerTokenFile':'/does-not-exist'}))
        self.media_config.chmod(0o600)

    def write_config(self, value):
        self.config.write_text(json.dumps(value))
        self.config.chmod(0o600)

    def test_mixed_real_clients_share_one_listener_without_role_bleed(self):
        digest = hashlib.sha256(self.config.read_bytes()).hexdigest()
        self.assertEqual(fingerprint(), digest)
        with concept_filex.factory() as c:
            self.assertEqual(c.capabilities()['namespace'],'canonical-files/binari/concept/')
        with motion_presets.factory() as m:
            self.assertEqual(m.capabilities()['namespace'],'canonical-files/binari/motion-presets/')
        with writer('audio') as a:
            self.assertEqual(a.capabilities()['namespace'],'canonical-files/binari/audio/')
            content = b'x'*44
            self.assertEqual(a.audio_separate_asset('/v1/workflow/audio-separate/asset',
                {'sourceSha256':hashlib.sha256(content).hexdigest()},maximum=44),content)
        props = bound_prop_writer(lambda:writer('props'),namespace=PROP_NAMESPACE)
        self.assertTrue(props.ready())
        self.assertEqual(props(WRITER+'/read',{'propId':'fixture'}),{})
        media = MediaArtifacts.configured(self.media_config)
        self.assertEqual(media.writer('GET','/v1/media-artifacts/receipts/req/attempt'),{})
        with OwnedNPCTransport({**scope_config('npc'),'scope':'npc'},
            guard={'npcId':'npc.test','generationId':'gen','requestId':'req','leaseToken':'token'}) as n:
            self.assertEqual(n.get_generation('npc.test'),{})
            self.assertEqual(n.register_portrait_asset({'fixture':True},b'png-fixture'),{})
        self.assertEqual([c[0] for c in self.calls],['concept','motion','audio','audio','props','props','media','npc','npc'])
        self.assertEqual(self.calls[-2][3],'npcId=npc.test')
        self.assertEqual(self.calls[-1][4:],('image/png',b'png-fixture'))

    def test_traversal_and_unconfigured_or_wrong_scope_never_fall_back(self):
        for u in ['/v1/../npc','/v1/%2e%2e/npc','/v1/workflow/%2fother','/v1/a%252Fb','/v1/scopes/npc/capabilities',
                  'http://elsewhere/v1/capabilities','/v1//capabilities','/v1/\rwrong']:
            with self.subTest(u=u),self.assertRaises(ValueError):
                scoped_path('props',u)
        self.write_config({**self.manifest,'scopes':[self.manifest['scopes'][0]]*len(SCOPES)})
        with self.assertRaisesRegex(ValueError,'SCOPES_INVALID'):
            writer('npc')
        self.config.unlink()
        with self.assertRaisesRegex(ValueError,'CONFIG_REQUIRED'):
            concept_filex.factory()
        self.assertEqual(self.calls,[])

    def test_preapproved_folder_entry_extends_binding_without_new_urls(self):
        self.manifest['scopes'].append({'id':'additional-concept','tokenFile':self.manifest['scopes'][0]['tokenFile']})
        self.write_config(self.manifest)
        with writer('additional-concept') as extra:
            self.assertEqual(extra.request_url('/v1/capabilities'),
                self.manifest['endpoint']+'/v1/scopes/additional-concept/capabilities')
        with self.assertRaisesRegex(ValueError,'SCOPE_INVALID'):
            writer('unlisted')

    def test_wrong_token_is_authoritative_denial_with_no_alternate_writer(self):
        for entry in self.manifest['scopes']:
            if entry['id']=='concept':
                entry['tokenFile']=next(x['tokenFile'] for x in self.manifest['scopes'] if x['id']=='media')
        self.write_config(self.manifest)
        with concept_filex.factory() as source,self.assertRaisesRegex(ValueError,'HTTP_401'):
            source.capabilities()
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
