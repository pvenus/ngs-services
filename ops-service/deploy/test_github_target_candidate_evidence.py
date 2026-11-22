from email.message import Message
import io
import unittest
import zipfile

import github_target_candidate_evidence as candidate
import prepare_service_targets as p
from prepare_ngs_release import Refused, json_bytes


class EvidenceFixture:
    head = 'a'*40
    def __init__(self, repo=p.WORKERS):
        self.repo = repo
        self.base = 'repos/'+repo+'/'
        self.stem = p.WORKFLOWS[repo]+'-targets'
        self.head_route = self.base+candidate.MAIN
        self.list_route = self.base+f'actions/workflows/5/runs?branch=main&event=push&head_sha={self.head}&per_page=1'
        self.download = self.base+'actions/artifacts/7/zip'
        self.calls = []
        path = '.github/workflows/prepare-'+self.stem+'.yml'
        self.workflow = dict(id=5,path=path)
        repo_meta = dict(full_name=repo,id=9,fork=False)
        self.run = dict(id=6,run_attempt=2,workflow_id=5,path=path,head_sha=self.head,
                      head_branch='main',event='push',status='completed',conclusion='success',
                      repository=repo_meta,head_repository=dict(repo_meta))
        targets={}
        for name,(roots,entries) in p.SPECS[repo].items():
            contents = {r:'b'*40 for r in roots}
            targets[name] = dict(roots=contents,entrypoints=list(entries),
                sourceFingerprint=p.digest(json_bytes(contents).rstrip(b'\n')),checked=dict(python=0,js=0,json=0))
        proof=dict(schemaVersion='ops.target-source-readiness.v1',repository=repo,ref=p.REF,headSha=self.head,
                   runId='6',runAttempt='2',targets=targets)
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as z:
            z.writestr('release.json',json_bytes(proof))
        self.raw=output.getvalue()
        name=f'{self.stem}-{self.head}-2'
        self.asset=dict(id=7,name=name,expired=False,size_in_bytes=len(self.raw),digest='sha256:'+p.digest(self.raw),
                     workflow_run=dict(id=6,head_sha=self.head,head_branch='main',repository_id=9,head_repository_id=9))
        self.proof=proof
        self.identity=dict(head=self.head,runId='6',runAttempt='2',artifactId='7',artifactSha256=p.digest(self.raw))
        self.routes = {self.head_route:dict(ref=p.REF,object=dict(type='commit',sha=self.head)),
            self.base+'actions/workflows/prepare-'+self.stem+'.yml':self.workflow,
            self.list_route:dict(total_count=1,workflow_runs=[dict(self.run)]),
            self.base+'actions/runs/6':self.run,
            self.base+f'actions/runs/6/artifacts?name={name}&per_page=2':dict(total_count=1,artifacts=[dict(id=7)]),
            self.base+'actions/artifacts/7':self.asset}

    def metadata(self,route,*,authorized):
        assert authorized is (route!=self.head_route)
        self.calls.append(route)
        return self.routes[route]

    def archive(self,route,size):
        assert route==self.download and size==self.asset['size_in_bytes']
        self.calls.append(route)
        return self.raw


class Response(io.BytesIO):
    def __init__(self,status=200,raw=b'{}',location=None):
        super().__init__(raw)
        self.status = status
        self.headers = Message()
        if location:
            self.headers['Location'] = location

    def getcode(self):
        return self.status


class Wire:
    def __init__(self,*responses):
        self.responses=iter(responses)
        self.calls=[]

    def open(self,request,*,timeout):
        self.calls.append((request.full_url,dict(request.header_items()),timeout))
        return next(self.responses)


class EvidenceTests(unittest.TestCase):
    def test_two_registered_repos_and_original_evidence(self):
        for repo in (p.WORKERS,p.SERVICES):
            with self.subTest(repo=repo):
                fx=EvidenceFixture(repo)
                result=candidate.acquire_target_candidate(repo.split('/')[1],transport=fx)
                self.assertEqual(result['verified']['proof'],fx.proof)
                self.assertEqual(result['raw'],fx.raw)
                self.assertIs(result['run'],fx.run)
                self.assertEqual(fx.calls.count(fx.head_route),2)
                self.assertEqual(fx.calls.count(fx.download),1)

    def test_noop_binds_exact_attempt_and_digest_without_zip(self):
        fx=EvidenceFixture()
        self.assertIsNone(candidate.acquire_target_candidate(fx.repo,transport=fx,known=fx.identity))
        self.assertNotIn(fx.download,fx.calls)
        self.assertIsNotNone(candidate.acquire_target_candidate(fx.repo,transport=fx,known={**fx.identity,'runAttempt':'1'}))

    def test_wrong_path_fork_invalid_asset_and_tampered_zip_fail_closed(self):
        fx=EvidenceFixture()
        fx.run['head_repository']['fork']=True
        with self.assertRaisesRegex(Refused,'RUN_SOURCE_DENIED'):
            candidate.acquire_target_candidate(fx.repo,transport=fx)
        self.assertNotIn(fx.download,fx.calls)
        fx=EvidenceFixture()
        fx.asset['workflow_run']['repository_id']=10
        with self.assertRaisesRegex(Refused,'ARTIFACT_UNVERIFIED'):
            candidate.acquire_target_candidate(fx.repo,transport=fx)
        self.assertNotIn(fx.download,fx.calls)
        fx=EvidenceFixture()
        fx.raw=fx.raw[:-1]+b'x'
        with self.assertRaisesRegex(Refused,'ARTIFACT_MISMATCH'):
            candidate.acquire_target_candidate(fx.repo,transport=fx)

    def test_changed_attempt_or_main_during_download_is_denied(self):
        for mode, error in [('attempt','GITHUB_TARGET_RUN_MOVED'),('main','GITHUB_TARGET_MAIN_MOVED')]:
            fx=EvidenceFixture()
            archive=fx.archive
            def move(route,size):
                r=archive(route,size)
                if mode=='attempt':
                    fx.routes[fx.list_route]['workflow_runs'][0]['run_attempt']=3
                else:
                    fx.routes[fx.head_route]['object']['sha']='b'*40
                return r
            fx.archive=move
            with self.assertRaisesRegex(Refused,error):
                candidate.acquire_target_candidate(fx.repo,transport=fx)

    def test_fixed_transport_keeps_token_off_public_read_and_asset_redirect(self):
        fx=EvidenceFixture()
        url='https://productionresultssa12.blob.core.windows.net/pkg?sig=fixture'
        wire=Wire(Response(raw=b'{}'),Response(status=302,location=url),Response(raw=b'abc'))
        looked=[]
        def token():
            looked.append(1)
            return 'github_pat_'+'f'*40
        io_=candidate.TargetGitHubEvidence(token=token,opener=wire)
        self.assertEqual(io_.metadata(fx.head_route,authorized=False),{})
        self.assertEqual(looked,[])
        self.assertEqual(io_.archive(fx.download,3),b'abc')
        self.assertEqual(looked,[1])
        self.assertNotIn('Authorization',wire.calls[0][1])
        self.assertIn('Authorization',wire.calls[1][1])
        self.assertEqual(set(wire.calls[2][1]),{'Accept','User-agent'})
        with self.assertRaisesRegex(Refused,'REPO_DENIED'):
            candidate.canonical_target_main('pvenus/unrelated',transport=io_)
        with self.assertRaisesRegex(Refused,'ROUTE_DENIED'):
            io_.metadata(fx.base+'actions/workflows/prepare-service-targets.yml')

    def test_transport_denies_second_redirect_and_wrong_origin(self):
        fx=EvidenceFixture()
        for url,code in [('https://unknown.example/pkg?sig=fixture','REDIRECT_DENIED'),
                         ('https://productionresultssa12.blob.core.windows.net/pkg?sig=fixture','RESPONSE_INVALID')]:
            wire=Wire(Response(status=302,location=url),Response(status=302,location=url))
            io_=candidate.TargetGitHubEvidence(token=lambda:'github_pat_'+'f'*40,opener=wire)
            with self.assertRaisesRegex(Refused,code):
                io_.archive(fx.download,3)


if __name__=='__main__':
    unittest.main()
