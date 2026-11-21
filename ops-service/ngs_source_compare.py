"""Compare immutable canonical NGS Local subtrees, not repository-wide versions."""
from collections import OrderedDict
import http.client
from threading import Lock
import time

from deploy_control import is_sha
from space_candidate import REPO, public_github_json


class NgsSourceCompare:
    def __init__(self, fetch=public_github_json, clock=time.monotonic):
        self._fetch, self._clock = fetch, clock
        self._lock = Lock()
        self._cache = OrderedDict()

    def _local_tree(self, commit):
        with self._lock:
            saved = self._cache.get(commit)
            if saved is not None and saved[1] > self._clock():
                self._cache.move_to_end(commit)
                return saved[0]
            try:
                meta = self._fetch(f'/repos/{REPO}/git/commits/{commit}')
                root = meta['tree']['sha']
                if meta['sha'] != commit or not is_sha(root):
                    raise ValueError('NGS_COMMIT_IDENTITY_MISMATCH')
                tree = self._fetch(f'/repos/{REPO}/git/trees/{root}')
                if tree['sha'] != root or tree['truncated'] is not False or type(tree['tree']) is not list:
                    raise ValueError('NGS_TREE_UNVERIFIED')
                matches = [x for x in tree['tree'] if x.get('path') == 'ngs-local']
                if len(matches) != 1 or matches[0]['type'] != 'tree' or not is_sha(matches[0]['sha']):
                    raise ValueError('NGS_LOCAL_TREE_UNVERIFIED')
                value = matches[0]['sha']
            except (OSError, ValueError, KeyError, TypeError, AttributeError, http.client.HTTPException):
                value = None
            self._cache[commit] = (value, self._clock() + (86400 if value else 30))
            self._cache.move_to_end(commit)
            if len(self._cache) > 64:
                self._cache.popitem(last=False)
            return value

    def classify(self, local, space):
        if not is_sha(local) or not is_sha(space):
            return 'unknown'
        if local == space:
            return 'same-commit'
        a, b = self._local_tree(local), self._local_tree(space)
        if a is None or b is None:
            return 'unknown'
        return 'same-local-source' if a == b else 'different-local-source'
