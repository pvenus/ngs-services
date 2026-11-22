"""Read-only proof of the exact canonical NGS main source candidate."""
import http.client
import json
import re
import threading
import time
from urllib.parse import urlencode

from deploy.github_actions_keychain import KeychainGitHubEvidence
from deploy.prepare_ngs_release import json_load

REPO = 'pvenus/ngs-platform'
REF = 'refs/heads/main'
WORKFLOW = '.github/workflows/prepare-ngs-release.yml'
REF_PATH = f'/repos/{REPO}/git/ref/heads/main'
WORKFLOWS_PATH = f'/repos/{REPO}/actions/workflows/'
WORKFLOW_PATH = WORKFLOWS_PATH+'prepare-ngs-release.yml'
SPACE_RUNS = re.compile(re.escape(WORKFLOWS_PATH)+
    r'[1-9][0-9]{0,19}/runs\?branch=main&event=push&head_sha=[0-9a-f]{40}&per_page=1')
SHA = re.compile(r'[0-9a-f]{40}')
MAX_BODY = 64 * 1024


def public_github_json(path):
    # Direct TLS only: do not use proxy/redirect helpers or ambient credentials.
    with _github_connection() as conn:
        conn.request('GET', path, headers={
            'Accept':'application/vnd.github+json', 'User-Agent':'vinus-ops-space-candidate',
            'X-GitHub-Api-Version':'2022-11-28', 'Cache-Control':'no-cache',
        })
        response = conn.getresponse()
        if response.status != 200 or response.headers.get_content_type() != 'application/json':
            raise ValueError('GITHUB_METADATA_UNAVAILABLE')
        length = response.getheader('Content-Length')
        if length is not None and (not length.isdecimal() or int(length) > MAX_BODY):
            raise ValueError('GITHUB_METADATA_LIMIT')
        raw = response.read(MAX_BODY+1)
        if len(raw) > MAX_BODY:
            raise ValueError('GITHUB_METADATA_LIMIT')
        return json.loads(raw)


class _github_connection(http.client.HTTPSConnection):
    def __init__(self):
        super().__init__('api.github.com', 443, timeout=2.5)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _head(data):
    ref = data['object']
    sha = ref['sha']
    if data['ref'] != REF or ref['type'] != 'commit' or not isinstance(sha,str) or not SHA.fullmatch(sha):
        raise ValueError('GITHUB_MAIN_INVALID')
    return sha


def _positive(value):
    return type(value) is int and value > 0


def space_metadata(transport, path):
    # Unlike public tree comparison, this lane may use the Keychain credential.
    # Restrict it to one fixed repo/workflow metadata lane: no artifacts, writes,
    # redirects, or arbitrary GitHub/alternate-host requests.
    if type(path) is not str or (path not in (REF_PATH,WORKFLOW_PATH) and not SPACE_RUNS.fullmatch(path)):
        raise ValueError('GITHUB_SPACE_ROUTE_DENIED')
    return json_load(transport.read(path[1:],limit=MAX_BODY))


class SpaceCandidate:
    def __init__(self, fetch_json=None, clock=time.monotonic, ttl=15):
        self._fetch, self._clock, self._ttl = fetch_json, clock, ttl
        self._lock = threading.Lock()
        self._expiry = float('-inf')
        self._sha = None

    def _verify(self):
        fetch=self._fetch
        if fetch is None:
            transport=KeychainGitHubEvidence()
            fetch=lambda path:space_metadata(transport,path)
        head = _head(fetch(REF_PATH))
        workflow=fetch(WORKFLOW_PATH)
        workflow_id=workflow['id']
        if not _positive(workflow_id) or workflow_id>=10**20 or workflow['path']!=WORKFLOW:
            return None
        query = urlencode(dict(branch='main', event='push', head_sha=head, per_page=1))
        runs = fetch(f'{WORKFLOWS_PATH}{workflow_id}/runs?{query}')
        entries = runs['workflow_runs']
        if type(runs['total_count']) is not int or runs['total_count'] != 1 or not isinstance(entries,list) or len(entries) != 1:
            return None
        run = entries[0]
        repo, head_repo = run['repository'], run['head_repository']
        if not (_positive(run['id']) and _positive(run['run_attempt']) and _positive(run['workflow_id'])
                and run['workflow_id']==workflow_id and _positive(repo['id']) and _positive(head_repo['id'])
                and repo['id'] == head_repo['id']
                and repo['full_name'] == head_repo['full_name'] == REPO and repo['fork'] is False
                and head_repo['fork'] is False and run['path'] == WORKFLOW and run['head_sha'] == head
                and run['head_branch'] == 'main' and run['event'] == 'push'
                and run['status'] == 'completed' and run['conclusion'] == 'success'):
            return None
        return head if _head(fetch(REF_PATH)) == head else None

    def current(self, *, force=False):
        with self._lock:
            if not force and self._clock() < self._expiry:
                return self._sha
            try:
                sha = self._verify()
            except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError, http.client.HTTPException):
                sha = None
            # Do not return a stale positive after a failed refresh.
            self._sha = sha
            self._expiry = self._clock() + self._ttl
            return sha
