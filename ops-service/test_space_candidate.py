import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import space_candidate as candidate


class SpaceCandidateTests(unittest.TestCase):
    old = 'a'*40
    new = 'b'*40

    def fixture(self):
        self.now = 10
        self.sha = self.old
        self.final = None
        self.calls = []
        self.repo = dict(id=39, full_name=candidate.REPO, fork=False)
        self.run = dict(id=14, run_attempt=2, workflow_id=43, path=candidate.WORKFLOW,
                        head_sha=self.sha, head_branch='main', event='push', status='completed',
                        conclusion='success', repository=self.repo, head_repository=dict(self.repo))
        self.entries = [self.run]
        def fetch(path):
            self.calls.append(path)
            if path == candidate.REF_PATH:
                checked = self.final if self.final and len(self.calls) % 3 == 0 else self.sha
                return dict(ref=candidate.REF, object=dict(type='commit', sha=checked))
            parsed = urlsplit(path)
            self.assertEqual(parsed.path, candidate.RUNS_PATH)
            self.assertEqual(parse_qs(parsed.query), {'branch':['main'], 'event':['push'],
                             'head_sha':[self.sha], 'per_page':['2']})
            return dict(workflow_runs=self.entries, total_count=len(self.entries))
        return candidate.SpaceCandidate(fetch, lambda: self.now, 15)

    def test_verified_current_lazy_ttl_and_forced_refresh(self):
        app = self.fixture()
        self.assertEqual(self.calls, [])
        self.assertEqual(app.current(), self.old)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(app.current(), self.old)
        self.assertEqual(len(self.calls), 3)
        self.now += 16
        self.sha = self.run['head_sha'] = self.new
        self.assertEqual(app.current(), self.new)
        self.assertEqual(len(self.calls), 6)
        self.run['status'] = 'in_progress'
        self.assertIsNone(app.current(force=True))
        self.assertIsNone(app.current())

    def test_wrong_fork_ref_event_path_or_ambiguous_result_is_refused(self):
        for override in (
            {'head_repository': {'id':39,'full_name':'pvenus/ngs-platform','fork':True}},
            {'head_branch':'feature'}, {'event':'pull_request'}, {'head_sha':self.new},
            {'path':'other.yml'}, {'conclusion':'failure'}, {'id':True},
        ):
            with self.subTest(override=override):
                app = self.fixture()
                self.run.update(override)
                self.assertIsNone(app.current())
        app = self.fixture()
        self.entries.append(dict(self.run))
        self.assertIsNone(app.current())

    def test_main_moving_during_verification_is_refused(self):
        app = self.fixture()
        self.final = self.new
        self.assertIsNone(app.current())

    def test_refresh_after_transport_error_never_reuses_yesterday_proof(self):
        app = self.fixture()
        self.assertEqual(app.current(), self.old)
        self.now += 16
        def offline(_):
            raise OSError('private detail')
        app._fetch = offline
        self.assertIsNone(app.current())

    def test_native_transport_refuses_redirect_not_logs_or_credentials(self):
        class Response:
            status = 302
            def read(self, n):
                raise AssertionError('must not read redirected body')
        with patch.object(candidate._github_connection, 'request') as request, patch.object(
                candidate._github_connection, 'getresponse', return_value=Response()):
            with self.assertRaisesRegex(ValueError, 'GITHUB_METADATA_UNAVAILABLE'):
                candidate.public_github_json(candidate.REF_PATH)
            self.assertEqual(request.call_args[0], ('GET', candidate.REF_PATH))
            self.assertNotIn('Authorization', request.call_args.kwargs['headers'])


if __name__ == '__main__':
    unittest.main()
