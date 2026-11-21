import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from release_equivalence import SCHEMA, actual_selector_sha, equivalent_release


A='a'*40
B='b'*40
D='d'*64
STAMP='2026-11-22T00:00:00Z'
ID='worker-image'


class EquivalenceTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=Path(t.name).resolve()
        self.path=self.root/'equivalent.json'
        self.ready={'sha':B,'artifactSha256':D,'runnerSha256':'e'*64}
        self.observation={'sha':A,'deployedAt':STAMP}
        self.data={'schemaVersion':SCHEMA,'target':ID,'observedSha':A,
                   'selectorSha256':'f'*64,'runtimeTreeSha256':'0'*64,'verifiedAt':STAMP,**self.ready}
    def save(self,**patch):
        self.path.write_text(json.dumps({**self.data,**patch}));self.path.chmod(0o600)
    def check(self,ready=None,observation=None,selector='f'*64):
        return equivalent_release(self.root,ID,ready or self.ready,self.observation if observation is None else observation,
                                  selector_sha=lambda ident:selector if ident==ID else None)
    def test_no_change_binding_and_unrelated_new_candidate_never_inherits(self):
        self.assertFalse(self.check());self.save()
        self.assertTrue(self.check())
        self.assertFalse(self.check({**self.ready,'sha':'9'*40}))
        self.assertEqual(self.observation,{'sha':A,'deployedAt':STAMP})
    def test_every_live_binding_required(self):
        self.save()
        with self.assertRaises(ValueError):self.check({**self.ready,'artifactSha256':'0'*64})
        with self.assertRaises(ValueError):self.check({**self.ready,'runnerSha256':'0'*64})
        with self.assertRaises(ValueError):self.check(observation={'sha':'0'*40,'deployedAt':STAMP})
        with self.assertRaises(ValueError):self.check(observation={})
        with self.assertRaises(ValueError):self.check(selector='0'*64)
        self.path.chmod(0o644)
        with self.assertRaises(OSError):self.check()
    def test_real_selector_is_fixed_bounded_and_fails_if_alias(self):
        base=self.root/'Library/LaunchAgents';base.mkdir(parents=True)
        p=base/'com.vinus.workflow.image-worker.plist'
        p.write_text('safe');p.chmod(0o600)
        self.assertEqual(actual_selector_sha(ID,self.root),hashlib.sha256(b'safe').hexdigest())
        with self.assertRaises(ValueError):actual_selector_sha('ngs-space',self.root)
        p.chmod(0o666)
        with self.assertRaises(ValueError):actual_selector_sha(ID,self.root)
        previous=base/'moved';p.rename(previous);p.symlink_to(previous)
        with self.assertRaises(OSError):actual_selector_sha(ID,self.root)
