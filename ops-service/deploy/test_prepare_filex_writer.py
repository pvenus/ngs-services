import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock
import zipfile

import prepare_filex_writer as m

PIN = "python:3.12-slim@sha256:" + "a" * 64
IMAGE = "sha256:" + "b" * 64


class GitHub:
    def __init__(self, repo, head):
        self.head = head
        self.proof = m.source_proof(repo, head, "2", "3")
        self.run = {"id": 2, "run_attempt": 3, "workflow_id": 1, "path": m.WORKFLOW,
                    "head_sha": head, "head_branch": "main", "event": "push", "status": "completed",
                    "conclusion": "success", "repository": {"id": 5, "full_name": m.REPO},
                    "head_repository": {"id": 5, "full_name": m.REPO}}
        self.pack("filex-writer.json", self.proof)
        self.main_calls = 0
        self.advance = None

    def pack(self, member, doc):
        target = io.BytesIO()
        with zipfile.ZipFile(target, "w") as z:
            z.writestr(member, json.dumps(doc))
        self.raw = target.getvalue()
        self.artifact = {"id": 4, "name": "filex-concept-writer-" + self.head + "-3",
                         "expired": False, "size_in_bytes": len(self.raw),
                         "digest": "sha256:" + m.digest(self.raw),
                         "workflow_run": {"id": 2, "head_sha": self.head, "head_branch": "main",
                                          "repository_id": 5, "head_repository_id": 5}}

    def metadata(self, route):
        assert route.startswith("repos/" + m.REPO + "/")
        if route.endswith("git/ref/heads/main"):
            self.main_calls += 1
            head = self.advance if self.advance and self.main_calls > 2 else self.head
            return {"ref": m.REF, "object": {"type": "commit", "sha": head}}
        if route.endswith(m.WORKFLOW.rsplit("/", 1)[-1]):
            return {"id": 1, "path": m.WORKFLOW}
        if "/runs?" in route:
            return {"workflow_runs": [self.run]}
        if "/artifacts?" in route:
            return {"total_count": 1, "artifacts": [{"id": 4}]}
        assert route.endswith("/artifacts/4")
        return self.artifact

    def read(self, route, limit):
        assert route == "repos/" + m.REPO + "/actions/artifacts/4/zip"
        assert limit == len(self.raw)
        return self.raw


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.repo = root / "repo"
        self.repo.mkdir()
        w = self.repo / "filex-writer"
        w.mkdir()
        (w / "Dockerfile").write_bytes(m.dockerfile("python:3.12-slim"))
        for file in m.FILES:
            (w / file).write_text("value = 'committed fixture'\n")
        (w / "ignored-material.txt").write_text("must never enter context")
        self.control = root / "control"
        self.control.mkdir(mode=0o700)
        m.git(self.repo, "init", "-q")
        m.git(self.repo, "add", ".")
        m.git(self.repo, "-c", "user.name=fixture", "-c", "user.email=fixture@example.test",
              "-c", "commit.gpgsign=false", "commit", "-qm", "committed")
        self.head = m.git(self.repo, "rev-parse", "HEAD").decode().strip()
        self.gh = GitHub(self.repo, self.head)
        self.backend = Mock(spec=m.IsolatedDocker)
        self.backend.build_and_smoke.side_effect = self.build

    def build(self, context, base):
        self.assertEqual(base, PIN)
        self.assertEqual({p.name for p in context.iterdir()}, m.CLOSURE)
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o644 for p in context.iterdir()))
        self.assertEqual((context / "server.py").read_bytes(), b"value = 'committed fixture'\n")
        self.assertEqual((context / "Dockerfile").read_bytes(), m.dockerfile(PIN))
        return IMAGE

    def prepare(self, base=PIN):
        return m.prepare(self.gh, self.repo, self.control, base, self.backend)

    def test_preparation_only_exports_exact_committed_files_once(self):
        (self.repo / "filex-writer/server.py").write_text("uncommitted")
        out = self.prepare()
        self.assertEqual(out["state"], "image-verified")
        self.assertEqual(out["source"], self.gh.proof)
        self.assertEqual(out["imageId"], IMAGE)
        self.assertEqual(self.prepare(), out)
        self.backend.build_and_smoke.assert_called_once()
        self.assertEqual({x.name for x in self.control.iterdir()}, {"filex-writer-prepared"})
        self.assertEqual(list((self.control / "filex-writer-prepared").iterdir()),
                         [self.control / "filex-writer-prepared" / (self.head + ".json")])

    def test_wrong_ci_will_not_build(self):
        self.gh.run["head_repository"]["id"] = 13
        with self.assertRaisesRegex(m.Refused, "WORKFLOW_NOT_READY"):
            self.prepare()
        self.backend.build_and_smoke.assert_not_called()

    def test_artifact_byte_and_member_validation(self):
        self.gh.artifact["digest"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(m.Refused, "ARTIFACT_DIGEST_MISMATCH"):
            self.prepare()
        self.gh.pack("../filex-writer.json", self.gh.proof)
        with self.assertRaisesRegex(m.Refused, "ARTIFACT_SCOPE_INVALID"):
            self.prepare()
        self.backend.build_and_smoke.assert_not_called()

    def test_all_source_digests_are_cross_checked(self):
        self.gh.proof["files"]["server.py"] = "b" * 64
        self.gh.pack("filex-writer.json", self.gh.proof)
        with self.assertRaisesRegex(m.Refused, "GIT_PROOF_MISMATCH"):
            self.prepare()
        self.backend.build_and_smoke.assert_not_called()

    def test_no_evidence_on_smoke_failure_or_if_main_advances(self):
        self.gh.advance = "f" * 40
        with self.assertRaisesRegex(m.Refused, "MAIN_ADVANCED"):
            self.prepare()
        self.assertEqual(list((self.control / "filex-writer-prepared").iterdir()), [])
        self.gh.advance = None
        self.backend.build_and_smoke.side_effect = subprocess.TimeoutExpired(["sensitive"], 1)
        with self.assertRaisesRegex(m.Refused, "^FILEX_PREPARATION_UNAVAILABLE$"):
            self.prepare()
        self.assertEqual(list((self.control / "filex-writer-prepared").iterdir()), [])

    def test_unpinned_base_never_builds(self):
        with self.assertRaisesRegex(m.Refused, "BASE_NOT_PINNED"):
            self.prepare("python:3.12-slim")
        self.assertEqual(self.gh.main_calls, 0)
        self.backend.build_and_smoke.assert_not_called()

    def test_fixed_invocations_are_isolated_and_untagged(self):
        backend = m.IsolatedDocker()
        calls = []
        tmp = self.control / "test-context"
        tmp.mkdir()
        def run(*args, **kw):
            calls.append(args)
            if args[:3] == ("image", "inspect", PIN):
                return json.dumps([{"Os": "linux", "Architecture": "arm64",
                                    "RepoDigests": ["python@" + PIN.split("@")[1]]}]).encode()
            if args[0] == "build":
                (tmp.parent / "image-id").write_text(IMAGE)
            if args[:3] == ("image", "inspect", IMAGE):
                return json.dumps([{"Id": IMAGE, "Os": "linux", "Architecture": "arm64"}]).encode()
            if args[0] == "run":
                self.assertEqual(kw["data"], m.SMOKE)
            return b""
        backend.call = run
        self.assertEqual(backend.build_and_smoke(tmp, PIN), IMAGE)
        build, launch = calls[1], calls[3]
        self.assertIn("--network=none", build)
        self.assertNotIn("--tag", build)
        self.assertIn("--network=none", launch)
        self.assertIn("--read-only", launch)
        self.assertIn("--user=501:20", launch)
        self.assertIn("--cap-drop=ALL", launch)
        self.assertIn(IMAGE, launch)
        self.assertNotIn("-v", launch)
        self.assertNotIn("--mount", launch)
        self.assertNotIn("--publish", launch)


if __name__ == "__main__":
    unittest.main()
