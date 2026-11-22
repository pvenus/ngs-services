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

    def native(self, *, result=None, stderr=None, rc=1):
        script=self.root/'bin/ngs-mac-apply'
        source='#!/bin/sh\nprintf "%s" "$1" > invocation\n'
        if result is not None:
            source+="umask 077\nprintf '%s' '"+json.dumps(result)+"' > mac-releases/"+SHA+"/result.json\n"
        if stderr is not None:
            source+="printf '%s\\n' '"+stderr+"' >&2\n"
        script.write_text(source+f'exit {rc}\n')
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
        self.native(result={'sha':SHA,'target':'mac','state':'deployed','health':'verified','deployedAt':WHEN},rc=0)
        result=M.run(self.root,SHA,ARCHIVE)
        self.assertEqual((self.root/'invocation').read_text(),SHA)
        self.assertEqual(result,{'schemaVersion':'ops.deploy-target-receipt.v1','target':'ngs-local',
                         'state':'deployed','health':'verified','sha':SHA,'deployedAt':WHEN})

    def test_does_not_report_rollback_as_success(self):
        self.write(f'mac-releases/{SHA}/result.json',{'state':'rolled-back'})
        with self.assertRaises(M.Failure) as error:
            M.run(self.root,SHA,ARCHIVE)
        self.assertEqual(error.exception.value,
                         {'phase':'unknown','code':'NGS_LOCAL_CAUSE_NOT_RECORDED','rollback':'restored'})
        self.assertFalse((self.root/'invocation').exists())

    def test_real_child_preserves_initial_failure_and_rollback_failure(self):
        failure={'stage':'check_candidate','code':'NATIVE_AUTH_SCOPE_DRIFT'}
        rollback={'stage':'rollback_check','code':'ROLLBACK_STORAGE_IDENTITY_DRIFT'}
        self.native(result={'state':'rollback-failed','failure':failure,'rollbackFailure':rollback},
                    stderr='raw log /secret/queue path')
        with self.assertRaises(M.Failure) as error:
            M.run(self.root,SHA,ARCHIVE)
        self.assertEqual(error.exception.value,{'phase':'check_candidate','code':'NATIVE_AUTH_SCOPE_DRIFT',
                                               'rollback':'failed','rollbackFailure':rollback})
        self.assertEqual((self.root/'invocation').read_text(),SHA)

    def test_exact_preflight_stderr_and_explicit_no_restore(self):
        self.native(stderr=json.dumps({'schemaVersion':'ops.ngs-mac-apply-error.v1',
                 'failure':{'stage':'validate_candidate','code':'RELEASE_ARCHIVE_DRIFT'}}))
        with self.assertRaises(M.Failure) as error:
            M.run(self.root,SHA,ARCHIVE)
        self.assertEqual(error.exception.value,{'phase':'validate_candidate','code':'RELEASE_ARCHIVE_DRIFT',
                                               'rollback':'not_attempted'})

    def test_bad_nested_or_raw_fields_never_escape_and_no_state_inferred(self):
        raw=json.dumps({'schemaVersion':'ops.ngs-mac-apply-error.v1',
                        'failure':{'stage':'check_candidate','code':'PRIVATE_TOKEN_ABC','raw':'/owner/secret'}})
        self.native(result={'state':'rolled-back','failure':{'stage':'check_candidate','code':'PRIVATE_TOKEN_ABC'}},
                    stderr=raw)
        with self.assertRaises(M.Failure) as error:
            M.run(self.root,SHA,ARCHIVE)
        self.assertEqual(error.exception.value,{'phase':'unknown','code':M.NGS_LOCAL_UNRECORDED,'rollback':'restored'})
        self.assertNotIn('SECRET',str(error.exception.value))

    def test_strict_preexisting_attempt_marker_never_invokes_native(self):
        self.write(f'mac-releases/{SHA}/prior.plist',{'private':'do not reveal'})
        with self.assertRaises(M.Failure) as error:
            M.run(self.root,SHA,ARCHIVE)
        self.assertEqual(error.exception.value['code'],'RELEASE_ALREADY_ATTEMPTED_RECONCILE')
        self.assertEqual(error.exception.value['rollback'],'unknown')
        self.assertFalse((self.root/'invocation').exists())
