"""Use a disposable runner; never change the Mac service."""
from importlib.machinery import SourceFileLoader
from pathlib import Path
import json
import tempfile
import unittest

M = SourceFileLoader('ngs_local_adapter', str(Path(__file__).with_name('ngs-local-run-once'))).load_module()
SHA='a'*40
ARCHIVE='c'*64
WHEN='2026-11-22T04:00:00Z'


class LocalBridgeTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        (self.root/'bin').mkdir()
        self.spec=self.root/'mac-releases'/SHA
        self.spec.mkdir(parents=True)
        self.plan={'schemaVersion':'ops.ngs-prepared-deploy.v1','sha':SHA,
                   'mac':{'ready':True},'provenance':{'archiveSha256':ARCHIVE}}
        self.write('prepared.json', self.plan)
        script=self.root/'bin/ngs-mac-apply'
        script.write_text('#!/bin/sh\nprintf "%s" "$1" > invocation\n')
        script.chmod(0o700)

    def write(self, path, payload):
        file=self.root/path
        file.write_text(json.dumps(payload))
        file.chmod(0o600)

    def test_requires_exact_fenced_candidate(self):
        with self.assertRaisesRegex(ValueError,'LOCAL_DEPLOY_PLAN_DRIFT'):
            M.run(self.root,SHA,'b'*64)
        self.assertFalse((self.root/'invocation').exists())

    def test_projects_authoritative_receipt(self):
        self.write(f'mac-releases/{SHA}/result.json',{'sha':SHA,'target':'mac',
                   'state':'deployed','health':'verified','deployedAt':WHEN})
        result=M.run(self.root,SHA,ARCHIVE)
        self.assertEqual((self.root/'invocation').read_text(),SHA)
        self.assertEqual(result,{'schemaVersion':'ops.deploy-target-receipt.v1','target':'ngs-local',
                         'state':'deployed','health':'verified','sha':SHA,'deployedAt':WHEN})

    def test_does_not_report_rollback_as_success(self):
        self.write(f'mac-releases/{SHA}/result.json',{'state':'rolled-back'})
        with self.assertRaisesRegex(ValueError,'INVALID_LOCAL_DEPLOY_RECEIPT'):
            M.run(self.root,SHA,ARCHIVE)
