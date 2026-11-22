"""Acquire private, fixed GitHub Actions evidence for the OPS source preparer."""
import re
from urllib.parse import urlencode
import zipfile

from github_actions_keychain import (
    API, FINE_GRAINED, ID, SHA as SHA_TEXT, KeychainGitHubEvidence,
    artifact_location, keychain_token,
)
from prepare_ngs_release import DIGEST, REF, SHA, Refused, json_load, matches, need, number, shape
from prepare_service_targets import LIMIT, WORKERS, SERVICES, WORKFLOWS, verify_target_artifact

MAIN = 'git/ref/heads/main'
ARTIFACT_DOWNLOAD = re.compile(rf'actions/artifacts/{ID}/zip')
METADATA_LIMIT = 512*1024
_HEADERS = {'Accept':'application/vnd.github+json', 'User-Agent':'ngs-ops-target-candidate',
            'X-GitHub-Api-Version':'2022-11-28', 'Cache-Control':'no-cache'}


def _repo(value):
    if value in ('ngs-workers','ngs-services'):
        value = 'pvenus/' + value
    need(value in (WORKERS,SERVICES),'GITHUB_TARGET_REPO_DENIED')
    return value


def _route(route):
    need(type(route) is str and route.startswith('repos/'),'GITHUB_TARGET_ROUTE_DENIED')
    candidates = [('repos/'+repo+'/', family+'-targets') for repo,family in WORKFLOWS.items()]
    hit = next(((prefix, stem) for prefix,stem in candidates if route.startswith(prefix)),None)
    need(hit,'GITHUB_TARGET_ROUTE_DENIED')
    prefix,stem = hit
    path = route[len(prefix):]
    if path == MAIN:
        return 'main'
    if ARTIFACT_DOWNLOAD.fullmatch(path):
        return 'archive'
    allowed = re.fullmatch(
        rf'actions/(?:workflows/(?:prepare-{stem}\.yml|{ID}/runs\?branch=main&event=push&head_sha={SHA_TEXT}&per_page=1)'
        rf'|runs/{ID}(?:/artifacts\?name={stem}-{SHA_TEXT}-{ID}&per_page=2)?|artifacts/{ID})',path)
    need(allowed,'GITHUB_TARGET_ROUTE_DENIED')
    return 'actions'


class TargetGitHubEvidence(KeychainGitHubEvidence):
    """Fixed pair of repositories using the existing narrow three-repo Keychain item."""

    def __init__(self, *, token=keychain_token, opener=None):
        super().__init__(token=token,opener=opener)

    def _headers(self, method):
        headers = dict(_HEADERS)
        if method != 'main':
            if self._pat is None:
                token = self._get_token()
                need(type(token) is str and token.isascii() and FINE_GRAINED.fullmatch(token.encode())
                     and '\n' not in token,'GITHUB_TARGET_KEYCHAIN_ITEM_INVALID')
                self._pat = token
            headers['Authorization'] = 'Bearer ' + self._pat
        return headers

    def metadata(self, route, *, authorized=None):
        kind = _route(route)
        need(kind != 'archive' and (authorized is None or authorized is (kind=='actions')),
             'GITHUB_TARGET_ROUTE_DENIED')
        raw = self._request('https://'+API+'/'+route,headers=self._headers(kind),
                            status=200,limit=METADATA_LIMIT,timeout=12)
        try:
            value = json_load(raw)
        except (UnicodeError,ValueError,RecursionError):
            raise Refused('GITHUB_TARGET_METADATA_INVALID') from None
        need(type(value) is dict,'GITHUB_TARGET_METADATA_INVALID')
        return value

    def archive(self, route, expected_bytes):
        need(_route(route) == 'archive' and type(expected_bytes) is int and 0<expected_bytes<=LIMIT,
             'GITHUB_TARGET_ROUTE_DENIED')
        location = self._request('https://'+API+'/'+route,headers=self._headers('archive'),
                                 status=302,limit=expected_bytes,timeout=12)
        raw = self._request(artifact_location(location),
              headers={'Accept':'application/octet-stream','User-Agent':_HEADERS['User-Agent']},
              status=200,limit=expected_bytes,timeout=30)
        need(len(raw)==expected_bytes,'GITHUB_TARGET_ARTIFACT_SIZE')
        return raw

    def read(self, *_args, **_kwargs):
        raise Refused('GITHUB_TARGET_ROUTE_DENIED')


def canonical_target_main(repo_name, *, transport=None):
    repo = _repo(repo_name)
    transport = transport if transport is not None else TargetGitHubEvidence()
    value = transport.metadata(f'repos/{repo}/{MAIN}',authorized=False)
    obj = value.get('object')
    need(value.get('ref') == REF and type(obj) is dict and obj.get('type') == 'commit'
         and matches(SHA,obj.get('sha')),'GITHUB_TARGET_MAIN_INVALID')
    return obj['sha']


def _run_claim(run, workflow, repo, head):
    stem = WORKFLOWS[repo] + '-targets'
    path = '.github/workflows/prepare-' + stem + '.yml'
    need(workflow.get('path') == path and run.get('path') == path
         and type(run.get('workflow_id')) is int and run['workflow_id'] == int(number(workflow['id']))
         and run.get('head_sha') == head and run.get('head_branch') == 'main'
         and run.get('event') == 'push' and run.get('status') == 'completed'
         and run.get('conclusion') == 'success','GITHUB_TARGET_RUN_NOT_READY')
    rid,attempt = number(run['id']),number(run['run_attempt'])
    a,b = run['repository'],run['head_repository']
    repo_id = int(number(a['id']))
    need(a.get('full_name') == b.get('full_name') == repo and a.get('fork') is False
         and b.get('fork') is False and type(b.get('id')) is int and b['id']==repo_id,
         'GITHUB_TARGET_RUN_SOURCE_DENIED')
    return rid,attempt,repo_id


def _artifact_claim(obj, aid, name, rid, head, repo_id):
    size = obj.get('size_in_bytes')
    proof = obj.get('workflow_run')
    digest = obj.get('digest')
    need(number(obj['id']) == aid and obj.get('name') == name and obj.get('expired') is False
         and type(size) is int and 0<size<=LIMIT and type(digest) is str
         and digest.startswith('sha256:') and matches(DIGEST,digest[7:])
         and type(proof) is dict and type(proof.get('id')) is int and proof['id']==int(rid)
         and proof.get('head_sha') == head and proof.get('head_branch') == 'main'
         and all(type(proof.get(key)) is int and proof[key]==repo_id
                 for key in ('repository_id','head_repository_id')),'GITHUB_TARGET_ARTIFACT_UNVERIFIED')
    return size,digest[7:]


def acquire_target_candidate(repo_name, *, transport=None, known=None):
    """Return exact original evidence plus verified proof, or None for verified no-op.

    Only use known from a prior verified acquisition, never a request parameter.
    """
    repo = _repo(repo_name)
    transport = transport if transport is not None else TargetGitHubEvidence()
    prefix = f'repos/{repo}/'
    stem = WORKFLOWS[repo]+'-targets'
    meta = lambda path:transport.metadata(prefix+path,authorized=True)
    try:
        head = canonical_target_main(repo,transport=transport)
        workflow = meta('actions/workflows/prepare-'+stem+'.yml')
        workflow_id = number(workflow['id'])
        query = urlencode(dict(branch='main',event='push',head_sha=head,per_page=1))
        runs_route = f'actions/workflows/{workflow_id}/runs?{query}'
        listed = meta(runs_route)
        runs = listed['workflow_runs']
        need(type(listed['total_count']) is int and listed['total_count']>0 and type(runs) is list
             and len(runs)==1,'GITHUB_TARGET_RUN_NOT_READY')
        listed_id = number(runs[0]['id'])
        run = meta(f'actions/runs/{listed_id}')
        rid,attempt,repo_id = _run_claim(run,workflow,repo,head)
        need(rid==listed_id and number(runs[0]['run_attempt'])==attempt,'GITHUB_TARGET_RUN_MOVED')
        name = f'{stem}-{head}-{attempt}'
        listed = meta(f'actions/runs/{rid}/artifacts?'+urlencode(dict(name=name,per_page=2)))
        assets = listed['artifacts']
        need(type(listed['total_count']) is int and listed['total_count']==1 and type(assets) is list
             and len(assets)==1,'GITHUB_TARGET_ARTIFACT_AMBIGUOUS')
        aid = number(assets[0]['id'])
        artifact = meta(f'actions/artifacts/{aid}')
        size,sha = _artifact_claim(artifact,aid,name,rid,head,repo_id)
        identity = dict(head=head,runId=rid,runAttempt=attempt,artifactId=aid,artifactSha256=sha)
        unchanged = known == identity
        if not unchanged:
            raw = transport.archive(prefix+f'actions/artifacts/{aid}/zip',size)
            verified = verify_target_artifact(head,workflow,run,artifact,raw,repo=repo)
        latest = meta(runs_route)
        recent = latest['workflow_runs']
        need(type(recent) is list and len(recent)==1 and number(recent[0]['id'])==rid
             and number(recent[0]['run_attempt'])==attempt
             and recent[0]['status']=='completed' and recent[0]['conclusion']=='success',
             'GITHUB_TARGET_RUN_MOVED')
        need(canonical_target_main(repo,transport=transport)==head,'GITHUB_TARGET_MAIN_MOVED')
        return None if unchanged else dict(head=head,workflow=workflow,run=run,artifact=artifact,raw=raw,verified=verified)
    except Refused:
        raise
    except (AttributeError,KeyError,TypeError,ValueError,UnicodeError,RecursionError,OSError,zipfile.BadZipFile):
        raise Refused('GITHUB_TARGET_EVIDENCE_INVALID') from None
