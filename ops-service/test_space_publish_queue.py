from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from deploy_cycle import SPACE_HOST, SPACE_SITE
import space_publish_queue as q

A = "1" * 40
B = "2" * 40
P = "a" * 40
T = "01000000-1234-4567-890a-000000000001"
X = "01000000-1234-4567-890a-000000000002"
NOW = "2026-11-22T00:00:00Z"


class SpacePublicationQueueTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.target = self.root / "targets" / "ngs-space"
        self.target.mkdir(mode=0o700, parents=True)
        (self.root / "targets").chmod(0o700)
        q.atomic(self.target / "publication.json", {
            "schemaVersion": "ops.ngs-space-publication.v1", "provider": "chatgpt-sites",
            "projectId": SPACE_SITE, "hostname": SPACE_HOST, "accessMode": "custom",
        })
        self.set_thread(T)
        self.queue = q.SpacePublishQueue(self.root, clock=lambda: NOW)

    def set_thread(self, thread):
        q.atomic(self.target / "handoff.json", {"threadId": thread, "title": "비나리 스페이스 배포"})

    def add(self, sha=A):
        return self.queue.enqueue(sha, verified_sha=sha)

    @staticmethod
    def receipt(sha=A, **more):
        return {"state": "succeeded", "commitSha": sha, "versionId": "native.site~version.1",
                "providerSha": P, "deploymentId": "native.deploy.1", "deployedAt": NOW, **more}

    def test_exact_owner_pending_and_claim_identity(self):
        self.assertIsNone(self.queue.status())
        self.assertFalse((self.target / "publish-queue.lock").exists())
        with self.assertRaisesRegex(q.QueueRejected, "SOURCE_NOT_PREPARED"):
            self.queue.enqueue(A, verified_sha=B)
        self.assertFalse(self.queue.file.exists())
        t = self.add()
        self.assertEqual(t["state"], "pending")
        self.assertEqual(t["queuedAt"], NOW)
        self.assertEqual(self.add(), t)
        self.assertNotIn("threadId", t)
        with self.assertRaisesRegex(q.QueueRejected, "WRONG_THREAD"):
            self.queue.for_thread(thread_id=X)
        for_thread = self.queue.for_thread(thread_id=T)
        self.assertEqual(for_thread["projectId"], SPACE_SITE)
        self.assertEqual(for_thread["repository"], "pvenus/ngs-platform")
        self.assertEqual(for_thread["threadId"], T)
        self.assertEqual(self.queue.claim(t["requestId"], thread_id=T)["state"], "claimed")

    def test_late_or_duplicate_poller_cannot_claim_twice(self):
        req = self.add()["requestId"]
        def contender(_):
            try:
                return self.queue.claim(req, thread_id=T)["state"]
            except q.QueueRejected as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=8) as workers:
            states = list(workers.map(contender, range(12)))
        self.assertEqual(states.count("claimed"), 1)
        self.assertEqual(states.count("OPS_SPACE_NOT_CLAIMABLE"), 11)
        other = q.SpacePublishQueue(self.root, clock=lambda: NOW)
        self.assertEqual(other.for_thread(thread_id=T)["state"], "claimed")
        with self.assertRaisesRegex(q.QueueRejected, "NOT_CLAIMABLE"):
            other.claim(req, thread_id=T)
        with self.assertRaisesRegex(q.QueueRejected, "QUEUE_BUSY"):
            self.add(B)

    def test_native_completion_bound_to_exact_claim_and_receipt_idempotent(self):
        req = self.add()["requestId"]
        with self.assertRaisesRegex(q.QueueRejected, "NOT_FINISHABLE"):
            self.queue.finish(req, self.receipt(), thread_id=T)
        self.queue.claim(req, thread_id=T)
        with self.assertRaisesRegex(q.QueueRejected, "RESULT_INVALID"):
            self.queue.finish(req, self.receipt(B), thread_id=T)
        with self.assertRaisesRegex(q.QueueRejected, "RESULT_INVALID"):
            self.queue.finish(req, self.receipt(extra="unapproved full logs"), thread_id=T)
        with self.assertRaisesRegex(q.QueueRejected, "RESULT_INVALID"):
            self.queue.finish(req, {"state": []}, thread_id=T)
        done = self.queue.finish(req, self.receipt(), thread_id=T)
        self.assertEqual(done["state"], "succeeded")
        self.assertEqual(self.queue.finish(req, self.receipt(), thread_id=T), done)
        with self.assertRaisesRegex(q.QueueRejected, "NOT_FINISHABLE"):
            self.queue.finish(req, self.receipt(deploymentId="native.deploy.2"), thread_id=T)
        self.assertEqual(self.add(), done)
        self.assertEqual(self.add(B)["state"], "pending")

    def test_uncertain_blocks_all_later_intent_and_never_auto_retries(self):
        req = self.add()["requestId"]
        self.queue.claim(req, thread_id=T)
        result = {"state": "uncertain", "code": "NATIVE_RESULT_UNKNOWN"}
        row = self.queue.finish(req, result, thread_id=T)
        self.assertEqual(row["state"], "uncertain")
        self.assertEqual(self.add(), row)
        with self.assertRaisesRegex(q.QueueRejected, "QUEUE_BUSY"):
            self.add(B)
        with self.assertRaisesRegex(q.QueueRejected, "NOT_CLAIMABLE"):
            self.queue.claim(req, thread_id=T)

    def test_handoff_identity_cannot_be_swapped_under_existing_ticket(self):
        req = self.add()["requestId"]
        self.set_thread(X)
        with self.assertRaisesRegex(q.QueueRejected, "WRONG_THREAD"):
            self.queue.claim(req, thread_id=T)
        with self.assertRaisesRegex(q.QueueRejected, "THREAD_CHANGED"):
            self.queue.claim(req, thread_id=X)
        with self.assertRaisesRegex(q.QueueRejected, "THREAD_CHANGED"):
            self.queue.for_thread(thread_id=X)
        self.assertEqual(self.queue.status()["state"], "pending")

    def test_history_and_private_file_bound(self):
        for i in range(q.LIMIT + 2):
            sha = format(i + 120, "040x")
            req = self.add(sha)["requestId"]
            self.queue.claim(req, thread_id=T)
            self.queue.finish(req, {"state": "failed", "code": "NATIVE_PUBLISH_FAILED"}, thread_id=T)
        self.assertEqual(len(q.read(self.queue.file)["items"]), q.LIMIT)
        self.assertLess(self.queue.file.stat().st_size, q.MAX_BYTES)
        self.assertEqual(self.queue.file.stat().st_mode & 0o777, 0o600)
        self.queue.file.chmod(0o644)
        with self.assertRaises(OSError):
            self.queue.status()

    def test_corrupt_queue_refuses_instead_of_resetting(self):
        q.atomic(self.queue.file, {"schemaVersion": q.QUEUE_SCHEMA, "items": [None]})
        before = self.queue.file.read_bytes()
        with self.assertRaisesRegex(q.QueueRejected, "QUEUE_INVALID"):
            self.add()
        self.assertEqual(self.queue.file.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
