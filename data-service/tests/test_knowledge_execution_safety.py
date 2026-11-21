"""Synthetic execution/scratch safety; no real model, Filex, or user paths."""
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from project_data_service.knowledge_execution import KnowledgeExecution
from project_data_service.knowledge_requests import KnowledgeRequests, RequestJournal, digest
from test_knowledge_requests import FilexFixture, TestKnowledgeRequests


class ExecutionSafety(unittest.TestCase):
    request = TestKnowledgeRequests.request

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.execution = KnowledgeExecution(self.root / 'scratch')
        self.store = RequestJournal(self.root / 'state.sqlite')
        self.content = FilexFixture()
        self.snapshot = self.content.snapshot()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def folder(self, request):
        return self.execution.root / 'requests' / digest(request)

    def receipt(self, request, output):
        return {'requestId':request['requestId'], 'inputHash':digest(request),
                'sourceVersion':'published', 'verified':True,
                'fileHash':digest(output['files'][0]['body'])}

    def test_process_boundary_output_and_archive_equivalence(self):
        request = self.request()
        output = self.execution.execute(request, self.snapshot)
        folder = self.folder(request)
        files = {p.relative_to(folder).as_posix():p.read_bytes() for p in folder.rglob('*') if p.is_file()}
        status = self.execution.finalize(request, self.receipt(request, output), self.store)
        self.assertEqual(status['state'], 'cleaned')
        self.assertFalse(folder.exists())
        row = self.store.connection.execute('SELECT archive_hash,archive FROM knowledge_execution_archives').fetchone()
        self.assertEqual(hashlib.sha256(row[1]).hexdigest(),row[0])
        with tarfile.open(fileobj=io.BytesIO(row[1]),mode='r:gz') as archive:
            restored={m.name:archive.extractfile(m).read() for m in archive.getmembers()}
        self.assertEqual(restored,files)
        with self.assertRaisesRegex(ValueError,'RETIRED_NO_REPLAY'):
            self.execution.execute(request,self.snapshot)

    def test_bad_receipt_never_removes_scratch(self):
        request=self.request(); output=self.execution.execute(request,self.snapshot)
        for changes in ({'verified':False},{'requestId':'other'},{'inputHash':'other'},{'fileHash':'wrong'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.execution.finalize(request,{**self.receipt(request,output),**changes},self.store)
            self.assertTrue(self.folder(request).exists())

    def test_changed_source_rejects_cached_output(self):
        request=self.request();self.execution.execute(request,self.snapshot)
        changed={**self.snapshot,'sourceVersion':'different'}
        with self.assertRaisesRegex(ValueError,'OUTPUT_INVALID'):
            self.execution.execute(request,changed)

    def test_same_source_version_changed_snapshot_rejected(self):
        request=self.request();self.execution.execute(request,self.snapshot)
        changed={**self.snapshot,'documents':{**self.snapshot['documents'],'new.json':{'changed':True}}}
        with self.assertRaisesRegex(ValueError,'OUTPUT_INVALID'):
            self.execution.execute(request,changed)

    def test_committed_output_missing_terminal_recovers_without_process(self):
        request=self.request();output=self.execution.execute(request,self.snapshot)
        (self.folder(request)/'terminal.json').unlink()
        with patch('project_data_service.knowledge_execution.subprocess.run',side_effect=AssertionError('must not execute')):
            self.assertEqual(self.execution.execute(request,self.snapshot),output)
        self.assertEqual(json.loads((self.folder(request)/'terminal.json').read_text())['status'],'prepared')

    def test_partial_cleanup_retries_from_verified_archive(self):
        request=self.request();output=self.execution.execute(request,self.snapshot);receipt=self.receipt(request,output)
        def partial(folder):
            (folder/'started.json').unlink()
            (folder/'output.json').unlink()
            raise OSError('interrupted')
        with patch('project_data_service.knowledge_execution.shutil.rmtree',side_effect=partial):
            with self.assertRaises(OSError):self.execution.finalize(request,receipt,self.store)
        status=self.execution.finalize(request,receipt,self.store)
        self.assertEqual(status['state'],'cleaned')
        self.assertFalse(self.folder(request).exists())

    def test_failed_terminal_restored_from_committed_output(self):
        request=self.request();output=self.execution.execute(request,self.snapshot)
        (self.folder(request)/'terminal.json').write_text('{"status":"failed"}')
        with patch('project_data_service.knowledge_execution.subprocess.run',side_effect=AssertionError('no replay')):
            self.assertEqual(self.execution.execute(request,self.snapshot),output)
        self.assertEqual(json.loads((self.folder(request)/'terminal.json').read_text())['status'],'prepared')

    def test_preexisting_write_target_symlinks_never_touch_external(self):
        for name in ('input-manifest.json','stderr.log','output.pending','terminal.json'):
            request=self.request();request['requestId']+='-'+name
            folder=self.folder(request);folder.mkdir()
            target=self.root/('outside-'+name);target.write_text('keep')
            (folder/name).symlink_to(target)
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'SYMLINK'):
                self.execution.execute(request,self.snapshot)
            self.assertEqual(target.read_text(),'keep')

    def test_unknown_file_blocks_first_archive(self):
        request=self.request();output=self.execution.execute(request,self.snapshot)
        unknown=self.folder(request)/'unowned-user-data.txt';unknown.write_text('keep')
        with self.assertRaisesRegex(ValueError,'UNKNOWN|UNEXPECTED'):
            self.execution.finalize(request,self.receipt(request,output),self.store)
        self.assertEqual(unknown.read_text(),'keep')

    def prepared_service(self):
        self.execution=KnowledgeExecution(self.root/'knowledge-worker-scratch')
        def selector(r):
            s=self.content.snapshot();registry=s['documents']['categories/test/entries/laya-worker-registry.json']
            return {'requestHash':digest(r),'registryHash':digest(registry['data']),
                    'knowledgeSource':{'entryVersion':registry['version'],'sourceVersion':s['sourceVersion']},
                    'selectionQuality':{'score':1,'threshold':.8,'passed':True},
                    'stages':[{'id':'knowledge.create','workerId':'knowledge-mutation'}],'invocations':[{}]}
        return KnowledgeRequests(self.store,selector,content=self.content,executor=self.execution)

    def test_superseded_completed_replay_still_cleans_owned_scratch(self):
        service=self.prepared_service();request=self.request()
        with patch('project_data_service.knowledge_execution.shutil.rmtree',side_effect=OSError('busy')):
            service.submit(request)
        self.content.recover=lambda *a: (_ for _ in ()).throw(ValueError('KNOWLEDGE_PRIOR_RESULT_SUPERSEDED_NO_REPLAY'))
        result=service.submit(request)
        self.assertTrue(result['resultSuperseded'])
        self.assertFalse(self.folder(request).exists())
        self.assertEqual(self.content.calls,1)

    def test_model_free_tick_cleans_completed_but_preserves_failed(self):
        from project_data_service.knowledge_cleanup_runtime import KnowledgeCleanupRuntime
        service=self.prepared_service();request=self.request()
        with patch('project_data_service.knowledge_execution.shutil.rmtree',side_effect=OSError('busy')):
            service.submit(request)
        failed=self.request();failed['requestId']='failed-request';failed['entryId']='failed-entry';failed['entry']['id']='failed-entry'
        self.content.apply=lambda *a,**k: (_ for _ in ()).throw(OSError('offline'))
        with self.assertRaises(OSError):service.submit(failed)
        with patch('project_data_service.knowledge_execution.subprocess.run',side_effect=AssertionError('no worker')):
            result=KnowledgeCleanupRuntime(self.root/'state.sqlite').tick()
        self.assertEqual([v['requestId'] for v in result],[request['requestId']])
        self.assertFalse(self.folder(request).exists())
        self.assertTrue(self.folder(failed).exists())
        self.assertEqual(service.view(failed['requestId'])['state'],'failed')

    def test_active_lock_rejects_second_execution(self):
        request=self.request()
        with self.execution.locked(request):
            with self.assertRaisesRegex(ValueError,'EXECUTION_BUSY'):
                self.execution.execute(request,self.snapshot)
        self.assertFalse(self.folder(request).exists())

    def test_other_process_cannot_enter_held_request_lock(self):
        request=self.request()
        script="""import json,sys
from project_data_service.knowledge_execution import KnowledgeExecution
x=json.load(sys.stdin)
try:
 with KnowledgeExecution(x['root']).locked(x['request']): print('entered')
except ValueError as error: print(str(error))
"""
        env={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]),'PYTHONDONTWRITEBYTECODE':'1'}
        with self.execution.locked(request):
            result=subprocess.run([sys.executable,'-B','-c',script],input=json.dumps({'root':str(self.execution.root),'request':request}),text=True,capture_output=True,env=env,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'KNOWLEDGE_EXECUTION_BUSY')

    def test_unknown_started_job_never_reexecutes(self):
        request=self.request();folder=self.folder(request);folder.mkdir()
        (folder/'started.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'OUTCOME_UNKNOWN_NO_REEXECUTION'):
            self.execution.execute(request,self.snapshot)
        self.assertFalse((folder/'output.json').exists())

    def test_corrupt_archive_does_not_remove_scratch(self):
        request=self.request();output=self.execution.execute(request,self.snapshot);receipt=self.receipt(request,output)
        with patch('project_data_service.knowledge_execution.shutil.rmtree',side_effect=OSError('busy')):
            with self.assertRaises(OSError): self.execution.finalize(request,receipt,self.store)
        with self.store.connection:
            self.store.connection.execute('UPDATE knowledge_execution_archives SET archive=?',(b'corrupt',))
        with self.assertRaisesRegex(ValueError,'ARCHIVE_HASH_MISMATCH'):
            self.execution.finalize(request,receipt,self.store)
        self.assertTrue(self.folder(request).exists())

    def test_symlink_is_preserved_and_external_target_untouched(self):
        request=self.request();output=self.execution.execute(request,self.snapshot)
        external=self.root/'external.txt';external.write_text('keep')
        (self.folder(request)/'link').symlink_to(external)
        with self.assertRaisesRegex(ValueError,'SYMLINK'):
            self.execution.finalize(request,self.receipt(request,output),self.store)
        self.assertEqual(external.read_text(),'keep')
        self.assertTrue(self.folder(request).exists())

    def test_changed_bytes_after_archive_deny_retirement(self):
        request=self.request();output=self.execution.execute(request,self.snapshot)
        receipt=self.receipt(request,output)
        with patch('project_data_service.knowledge_execution.shutil.rmtree',side_effect=OSError('busy')):
            with self.assertRaises(OSError):self.execution.finalize(request,receipt,self.store)
        (self.folder(request)/'stderr.log').write_text('changed')
        with self.assertRaisesRegex(ValueError,'EQUIVALENCE_FAILED'):
            self.execution.finalize(request,receipt,self.store)
        self.assertTrue(self.folder(request).exists())

    def test_cleanup_failure_retry_does_not_republish(self):
        request=self.request()
        def selector(r):
            s=self.content.snapshot();registry=s['documents']['categories/test/entries/laya-worker-registry.json']
            return {'requestHash':digest(r),'registryHash':digest(registry['data']),
                    'knowledgeSource':{'entryVersion':registry['version'],'sourceVersion':s['sourceVersion']},
                    'selectionQuality':{'score':1,'threshold':.8,'passed':True},
                    'stages':[{'id':'knowledge.create','workerId':'knowledge-mutation'}],'invocations':[{}]}
        service=KnowledgeRequests(self.store,selector,content=self.content,executor=self.execution)
        with patch('project_data_service.knowledge_execution.shutil.rmtree',side_effect=OSError('busy')):
            service.submit(request)
        self.assertEqual(service.view(request['requestId'])['state'],'completed')
        self.assertTrue(self.folder(request).exists())
        service.submit(request)
        self.assertFalse(self.folder(request).exists())
        self.assertEqual(self.content.calls,1)
        self.assertEqual(service.view(request['requestId'])['cleanup']['state'],'cleaned')


del TestKnowledgeRequests
if __name__=='__main__':unittest.main()
