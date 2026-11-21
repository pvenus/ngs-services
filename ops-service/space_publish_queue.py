"""Private owner-intent queue for one fixed Site; never contacts or publishes to Sites."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid

from deploy_control import bounded_private, is_sha, secure_directory, stamp
from deploy_cycle import SPACE_HOST, SPACE_SITE, exact, space_handoff, space_publication

TARGET = "ngs-space"
REPOSITORY = "pvenus/ngs-platform"
QUEUE_SCHEMA = "ops.space-publish-queue.v1"
PUB_STATES = {"pending", "claimed", "succeeded", "failed", "uncertain"}
EXCLUSIVE_STATES = {"pending", "claimed", "uncertain"}
LIMIT = 8
MAX_BYTES = 32768
REQUEST_ID = re.compile(r"space-publish\.[0-9a-f]{32}")
THREAD_ID = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")
PROVIDER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:~-]{0,159}")
FAILURES = {"SOURCE_VERIFICATION_FAILED", "AUDIENCE_MISMATCH", "NATIVE_PUBLISH_REJECTED", "NATIVE_PUBLISH_FAILED"}
UNCERTAINTIES = {"NATIVE_RESULT_UNKNOWN", "NATIVE_PROOF_UNAVAILABLE"}


class QueueRejected(ValueError):
    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code = status, code


def need(test, status, code):
    if not test:
        raise QueueRejected(status, code)


def fixed(pattern, value):
    return type(value) is str and bool(pattern.fullmatch(value))


def unique(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, 503, "OPS_SPACE_QUEUE_INVALID")
        result[key] = value
    return result


def read(path, limit=MAX_BYTES):
    return json.loads(bounded_private(path, limit), object_pairs_hook=unique)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def atomic(path, value):
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    need(0 < len(raw) <= MAX_BYTES, 503, "OPS_SPACE_QUEUE_LIMIT")
    fd, tmp = tempfile.mkstemp(prefix=".space-publish-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as writer:
            writer.write(raw)
            writer.flush()
            os.fsync(writer.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def native_result(report, commit):
    if type(report) is not dict or type(report.get("state")) is not str or report["state"] not in {"succeeded", "failed", "uncertain"}:
        raise QueueRejected(400, "OPS_SPACE_RESULT_INVALID")
    state = report["state"]
    if state == "succeeded":
        need(exact(report, "state", "commitSha", "versionId", "providerSha", "deploymentId", "deployedAt")
             and report["commitSha"] == commit and is_sha(report["providerSha"])
             and fixed(PROVIDER_ID, report["versionId"]) and fixed(PROVIDER_ID, report["deploymentId"]),
             400, "OPS_SPACE_RESULT_INVALID")
        try:
            stamp(report["deployedAt"])
        except (ValueError, TypeError, OverflowError):
            raise QueueRejected(400, "OPS_SPACE_RESULT_INVALID") from None
    else:
        options = FAILURES if state == "failed" else UNCERTAINTIES
        need(exact(report, "state", "code") and type(report["code"]) is str and report["code"] in options,
             400, "OPS_SPACE_RESULT_INVALID")
    return report


class SpacePublishQueue:
    """One exclusive ticket, sparse bounded history, and no automatic reclaims.

    Routes authenticate the owner intake separately. Machine transport must supply
    the verified dedicated thread identity, not a caller-controlled header/field.
    """

    def __init__(self, root, *, clock=now):
        self.root = Path(root)
        self.path = self.root / "targets" / TARGET
        self.file = self.path / "publish-queue.json"
        self.clock = clock

    @contextmanager
    def _lock(self):
        for p in (self.root, self.path.parent, self.path):
            secure_directory(p)
        path = self.path / "publish-queue.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            s = os.fstat(fd)
            need(stat.S_ISREG(s.st_mode) and stat.S_IMODE(s.st_mode) == 0o600 and s.st_uid == os.getuid()
                 and s.st_nlink == 1 and path.lstat().st_ino == s.st_ino,
                 503, "OPS_SPACE_QUEUE_INVALID")
            fcntl.flock(fd, fcntl.LOCK_EX)
            need(path.lstat().st_ino == s.st_ino, 503, "OPS_SPACE_QUEUE_INVALID")
            yield
        finally:
            os.close(fd)

    def _thread(self):
        publication = space_publication(self.path)
        item = space_handoff(self.path)
        need(publication is not None and item is not None, 503, "OPS_SPACE_QUEUE_NOT_CONFIGURED")
        return item["threadId"]

    def _authorize(self, thread):
        need(fixed(THREAD_ID, thread) and thread == self._thread(), 403, "OPS_SPACE_WRONG_THREAD")

    def _value(self):
        try:
            v = read(self.file)
        except FileNotFoundError:
            return {"schemaVersion": QUEUE_SCHEMA, "items": []}
        need(exact(v, "schemaVersion", "items") and v["schemaVersion"] == QUEUE_SCHEMA
             and type(v["items"]) is list and len(v["items"]) <= LIMIT, 503, "OPS_SPACE_QUEUE_INVALID")
        ids, commits = set(), set()
        for i, t in enumerate(v["items"]):
            need(exact(t, "requestId", "commitSha", "state", "queuedAt", "claimedAt", "finishedAt", "result",
                       "threadId") and fixed(REQUEST_ID, t["requestId"])
                 and is_sha(t["commitSha"]) and type(t["state"]) is str and t["state"] in PUB_STATES
                 and fixed(THREAD_ID, t["threadId"])
                 and t["requestId"] not in ids and t["commitSha"] not in commits
                 and (i == 0 or t["state"] not in EXCLUSIVE_STATES), 503, "OPS_SPACE_QUEUE_INVALID")
            ids.add(t["requestId"]); commits.add(t["commitSha"])
            queued = stamp(t["queuedAt"])
            if t["state"] == "pending":
                need(t["claimedAt"] is t["finishedAt"] is t["result"] is None, 503, "OPS_SPACE_QUEUE_INVALID")
                continue
            claimed = stamp(t["claimedAt"])
            need(queued <= claimed, 503, "OPS_SPACE_QUEUE_INVALID")
            if t["state"] == "claimed":
                need(t["finishedAt"] is t["result"] is None, 503, "OPS_SPACE_QUEUE_INVALID")
            else:
                need(claimed <= stamp(t["finishedAt"]) and type(t["result"]) is dict
                     and t["result"].get("state") == t["state"], 503, "OPS_SPACE_QUEUE_INVALID")
                native_result(t["result"], t["commitSha"])
        return v

    @staticmethod
    def _public(t):
        return {k: t[k] for k in ("requestId", "commitSha", "state", "queuedAt", "claimedAt", "finishedAt", "result")}

    @classmethod
    def _authorized(cls, t):
        return {**cls._public(t), "projectId": SPACE_SITE, "hostname": SPACE_HOST, "threadId": t["threadId"],
                "repository": REPOSITORY}

    def status(self):
        # Atomically replaced, no lockfile creation or mutations on a read.
        for p in (self.root, self.path.parent, self.path):
            secure_directory(p)
        items = self._value()["items"]
        return self._public(items[0]) if items else None

    def last_success(self):
        for p in (self.root,self.path.parent,self.path):
            secure_directory(p)
        return next((t['result'] for t in self._value()['items'] if t['state']=='succeeded'), None)

    def for_thread(self, *, thread_id):
        for p in (self.root, self.path.parent, self.path):
            secure_directory(p)
        self._authorize(thread_id)
        items = self._value()["items"]
        if not items:
            return None
        need(items[0]["threadId"] == thread_id, 409, "OPS_SPACE_THREAD_CHANGED")
        # A read, even for a previously claimed ticket, grants no permission.
        return self._authorized(items[0])

    def enqueue(self, commit_sha, *, verified_sha):
        # verified_sha comes from the caller's forced canonical GitHub check,
        # never from the owner HTTP payload or local NGS preparation.
        need(is_sha(commit_sha), 400, "OPS_SPACE_COMMIT_INVALID")
        need(is_sha(verified_sha) and commit_sha == verified_sha, 409, "OPS_SPACE_SOURCE_NOT_PREPARED")
        with self._lock():
            thread = self._thread()
            v = self._value()
            items = v["items"]
            for t in items:
                if t["commitSha"] == commit_sha:
                    return self._public(t)
            need(not items or items[0]["state"] not in EXCLUSIVE_STATES, 409, "OPS_SPACE_QUEUE_BUSY")
            queued = self.clock()
            stamp(queued)
            t = {"requestId": "space-publish." + uuid.uuid4().hex, "commitSha": commit_sha, "state": "pending",
                 "queuedAt": queued, "claimedAt": None, "finishedAt": None, "result": None,
                 "threadId": thread}
            v["items"] = [t, *items[:LIMIT - 1]]
            atomic(self.file, v)
            return self._public(t)

    def _ticket(self, v, request_id, thread):
        self._authorize(thread)
        need(fixed(REQUEST_ID, request_id), 400, "OPS_SPACE_REQUEST_ID_INVALID")
        entries = v["items"]
        t = next((row for row in entries if row["requestId"] == request_id), None)
        need(t is not None, 404, "OPS_SPACE_REQUEST_NOT_FOUND")
        need(t["threadId"] == thread, 409, "OPS_SPACE_THREAD_CHANGED")
        return t

    def claim(self, request_id, *, thread_id):
        with self._lock():
            v = self._value()
            t = self._ticket(v, request_id, thread_id)
            need(t["state"] == "pending", 409, "OPS_SPACE_NOT_CLAIMABLE")
            claimed = self.clock()
            need(stamp(claimed) >= stamp(t["queuedAt"]), 503, "OPS_SPACE_CLOCK_DRIFT")
            t.update(state="claimed", claimedAt=claimed)
            atomic(self.file, v)
            return self._authorized(t)

    def finish(self, request_id, report, *, thread_id):
        with self._lock():
            v = self._value()
            t = self._ticket(v, request_id, thread_id)
            report = native_result(report, t["commitSha"])
            if t["state"] in {"succeeded", "failed", "uncertain"} and t["result"] == report:
                return self._public(t)
            need(t["state"] == "claimed", 409, "OPS_SPACE_NOT_FINISHABLE")
            finished = self.clock()
            need(stamp(finished) >= stamp(t["claimedAt"]), 503, "OPS_SPACE_CLOCK_DRIFT")
            t.update(state=report["state"], finishedAt=finished, result=report)
            atomic(self.file, v)
            return self._public(t)
