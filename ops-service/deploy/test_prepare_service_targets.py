import io
import json
from pathlib import Path
import plistlib
import shutil
import tempfile
import unittest
import zipfile

import prepare_service_targets as p


class ServiceTargets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = Path(__file__).resolve().parents[2]
        cls.head = p.git(cls.repo, "rev-parse", "HEAD").decode().strip()
        targets = {}
        for t, (roots, entries) in p.SPECS[p.SERVICES].items():
            trees, counts = {}, dict.fromkeys(p.SOURCE.values(), 0)
            for root in roots:
                trees[root] = p.git(cls.repo, "rev-parse", cls.head + ":" + root).decode().strip()
                for path in p.git(cls.repo, "ls-tree", "-r", "--name-only", cls.head, "--", root).decode().splitlines():
                    lang = p.SOURCE.get(Path(path).suffix)
                    if lang:
                        counts[lang] += 1
            targets[t] = {"roots": trees, "checked": counts, "entrypoints": list(entries),
                          "sourceFingerprint": p.digest(p.json_bytes(trees).rstrip(b"\n"))}
        cls.proof = {"schemaVersion": "ops.target-source-readiness.v1", "repository": p.SERVICES,
                     "ref": p.REF, "headSha": cls.head, "runAttempt": "1", "runId": "123", "targets": targets}
        cls.workflow = {"id": 4, "path": ".github/workflows/prepare-service-targets.yml"}
        repo = {"full_name": p.SERVICES, "id": 32}
        cls.run_meta = {"id": 123, "run_attempt": 1, "workflow_id": 4, "path": cls.workflow["path"],
                   "head_sha": cls.head, "head_branch": "main", "event": "push", "status": "completed",
                   "conclusion": "success", "repository": repo, "head_repository": repo}

    def verified(self, proof=None, extra=None, run=None, mismatch=False):
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as z:
            z.writestr("release.json", json.dumps(proof or self.proof))
            if extra:
                z.writestr(extra, "do not distribute private content")
        raw = out.getvalue()
        artifact = {"id": 787, "name": f"service-targets-{self.head}-1", "expired": False, "size_in_bytes": len(raw),
                    "digest": "sha256:" + ("0" * 64 if mismatch else p.digest(raw)),
                    "workflow_run": {"id": 123, "head_sha": self.head, "head_branch": "main",
                                     "repository_id": 32, "head_repository_id": 32}}
        return p.verify_target_artifact(self.head, self.workflow, run or self.run_meta, artifact, raw, repo=p.SERVICES)

    def test_immutable_tree_from_proof_and_independent_target_closures(self):
        result = self.verified()
        paths = p.tracked_targets(result, self.repo)
        self.assertEqual(set(paths), {"request-service", "workflow-planner", "backend-stack"})
        self.assertTrue(all(x.startswith("request-service/") for x in paths["request-service"]))
        self.assertTrue(all(x.startswith("workflow-planner/") for x in paths["workflow-planner"]))
        for r in p.STACK:
            self.assertIn(r + "/run.py", paths["backend-stack"])
        self.assertFalse(any(x.startswith(("ops-service/", "filex-writer/")) for x in paths["backend-stack"]))

    def test_ref_fork_digest_and_zip_scope_blocked(self):
        for extra in ("private.env", "../anything", "another.json"):
            with self.assertRaisesRegex(ValueError, "ARTIFACT_SCOPE_INVALID"):
                self.verified(extra=extra)
        with self.assertRaisesRegex(ValueError, "ARTIFACT_MISMATCH"):
            self.verified(mismatch=True)
        with self.assertRaisesRegex(ValueError, "RUN_SOURCE_DENIED"):
            self.verified(run={**self.run_meta, "head_repository":{"id":33,"full_name":p.SERVICES}})
        with self.assertRaisesRegex(ValueError, "RUN_NOT_VERIFIED"):
            self.verified(run={**self.run_meta, "event":"pull_request"})

    def test_asserted_fingerprint_or_counts_cannot_replace_git(self):
        altered = json.loads(json.dumps(self.proof))
        target = altered["targets"]["request-service"]
        target["roots"]["request-service"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "PROOF_SCOPE_INVALID"):
            self.verified(proof=altered)
        target["sourceFingerprint"] = p.digest(p.json_bytes(target["roots"]).rstrip(b"\n"))
        with self.assertRaisesRegex(ValueError, "GIT_TREE_DRIFT"):
            p.tracked_targets(self.verified(proof=altered), self.repo)

    def test_private_assembly_and_equivalence_without_ready_write(self):
        result = self.verified()
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp).resolve()
            releases = d / "releases"; releases.mkdir(mode=0o700)
            staged = p.assemble_source(result, self.repo, releases)
            self.assertEqual(staged.name, "ngs-services-github-" + self.head)
            self.assertRegex(p.verify_equivalent(result, self.repo, "request-service", staged), "[0-9a-f]{64}")
            with self.assertRaisesRegex(ValueError, "ALREADY_STAGED"):
                p.assemble_source(result, self.repo, releases)
            (staged / "request-service/extra.mjs").write_text("export const notAttested=1")
            with self.assertRaisesRegex(ValueError, "UNTRACKED_RUNTIME_SOURCE"):
                p.verify_equivalent(result, self.repo, "request-service", staged)
            self.assertFalse(any(d.rglob("ready.json")))


    def test_request_selector_and_schema_immutable(self):
        result = self.verified()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            releases = home / "Workspaces/BackendServices/code/releases"
            releases.mkdir(mode=0o700, parents=True)
            stage = p.assemble_source(result, self.repo, releases)
            old = releases / "ngs-services-github-abcdef1"
            old.mkdir(mode=0o700)
            shutil.copytree(stage / "request-service", old / "request-service")
            selectors = home / "Library/LaunchAgents"
            selectors.mkdir(parents=True)
            path = selectors / "com.vinus.ngs.request-service.plist"
            raw = plistlib.dumps({"Label":"com.vinus.ngs.request-service",
                                  "ProgramArguments":["/bin/node", str(old / "request-service/request-service.mjs")],
                                  "WorkingDirectory":str(old / "request-service"),
                                  "EnvironmentVariables":{"REQUEST_OFFICIAL_INBOX_DIR":"/canonical/fixture"}})
            path.write_bytes(raw)
            path.chmod(0o600)
            sha = p.digest(raw)
            candidate = p.request_candidate(result, self.repo, stage, home, sha)
            self.assertEqual(candidate["expectedPriorPlistSha256"], sha)
            self.assertEqual(candidate["treeSha256"], p.tree_sha(stage))
            self.assertEqual(path.read_bytes(), raw)
            with self.assertRaisesRegex(ValueError, "SELECTOR_CHANGED"):
                p.request_candidate(result, self.repo, stage, home, "1" * 64)
            (old / "request-service/official-inbox-schema.mjs").write_text("migration drift")
            with self.assertRaisesRegex(ValueError, "MIGRATION_NOT_VERIFIED"):
                p.request_candidate(result, self.repo, stage, home, sha)

if __name__ == "__main__":
    unittest.main()
