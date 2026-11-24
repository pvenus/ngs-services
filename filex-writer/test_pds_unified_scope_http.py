"""Production PDS clients and the real one-listener Filex, sterile scratch."""
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from project_data_service import concept_filex, motion_presets
from project_data_service.filex_writer_binding import CONFIG_ENV, PROP_NAMESPACE, scope_config, writer
from project_data_service.pds_owned_transport import OwnedNPCTransport
from project_data_service.media_artifacts import MediaArtifacts
from project_data_service.workflow_prop_package import PATH, WorkflowPropPackage, bound_prop_writer
from test_scope_registry import UnifiedFixture
from test_prop_package_http import FixtureAuthority, PDS, PropFixture, make


class JoinedClients(UnifiedFixture):
    def setUp(self):
        super().setUp()
        identity={'npc':'npc','motion':'motion','media':'media','concept':'concept','audio':'concept','props':'concept'}
        def token(role):
            return str(self.secret/identity[role]/'writer_token')
        config=self.root/'pds.json'
        config.write_text(json.dumps({'schemaVersion':'pds-filex-writer.v1','projectId':'binari','endpoint':self.origin,
            'scopes':[{'id':role,'tokenFile':token(role)} for role in identity]}))
        config.chmod(0o600)
        env=patch.dict(os.environ,{CONFIG_ENV:str(config)});env.start();self.addCleanup(env.stop)
        self.media_config=self.root/'media-pds-test.json'
        self.media_config.write_text(json.dumps({'schemaVersion':'pds.media-artifacts.v2','enabled':True,
            'authorityEndpoint':self.origin,'authorityTokenFile':token('media'),'ingressTokenFile':token('concept')}))
        self.media_config.chmod(0o600)

    def test_production_pds_scope_routes_and_real_writer_keep_storage_isolated(self):
        with concept_filex.factory() as c:
            self.assertEqual(c.capabilities()['namespace'],'canonical-files/binari/concept/')
            self.assertIn('documents',c.snapshot())
            with self.assertRaisesRegex(ValueError,'HTTP_404'):
                c.call('POST','/v1/workflow/props',{})
        with motion_presets.factory() as m:
            self.assertEqual(m.capabilities()['namespace'],'canonical-files/binari/motion-presets/')
        with OwnedNPCTransport({**scope_config('npc'),'scope':'npc'}) as npc:
            self.assertTrue(npc.capabilities()['generationFence'])
        self.assertIsNone(MediaArtifacts.configured(self.media_config).writer('GET','/v1/media-artifacts/assets/'+'b'*64+'.png'))
        with writer('audio') as audio:
            self.assertTrue(audio.capabilities()['workflowAudioSeparateV1'])

        authority=FixtureAuthority()
        outgoing=bound_prop_writer(lambda:writer('props'),namespace=PROP_NAMESPACE)
        self.assertTrue(outgoing.ready())
        pds=PropFixture.launch(self,PDS,workflow_prop_package=WorkflowPropPackage(authority,outgoing))
        entry=make('a');claim=entry['claim'];authority.claims[claim['officialRequestId']]=entry
        url=pds+PATH
        def call(path,data):
            packet=Request(url+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'},method='POST')
            with urlopen(packet,timeout=5) as r:
                return json.load(r)
        body={'claim':claim,'manifest':entry['manifest']}
        fabricated={**claim,'claimStamp':999}
        with self.assertRaises(HTTPError) as exc:
            call('',{**body,'claim':fabricated})
        self.assertEqual(exc.exception.code,403)
        self.assertEqual(call('',body),{'ok':True,'resultRef':entry['candidate']['resultRef']})
        pin={'propId':entry['manifest']['propId'],'revision':1,'sha256':entry['candidate']['manifestSha256']}
        self.assertEqual(call('/read',pin)['manifest'],entry['manifest'])
        self.assertTrue((self.data/'props'/entry['manifest']['propId']/'metadata.json').is_file())
        for family in ('concept','npcs','motion-presets','audio','media-artifacts'):
            self.assertFalse((self.data/family/entry['manifest']['propId']).exists())
