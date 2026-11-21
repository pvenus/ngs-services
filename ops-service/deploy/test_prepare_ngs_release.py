import copy
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

import prepare_ngs_release as prep


class EvidenceFixture(unittest.TestCase):
    def setUp(self):
        self.head = "a" * 40
        self.workflow = {"id": 5, "path": prep.WORKFLOW}
        repository = {"id": 39, "full_name": prep.REPO}
        self.run = {"id": 14, "run_attempt": 2, "workflow_id": 5, "path": prep.WORKFLOW,
                    "head_sha": self.head, "head_branch": "main", "event": "push", "status": "completed",
                    "conclusion": "success", "repository": repository, "head_repository": dict(repository)}
        self.body = {"README.md": b"test fixture", "local-miniflare/package-lock.json":
                     b'{"packages":{"node_modules/miniflare":{"version":"test-locked"}}}'}
        self.body["source-manifest.json"] = prep.json_bytes({"files": {k: prep.digest(v) for k, v in self.body.items()}})
        self.artifact = {"id": 63, "name": "ngs-local-source-" + self.head + "-2", "expired": False,
                         "workflow_run": {"id": 14, "head_sha": self.head, "head_branch": "main",
                                          "repository_id": 39, "head_repository_id": 39}}
        self.zip = self.pack()

    def pack(self, *, tamper=False, extra=None, symlink=False):
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode="w") as tar:
            for name, body in self.body.items():
                member = tarfile.TarInfo(name)
                member.size = len(body)
                tar.addfile(member, io.BytesIO(body))
            if symlink:
                member = tarfile.TarInfo("sites/npcgen/build/symlink.mjs")
                member.type = tarfile.SYMTYPE
                member.linkname = "../private"
                tar.addfile(member)
        raw = out.getvalue()
        manifest = {"schemaVersion": "ngs.ops-release.v1", "repository": prep.REPO, "workflow": prep.WORKFLOW,
                    "headSha": self.head, "runId": "14", "runAttempt": "2", "nodeVersion": prep.NODE,
                    "archive": {"file": prep.TAR, "bytes": len(raw), "sha256": "9" * 64 if tamper else prep.digest(raw)},
                    "spaceOverlay": {"path": "space-hosting/overlay", "treeSha": "b" * 40, "validation": "source-only"}}
        outer = io.BytesIO()
        with zipfile.ZipFile(outer, mode="w") as zip_out:
            zip_out.writestr(prep.TAR, raw)
            zip_out.writestr("release.json", prep.json_bytes(manifest))
            if extra:
                zip_out.writestr(extra, b"fixture")
        result = outer.getvalue()
        self.artifact.update(digest="sha256:" + prep.digest(result), size_in_bytes=len(result))
        return result

    def verify(self, payload=None, *, run=None, artifact=None):
        return prep.verify_release(self.head, self.workflow, run or self.run, artifact or self.artifact,
                                   self.zip if payload is None else payload)

    def test_verified_plan_preserves_exact_archive_and_run_attempt(self):
        result = self.verify()
        self.assertEqual(result["files"], self.body)
        self.assertEqual(result["evidence"],
                         {"runId": "14", "runAttempt": "2", "artifactId": "63", "archiveSha256": prep.digest(result["raw"])})

    def test_refuses_foreign_push_wrong_attempt_expired_artifact_or_digest(self):
        for field, value in (("event", "pull_request"), ("path", "build.yml"), ("conclusion", "failure"), ("run_attempt", 1)):
            with self.subTest(field=field), self.assertRaises(prep.Refused):
                self.verify(run={**self.run, field: value})
        self.assertRaises(prep.Refused, self.verify, artifact={**self.artifact, "expired": True})
        self.assertRaisesRegex(prep.Refused, "ZIP_DIGEST_MISMATCH", self.verify, self.zip[:-1] + b"X")
        again = copy.deepcopy(self.run)
        again["head_repository"]["full_name"] = "someone/fork"
        self.assertRaisesRegex(prep.Refused, "FOREIGN_SOURCE_REPOSITORY", self.verify, run=again)

    def test_rejects_traversal_duplicate_artifact_and_unverified_source(self):
        self.assertRaisesRegex(prep.Refused, "INNER_MANIFEST_MISMATCH", self.verify, self.pack(tamper=True))
        self.assertRaisesRegex(prep.Refused, "ZIP_UNEXPECTED_FILES", self.verify, self.pack(extra="../outside"))
        self.assertRaisesRegex(prep.Refused, "TAR_FILE_INVALID", self.verify, self.pack(symlink=True))
        self.body["README.md"] = b"unverified"
        self.assertRaisesRegex(prep.Refused, "TAR_SOURCE_MANIFEST_MISMATCH", self.verify, self.pack())

    def test_private_atomic_readiness_after_installed_files_and_lock(self):
        verified = self.verify()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            control = root / "control"
            installed = root / ("project-page-github-" + self.head)
            execution = root / self.head
            for p in (control, control / "mac-releases", installed, execution):
                p.mkdir(mode=0o700)
            for name, content in self.body.items():
                path = installed / name
                path.parent.mkdir(mode=0o700, exist_ok=True)
                path.write_bytes(content)
            prior = root / "com.vinus.ngs.local-mcp.plist"
            prior.write_bytes(b"prior fixture selector")
            with self.assertRaises(FileNotFoundError):
                prep.write_prepared(verified, control, installed, execution, prior)
            self.assertFalse((control / "prepared.json").exists())
            dep = installed / "local-miniflare/node_modules/miniflare"
            dep.mkdir(mode=0o700, parents=True)
            (dep / "package.json").write_bytes(b'{"version":"test-locked"}')
            record = prep.write_prepared(verified, control, installed, execution, prior, stamp="2026-11-22T00:00:01Z")
            package = control / "mac-releases" / self.head
            self.assertEqual((package / prep.TAR).read_bytes(), verified["raw"])
            self.assertEqual(prep.json_load((package / "manifest.json").read_bytes())["treeSha256"], prep.tree_sha(installed))
            self.assertEqual(record, prep.json_load((control / "prepared.json").read_bytes()))
            self.assertEqual((control / "prepared.json").stat().st_mode & 0o777, 0o600)

    def test_latest_main_fetch_reads_one_exact_successfully_bound_artifact(self):
        fix = self
        base = "repos/" + prep.REPO + "/"
        main = {"object": {"sha": self.head, "type": "commit"}, "ref": prep.REF}
        class Fake:
            def metadata(self, path):
                mapping = {
                    "git/ref/heads/main": main,
                    "actions/workflows/prepare-ngs-release.yml": fix.workflow,
                    "actions/workflows/5/runs?branch=main&event=push&head_sha=" + fix.head + "&per_page=1":
                        {"workflow_runs": [fix.run]},
                    "actions/runs/14/artifacts?name=" + fix.artifact["name"] + "&per_page=2":
                        {"artifacts": [{"id": 63}], "total_count": 1},
                    "actions/artifacts/63": fix.artifact,
                }
                return mapping[path[len(base):]]
            def read(self, path, limit):
                fix.assertEqual(path, base + "actions/artifacts/63/zip")
                fix.assertEqual(limit, len(fix.zip))
                return fix.zip
        self.assertEqual(prep.verified_main_candidate(Fake())["head"], self.head)


if __name__ == "__main__":
    unittest.main()
