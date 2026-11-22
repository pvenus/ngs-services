from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from prepare_filex_writer import closure, git, REPO, SCHEMA
from prepare_ngs_release import atomic_write, digest, json_bytes

import filex_writer_active as m


class FilexPrerequisite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Path(__file__).resolve().parents[2]
        cls.head = git(cls.repo,'rev-parse','HEAD').decode().strip()
        cls.previous = git(cls.repo,'rev-parse','HEAD^').decode().strip()
        cls.root, cls.files = closure(cls.repo,cls.head)
        cls.prepared_at = '2026-11-22T15:00:00Z'
        cls.deployed_at = '2026-11-22T16:00:00Z'

    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.control = self.home/'control'
        self.folder = self.control/'targets'/m.TARGET
        self.applied_dir = self.folder/'applied'
        self.staged = self.control/'filex-writer-prepared'
        for path in (self.control,self.control/'targets',self.folder,self.applied_dir,self.staged):
            path.mkdir(mode=0o700)
        self.staged_path = self.staged/(self.previous+'.json')
        self.applied_path = self.applied_dir/(self.previous+'.json')
        self.result_path = self.folder/'last-result.json'
        self.prepared = {'schemaVersion':m.EVIDENCE,'target':'filex-concept-writer','state':'image-verified',
            'source':{'schemaVersion':SCHEMA,'repository':REPO,'ref':m.REF,'headSha':self.previous,
                      'runId':'200','runAttempt':'1','rootTree':self.root,
                      'files':{k:digest(v) for k,v in self.files.items()}},
            'provenance':{'artifactId':'100','artifactSha256':'a'*64},
            'base':'python:3.12-slim@sha256:'+'c'*64,'imageId':'sha256:'+'b'*64,
            'smoke':'isolated-authenticated-capabilities','preparedAt':self.prepared_at}
        self.applied = {'schemaVersion':m.APPLIED,'target':m.TARGET,'sha':self.previous,
            'artifactSha256':'a'*64,'imageId':self.prepared['imageId'],'priorImageId':'sha256:'+'4'*64,
            'composeSha256':'1'*64,'startupSha256':'2'*64,'runtimeFingerprint':'3'*64,
            'deployedAt':self.deployed_at}
        self.result = {'schemaVersion':m.TERMINAL,'target':m.TARGET,'attemptedSha':self.previous,
            'state':'deployed','finishedAt':'2026-11-22T16:00:05Z',
            'observed':{'sha':self.previous,'deployedAt':self.deployed_at}}
        self.runtime = Mock(return_value=True)

    def write(self,path,payload):
        atomic_write(path,json_bytes(payload))

    def persist(self):
        self.write(self.applied_path,self.applied)
        self.write(self.staged_path,self.prepared)
        self.write(self.result_path,self.result)

    def run_guard(self):
        return m.filex_active(self.home,self.control,self.repo,self.head,runtime=self.runtime)

    def test_exact_writer_closure_and_success_receipt_still_need_live_proof(self):
        self.persist()
        self.assertNotEqual(self.head,self.previous)
        self.assertTrue(self.run_guard())
        self.runtime.assert_called_once_with(self.home,self.control,self.applied,host=None)
        self.runtime.return_value = False
        self.assertFalse(self.run_guard())
        self.runtime.return_value = 1  # no truthiness-only attestation
        self.assertFalse(self.run_guard())
        self.runtime.side_effect = OSError('private Docker detail')
        self.assertFalse(self.run_guard())

    def test_staged_image_does_not_prove_success_or_active_health(self):
        self.write(self.staged_path,self.prepared)
        self.assertFalse(self.run_guard())
        self.write(self.result_path,self.result)
        self.assertFalse(self.run_guard())
        self.runtime.assert_not_called()

    def test_mismatched_source_receipt_and_recovery_are_fail_closed(self):
        self.persist()
        wrong = {**self.prepared,'imageId':'sha256:'+'5'*64}
        self.write(self.staged_path,wrong)
        self.assertFalse(self.run_guard())
        p = self.prepared['source']
        self.write(self.staged_path,{**self.prepared,'source':{**p,'files':{**p['files'],'workflow_audio_store.py':'9'*64}}})
        self.assertFalse(self.run_guard())
        self.write(self.staged_path,self.prepared)
        self.write(self.result_path,{**self.result,'state':'failed'})
        self.assertFalse(self.run_guard())
        self.runtime.assert_not_called()

    def test_racing_replacement_and_nonprivate_proof_are_rejected(self):
        self.persist()
        def race(*args,**kwargs):
            self.write(self.result_path,{**self.result,'finishedAt':'2026-11-22T17:00:00Z'})
            return True
        self.runtime.side_effect = race
        self.assertFalse(self.run_guard())
        self.runtime.reset_mock(side_effect=True)
        self.applied_path.chmod(0o644)
        self.assertFalse(self.run_guard())
        self.runtime.assert_not_called()


if __name__ == '__main__':
    unittest.main()
