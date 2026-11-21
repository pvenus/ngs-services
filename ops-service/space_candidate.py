"""Read-only proof of the exact canonical NGS main source candidate."""
import http.client
import json
import re
import threading
import time
from urllib.parse import urlencode

REPO = 'pvenus/ngs-platform'
REF = 'refs/heads/main'
WORKFLOW = '.github/workflows/prepare-ngs-release.yml'
REF_PATH = f'/repos/{REPO}/git/ref/heads/main'
RUNS_PATH = f'/repos/{REPO}/actions/workflows/prepare-ngs-release.yml/runs'
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


class SpaceCandidate:
    def __init__(self, fetch_json=public_github_json, clock=time.monotonic, ttl=15):
        self._fetch, self._clock, self._ttl = fetch_json, clock, ttl
        self._lock = threading.Lock()
        self._expiry = float('-inf')
        self._sha = None

    def _verify(self):
        head = _head(self._fetch(REF_PATH))
        query = urlencode(dict(branch='main', event='push', head_sha=head, per_page=2))
        runs = self._fetch(f'{RUNS_PATH}?{query}')
        entries = runs['workflow_runs']
        if type(runs['total_count']) is not int or runs['total_count'] != 1 or not isinstance(entries,list) or len(entries) != 1:
            return None
        run = entries[0]
        repo, head_repo = run['repository'], run['head_repository']
        if not (_positive(run['id']) and _positive(run['run_attempt']) and _positive(run['workflow_id'])
                and _positive(repo['id']) and repo['id'] == head_repo['id']
                and repo['full_name'] == head_repo['full_name'] == REPO and repo['fork'] is False
                and head_repo['fork'] is False and run['path'] == WORKFLOW and run['head_sha'] == head
                and run['head_branch'] == 'main' and run['event'] == 'push'
                and run['status'] == 'completed' and run['conclusion'] == 'success'):
            return None
        return head if _head(self._fetch(REF_PATH)) == head else None

    def current(self, *, force=False):
        with self._lock:
            if not force and self._clock() < self._expiry:
                return self._sha
            try:
                sha = self._verify()
            except (OSError, ValueError, KeyError, TypeError, AttributeError, http.client.HTTPException):
                sha = None
            # Do not return a stale positive after a failed refresh.
            self._sha = sha
            self._expiry = self._clock() + self._ttl
            return sha
