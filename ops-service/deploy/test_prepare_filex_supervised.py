from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import prepare_filex_supervised as s
import prepare_filex_writer as writer
from prepare_ngs_release import Refused, atomic_write, digest, json_bytes, json_load


HEAD = 'a'*40
BASE = 'python:3.12-slim@sha256:'+'b'*64
IMAGE = 'sha256:'+'c'*64
PRIOR = 'sha256:'+'d'*64
COMPOSE = 'e'*64
STARTUP = 'f'*64
RUNTIME = '1'*64
ARTIFACT = '2'*64


class SupervisedFilex(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home=Path(tmp.name).resolve()
        self.control=self.home/'control'
        self.control.mkdir(mode=0o700)
        (self.control/'targets').mkdir(mode=0o700)
        self.directory=self.control/'targets'/s.TARGET
        self.directory.mkdir(mode=0o700)
        (self.directory/'run-once').write_bytes(b'reviewed, non-executed fixture adapter')
        (self.directory/'run-once').chmod(0o700)
        self.runner=digest((self.directory/'run-once').read_bytes())
        self.prior={'priorImageId':PRIOR,'composeSha256':COMPOSE,'startupSha256':STARTUP,'runtimeFingerprint':RUNTIME}
        self.prepared=self.control/'filex-writer-prepared'
        self.policy=dict(schemaVersion=s.CONFIG_SCHEMA,base=BASE,runnerSha256=self.runner,
                         composeSha256=COMPOSE,startupSha256=STARTUP)
        self.proof=dict(schemaVersion=s.STATE,target=writer.TARGET,state='image-verified',
            source=dict(schemaVersion=writer.SCHEMA,repository=writer.REPO,ref=writer.REF,
                        headSha=HEAD,runId='2',runAttempt='1',rootTree='3'*40,
                        files={k:'4'*64 for k in writer.CLOSURE}),
            provenance=dict(artifactId='3',artifactSha256=ARTIFACT),base=BASE,imageId=IMAGE,
            smoke='isolated-authenticated-capabilities',preparedAt='2026-11-23T00:00:00Z')
        self.docker=Mock(spec=writer.IsolatedDocker)
        self.docker.verify_image.return_value=True
        self.observer=Mock(return_value=self.prior)
        self.main=Mock()
        self.make=Mock(side_effect=self.record)
        self.repo=self.home/'bare'
        self.transport=object()

    def opt_in(self, **updates):
        atomic_write(self.control/s.CONFIG_NAME,json_bytes({**self.policy,**updates}))

    def record(self, github, repo, control, base, docker):
        self.assertIs(github,self.transport)
        self.assertEqual(repo,self.repo)
        self.assertEqual(control,self.control)
        self.assertEqual(base,BASE)
        self.assertIs(docker,self.docker)
        self.prepared.mkdir(mode=0o700,exist_ok=True)
        atomic_write(self.prepared/(HEAD+'.json'),json_bytes(self.proof))
        return self.proof

    def prepare(self):
        return s.prepare_filex_candidate(self.transport,self.repo,self.home,self.control,HEAD,
           assert_current=self.main,read_prior=self.observer,docker=self.docker,prepare_image=self.make)

    def test_disabled_or_wrong_private_pins_never_call_docker_or_actions(self):
        self.assertEqual(self.prepare()['state'],'not_configured')
        self.opt_in(base='python:3.12-slim')
        with self.assertRaisesRegex(Refused,'PREP_CONFIG_INVALID'):
            self.prepare()
        self.opt_in(runnerSha256='0'*64)
        with self.assertRaisesRegex(Refused,'RUNNER_PIN_MISMATCH'):
            self.prepare()
        self.opt_in(composeSha256='0'*64)
        with self.assertRaisesRegex(Refused,'PRIOR_RUNTIME_UNVERIFIED'):
            self.prepare()
        self.make.assert_not_called()
        self.docker.verify_image.assert_not_called()

    def test_only_exact_bound_manual_candidate_is_published_and_reused(self):
        self.opt_in()
        self.assertEqual(self.prepare()['state'],'registered')
        pointer=(self.directory/'candidate.json').read_bytes()
        bound=(self.prepared/(HEAD+'.apply.json')).read_bytes()
        self.assertEqual(pointer,bound)
        self.assertEqual(json_load(pointer),dict(schemaVersion=s.CANDIDATE,target=s.TARGET,
              sha=HEAD,artifactSha256=ARTIFACT,imageId=IMAGE,**self.prior))
        ready_path=self.directory/'ready.json'
        ready=json_load(ready_path.read_bytes())
        self.assertEqual(ready,dict(schemaVersion=s.READY,target=s.TARGET,sha=HEAD,
              artifactSha256=ARTIFACT,runnerSha256=self.runner,manualOnly=True,verifiedAt=ready['verifiedAt']))
        self.assertEqual(ready_path.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.prepare()['state'],'ready')
        self.assertEqual(pointer,(self.directory/'candidate.json').read_bytes())
        self.assertTrue(all(call.args==(HEAD,) for call in self.main.call_args_list))

    def test_drift_busy_and_image_mismatch_never_publish_ready(self):
        self.opt_in()
        self.observer.side_effect=[self.prior,{**self.prior,'runtimeFingerprint':'5'*64}]
        with self.assertRaisesRegex(Refused,'PREPARATION_CHANGED'):
            self.prepare()
        self.observer.side_effect=None
        self.docker.verify_image.return_value=False
        with self.assertRaisesRegex(Refused,'IMAGE_ID_INVALID'):
            self.prepare()
        self.docker.verify_image.return_value=True
        (self.control/'active.json').touch()
        with self.assertRaisesRegex(Refused,'PREP_ACTIVE_DEPLOYMENT'):
            self.prepare()
        self.assertFalse((self.directory/'ready.json').exists())
        self.assertFalse((self.prepared/(HEAD+'.apply.json')).exists())

    def test_existing_same_head_binding_cannot_be_silently_repointed(self):
        self.opt_in()
        self.prepare()
        before=(self.prepared/(HEAD+'.apply.json')).read_bytes()
        self.observer.return_value={**self.prior,'runtimeFingerprint':'6'*64}
        with self.assertRaisesRegex(Refused,'PREPARATION_CHANGED'):
            self.prepare()
        self.assertEqual((self.prepared/(HEAD+'.apply.json')).read_bytes(),before)

    def test_readiness_write_failure_preserves_existing_candidate_pointer(self):
        self.opt_in()
        pointer=self.directory/'candidate.json'
        previous=b'{"previous":"candidate"}'
        atomic_write(pointer,previous)
        def conditional(path,data):
            if path.name=='ready.json':
                raise OSError('synthetic fixture failure')
            atomic_write(path,data)
        with patch.object(s,'atomic_write',conditional):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertEqual(pointer.read_bytes(),previous)
        self.assertFalse((self.directory/'ready.json').exists())


if __name__ == '__main__':
    unittest.main()
