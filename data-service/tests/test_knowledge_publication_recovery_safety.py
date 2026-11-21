"""Synthetic publication/recovery safety; never uses an installed Filex or model."""
import copy
import json
import unittest

from test_knowledge_requests import TestKnowledgeRequests
from project_data_service.knowledge_requests import digest


class PublicationRecoverySafety(unittest.TestCase):
    setUp = TestKnowledgeRequests.setUp
    tearDown = TestKnowledgeRequests.tearDown
    selection = TestKnowledgeRequests.selection
    request = TestKnowledgeRequests.request
    def authored(self):
        request = self.request()
        request['authoring'] = {'description': '보존한다.'}
        request['entry'].pop('title')
        request['entry'].pop('purpose')
        self.author_calls = 0
        def author(*args):
            self.author_calls += 1
            return {
                'title': '제목', 'purpose': '보존', 'content': '보존한다.',
                'triggers': ['보존'],
                'sections': [{'id': 's1', 'heading': '규칙', 'body': '보존한다.'}],
                'coverage': [{'paragraphId': 'p1', 'sectionIds': ['s1'], 'sourceQuote': '보존'}],
            }, {'modelCalls': 1}
        self.s.authorer = author
        return request

    def prepared(self, request):
        row = self.store.connection.execute(
            'SELECT files_json FROM knowledge_request_files WHERE id=?',
            (request['requestId'],)).fetchone()
        return json.loads(row[0]) if row else None

    def test_publish_failure_keeps_exact_prepared_file_for_retry(self):
        request = self.authored()
        apply = self.content.apply
        self.content.apply = lambda *a, **k: (_ for _ in ()).throw(OSError('offline'))
        with self.assertRaises(OSError):
            self.s.submit(request)
        files = self.prepared(request)
        self.assertTrue(files)
        self.assertEqual(self.s.view(request['requestId'])['state'], 'failed')
        self.assertEqual(self.author_calls, 1)
        self.content.apply = apply
        result = self.s.resume(request['requestId'], digest(request))
        self.assertEqual(result['entry']['content'], files[0]['body']['content'])
        self.assertEqual(self.author_calls, 1)
        self.assertEqual(self.content.calls, 1)
        self.assertIsNone(self.prepared(request))

    def test_response_loss_recovers_without_reauthor_or_republish(self):
        request = self.authored()
        apply = self.content.apply
        def lose_response(*a, **k):
            apply(*a, **k)
            raise OSError('response lost after commit')
        self.content.apply = lose_response
        with self.assertRaises(OSError):
            self.s.submit(request)
        self.assertTrue(self.prepared(request))
        result = self.s.resume(request['requestId'], digest(request))
        self.assertEqual(result['entry']['version'], 1)
        self.assertEqual(self.author_calls, 1)
        self.assertEqual(self.content.calls, 1)
        self.assertIsNone(self.prepared(request))
        self.assertEqual(self.s.view(request['requestId'])['state'], 'completed')

    def test_failed_request_id_collision_cannot_replace_prepared_content(self):
        request = self.authored()
        self.content.apply = lambda *a, **k: (_ for _ in ()).throw(OSError('offline'))
        with self.assertRaises(OSError):
            self.s.submit(request)
        files = self.prepared(request)
        changed = copy.deepcopy(request)
        changed['authoring']['description'] = '다른 내용'
        with self.assertRaisesRegex(ValueError, 'IDEMPOTENCY_CONFLICT'):
            self.s.submit(changed)
        self.assertEqual(files, self.prepared(request))
        self.assertEqual(self.author_calls, 1)

    def test_invalid_publication_receipt_keeps_prepared_file(self):
        request = self.authored()
        self.content.apply = lambda *a, **k: {'requestId': 'wrong'}
        with self.assertRaisesRegex(ValueError, 'RECEIPT_INVALID'):
            self.s.submit(request)
        self.assertTrue(self.prepared(request))
        self.assertEqual(self.s.view(request['requestId'])['state'], 'failed')
        self.assertEqual(self.author_calls, 1)


del TestKnowledgeRequests

if __name__ == '__main__':
    unittest.main()
