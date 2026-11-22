import io
import json
import unittest
from email.message import Message
from urllib.error import HTTPError
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
                checked = self.final if self.final and len(self.calls) % 4 == 0 else self.sha
                return dict(ref=candidate.REF, object=dict(type='commit', sha=checked))
            if path == candidate.WORKFLOW_PATH:
                return dict(id=43,path=candidate.WORKFLOW)
            parsed = urlsplit(path)
            self.assertEqual(parsed.path, candidate.WORKFLOWS_PATH+'43/runs')
            self.assertEqual(parse_qs(parsed.query), {'branch':['main'], 'event':['push'],
                             'head_sha':[self.sha], 'per_page':['1']})
            return dict(workflow_runs=self.entries, total_count=len(self.entries))
        return candidate.SpaceCandidate(fetch, lambda: self.now, 15)

    def test_verified_current_lazy_ttl_and_forced_refresh(self):
        app = self.fixture()
        self.assertEqual(self.calls, [])
        self.assertEqual(app.current(), self.old)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(app.current(), self.old)
        self.assertEqual(len(self.calls), 4)
        self.now += 16
        self.sha = self.run['head_sha'] = self.new
        self.assertEqual(app.current(), self.new)
        self.assertEqual(len(self.calls), 8)
        self.run['status'] = 'in_progress'
        self.assertIsNone(app.current(force=True))
        self.assertIsNone(app.current())

    def test_wrong_fork_ref_event_path_or_ambiguous_result_is_refused(self):
        for override in (
            {'head_repository': {'id':39,'full_name':'pvenus/ngs-platform','fork':True}},
            {'head_branch':'feature'}, {'event':'pull_request'}, {'head_sha':self.new},
            {'path':'other.yml'}, {'conclusion':'failure'}, {'id':True}, {'workflow_id':44},
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

    def test_fixed_space_path_reuses_existing_transport_for_public_git_and_authenticated_actions(self):
        app=self.fixture()
        token='github_pat_'+'X'*50
        events=[]
        class Response(io.BytesIO):
            headers=Message()
            def getcode(self):return 200
        class Opener:
            def open(self,request,timeout):
                events.append(('actions',request.get_method(),request.full_url,request.headers))
                path=urlsplit(request.full_url).path
                return Response(json.dumps(dict(id=43,path=candidate.WORKFLOW) if path==candidate.WORKFLOW_PATH
                    else dict(total_count=1,workflow_runs=[self_run])).encode())
        self_run=self.run
        def main(repo):
            events.append(('public-git',repo))
            return self.old
        def keychain():
            events.append(('keychain',))
            return token
        transport=candidate.KeychainGitHubEvidence(token=keychain,main_ref=main,opener=Opener())
        with patch.object(candidate,'KeychainGitHubEvidence',return_value=transport):
            self.assertEqual(candidate.SpaceCandidate().current(force=True),self.old)
        self.assertEqual([e[0] for e in events],['public-git','keychain','actions','actions','public-git'])
        self.assertEqual(events[0][1],candidate.REPO)
        self.assertEqual(events[-1][1],candidate.REPO)
        self.assertTrue(all(e[1]=='GET' and urlsplit(e[2]).netloc=='api.github.com'
                            and e[3]['Authorization']=='Bearer '+token for e in events if e[0]=='actions'))
        for path in ('https://elsewhere.example/'+candidate.WORKFLOW_PATH,'/repos/other/ngs-platform/actions/workflows/1/runs',
                     f'/repos/{candidate.REPO}/actions/artifacts/123/zip'):
            with self.assertRaisesRegex(ValueError,'GITHUB_SPACE_ROUTE_DENIED'):
                candidate.space_metadata(transport,path)
        self.assertEqual(len(events),5)

    def test_space_does_not_fallback_or_follow_redirect_on_authenticated_action_error(self):
        for status in (302,401,403,429):
            calls=[]
            class Opener:
                def open(self,request,timeout):
                    calls.append(request.full_url)
                    headers=Message()
                    headers['Location']='https://somewhere.example/redirection'
                    raise HTTPError(request.full_url,status,'upstream detail',headers,io.BytesIO(b'private'))
            transport=candidate.KeychainGitHubEvidence(token=lambda:'github_pat_'+'X'*50,
                       main_ref=lambda _:self.old,opener=Opener())
            with self.subTest(status=status),patch.object(candidate,'KeychainGitHubEvidence',return_value=transport):
                self.assertIsNone(candidate.SpaceCandidate().current(force=True))
                self.assertEqual(calls,['https://api.github.com'+candidate.WORKFLOW_PATH])

    def test_native_public_tree_transport_refuses_redirect_not_logs_or_credentials(self):
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
