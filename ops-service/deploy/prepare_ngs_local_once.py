"""Launchd entry for NGS Local candidate preparation, never an apply adapter."""
import contextlib
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import sys
import tarfile
import types
import zipfile

from github_actions_keychain import KeychainGitHubEvidence, MAIN_ROUTE
from prepare_ngs_release import (DIGEST, REF, REPO, SHA, Refused, atomic_write, digest, json_bytes, json_load,
                                 matches, need, private_directory, private_file, tree_sha,
                                 verified_main_candidate, write_prepared)

HOME = Path.home()
CONTROL = HOME / "Workspaces/OPS/runtime/deploy-control"
NGS = HOME / "Workspaces/NGS"
RELEASES = NGS / "code/releases"
EXECUTION = NGS / "runtime/local-miniflare-execution"
PLIST = HOME / "Library/LaunchAgents/com.vinus.ngs.local-mcp.plist"
NODE = HOME / ".nvm/versions/node/v24.21.0/bin/node"
CACHE = CONTROL / "ngs-local-npm-cache"
SELECTOR = "com.vinus.ngs.local-mcp"
ENTRY = Path("sites/npcgen/build/local-miniflare-app.mjs")
ASSEMBLER_SHA = "959547928ccafe7a27f6d05e0649ba6e24d770feaba3ea8a63b961d777765547"
ASSEMBLY = "ops.ngs-local-assembly.v1"
READY = "ops.deploy-target-ready.v1"
PLAN = "ops.ngs-prepared-deploy.v1"
STATUS = "ngs-local-preparation-status.json"


def read_private(path, limit, *, mode=None):
    private_file(path, mode)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        a = os.fstat(stream.fileno())
        b = path.lstat()
        need(a.st_ino == b.st_ino and a.st_dev == b.st_dev and a.st_uid == os.getuid()
             and stat.S_ISREG(a.st_mode) and a.st_nlink == 1 and (mode is None or stat.S_IMODE(a.st_mode) == mode)
             and not a.st_mode & 0o022 and 0 < a.st_size <= limit, "PRIVATE_PREP_INPUT_INVALID")
        raw = stream.read(limit + 1)
        c = os.fstat(stream.fileno())
        need(len(raw) == a.st_size and c.st_mtime_ns == a.st_mtime_ns and c.st_ctime_ns == a.st_ctime_ns,
             "PRIVATE_PREP_INPUT_CHANGED")
        return raw


def read_optional(path):
    try:
        return json_load(read_private(path, 4096, mode=0o600))
    except FileNotFoundError:
        return None


@contextlib.contextmanager
def lock(path, *, create=False, directory=False):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if directory:
        flags |= os.O_DIRECTORY
    else:
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        if create:
            flags |= os.O_CREAT
    fd = os.open(path, flags, 0o600)
    try:
        a, b = os.fstat(fd), path.lstat()
        need(a.st_ino == b.st_ino and a.st_dev == b.st_dev and a.st_uid == os.getuid()
             and (stat.S_ISDIR(a.st_mode) and stat.S_IMODE(a.st_mode) == 0o700 if directory else
                  stat.S_ISREG(a.st_mode) and a.st_nlink == 1 and stat.S_IMODE(a.st_mode) == 0o600),
             "PREP_LOCK_INVALID")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused("PREP_LOCK_BUSY") from None
        yield
    finally:
        os.close(fd)


def load_assembler(control):
    """Run only the separately reviewed, byte-pinned assembler, not CI code."""
    parent = control / "bin"
    private_directory(parent)
    dest = parent / "assemble_mac_release.py"
    raw = read_private(dest, 65536, mode=0o600)
    need(digest(raw) == ASSEMBLER_SHA, "NGS_ASSEMBLER_PIN_MISMATCH")
    module = types.ModuleType("_pinned_assemble_mac_release")
    exec(compile(raw, str(dest), "exec"), module.__dict__)
    return module


def main_head(github):
    main = github.metadata(MAIN_ROUTE)
    head = main.get("object", {}).get("sha")
    need(main.get("ref") == REF and main.get("object", {}).get("type") == "commit" and matches(SHA, head),
         "MAIN_REF_INVALID")
    return head


def read_selector(prior_plist, releases, scratch, node):
    raw = read_private(prior_plist, 65536, mode=0o600)
    p = plistlib.loads(raw)
    args = p.get("ProgramArguments")
    need(p.get("Label") == SELECTOR and type(args) is list and len(args) == 2 and args[0] == str(node)
         and type(args[1]) is str and type(p.get("WorkingDirectory")) is str, "NGS_SELECTOR_INVALID")
    path = Path(args[1])
    need(len(path.parents) >= 4, "NGS_SELECTOR_INVALID")
    selected = path.parents[3]
    work = Path(p["WorkingDirectory"])
    need(selected.parent == releases and work.parent == scratch and args[1] == str(selected / ENTRY),
         "NGS_SELECTOR_INVALID")
    return raw, selected, work


def runner_digest(target):
    p = target / "run-once"
    private_file(p)
    need(stat.S_IMODE(p.lstat().st_mode) in (0o500, 0o700), "NGS_LOCAL_RUNNER_UNTRUSTED")
    return digest(read_private(p, 1024 * 1024))


def already_ready(control, target, expected, head, runner):
    ready, prepared = read_optional(target / "ready.json"), read_optional(control / "prepared.json")
    if not (isinstance(ready, dict) and isinstance(prepared, dict) and prepared.get("sha") == head
            and prepared.get("schemaVersion") == PLAN and prepared.get("mac") == {"ready": True}):
        return False
    proof = prepared.get("provenance", {})
    if not (isinstance(proof, dict) and set(proof) == {"archiveSha256", "artifactId", "runId", "runAttempt"}
            and matches(DIGEST, proof["archiveSha256"])):
        return False
    expected_ready = {"schemaVersion": READY, "target": "ngs-local", "sha": head,
                      "artifactSha256": proof["archiveSha256"], "runnerSha256": runner,
                      "verifiedAt": prepared.get("verifiedAt")}
    if expected_ready != ready:
        return False
    folder = control / "mac-releases" / head
    private_directory(folder)
    manifest = read_optional(folder / "manifest.json")
    if not isinstance(manifest, dict):
        return False
    workflow = {"headSha": head, "headBranch": "main", "conclusion": "success",
                **{k: proof[k] for k in ("runId", "runAttempt", "artifactId")}}
    return all(manifest.get(k) == v for k, v in {
        "schemaVersion": "ops.ngs-mac-release.v1", "sha": head, "repo": REPO, "ref": REF,
        "workflow": workflow, "archiveSha256": proof["archiveSha256"], "expectedPriorPlistSha256": expected,
    }.items()) and matches(DIGEST, manifest.get("treeSha256"))


def receipt(head, status):
    return {"target": "ngs-local", "sha": head, "state": status}


def prepare_once(github, assembler, *, control=CONTROL, releases=RELEASES, scratch=EXECUTION,
                 prior_plist=PLIST, node=NODE, npm_cache=CACHE):
    target = control / "targets/ngs-local"
    for p in (control, control / "targets", target, control / "mac-releases", releases, scratch):
        private_directory(p)
    with lock(control / "prepare-ngs-local.lock", create=True):
        runner = runner_digest(target)
        prior, selected, workdir = read_selector(prior_plist, releases, scratch, node)
        expected = digest(prior)
        head = main_head(github)
        source, execution = releases / ("project-page-github-" + head), scratch / head
        if selected == source and workdir == execution:
            return receipt(head, "current")
        folder = control / "mac-releases" / head
        if os.path.lexists(folder):
            private_directory(folder)
            need(not any(os.path.lexists(folder / n) for n in ("prior.plist", "result.json")), "PREP_RECONCILE_REQUIRED")
        if already_ready(control, target, expected, head, runner):
            return receipt(head, "ready")
        verified = verified_main_candidate(github)
        need(verified["head"] == head, "MAIN_ADVANCED")
        folder.mkdir(mode=0o700, exist_ok=True)
        private_directory(folder)
        need(not any(os.path.lexists(folder / n) for n in ("prior.plist", "result.json")), "PREP_RECONCILE_REQUIRED")
        checkpoint = folder / "assembly.json"
        evidence = {"schemaVersion": ASSEMBLY, "sha": head,
                    "archiveSha256": verified["evidence"]["archiveSha256"], "expectedPriorPlistSha256": expected}
        if os.path.lexists(source) or os.path.lexists(execution):
            # Resume only our complete exact staging pair; never replace arbitrary or partial trees.
            private_directory(source)
            private_directory(execution)
            need(read_optional(checkpoint) == {**evidence, "treeSha256": tree_sha(source)}, "PREP_RECONCILE_REQUIRED")
        else:
            need(not os.path.lexists(checkpoint), "PREP_RECONCILE_REQUIRED")
            pair = assembler.assemble_verified(verified, control=control, releases=releases, scratch=scratch,
                                               prior_plist=prior_plist, expected_prior_sha256=expected,
                                               node=node, npm_cache=npm_cache)
            need(pair == (source, execution), "PREP_ASSEMBLY_PATH_MISMATCH")
            private_directory(source)
            private_directory(execution)
            atomic_write(checkpoint, json_bytes({**evidence, "treeSha256": tree_sha(source)}))
        # Same lock order as the button path (cycle, then apply/assembly directory).
        # No locks for apply are held while staging dependencies or the CI ZIP.
        with lock(control / "cycle.lock", create=True), lock(control, directory=True):
            need(not os.path.lexists(control / "active.json"), "PREP_ACTIVE_DEPLOYMENT")
            need(main_head(github) == head, "MAIN_ADVANCED")
            need(read_selector(prior_plist, releases, scratch, node)[0] == prior, "PRIOR_PLIST_CHANGED")
            need(runner_digest(target) == runner, "NGS_LOCAL_RUNNER_CHANGED")
            need(not any(os.path.lexists(folder / n) for n in ("prior.plist", "result.json")), "PREP_RECONCILE_REQUIRED")
            write_prepared(verified, control, source, execution, prior_plist, expected_prior_sha256=expected)
            need(already_ready(control, target, expected, head, runner), "NGS_LOCAL_READY_NOT_CONFIRMED")
        return receipt(head, "registered")


EXPECTED_WAIT = {
     "PREP_LOCK_BUSY", "PREP_ACTIVE_DEPLOYMENT", "GITHUB_KEYCHAIN_UNAVAILABLE", "GITHUB_KEYCHAIN_ITEM_MISSING",
    "GITHUB_KEYCHAIN_LOCKED_OR_DENIED", "GITHUB_KEYCHAIN_ITEM_INVALID", "GITHUB_ACTIONS_READ_DENIED",
    "GITHUB_ARTIFACT_EXPIRED", "GITHUB_ARTIFACT_REDIRECT_DENIED", "GITHUB_TRANSPORT_UNAVAILABLE",
    "GITHUB_HTTP_RESPONSE_INVALID", "CI_CANDIDATE_NOT_AVAILABLE", "CI_CANDIDATE_NOT_READY", "CI_ARTIFACT_AMBIGUOUS",
    "MAIN_ADVANCED", "PRIOR_PLIST_CHANGED", "PRIOR_SELECTOR_CHANGED", "PREP_RECONCILE_REQUIRED",
    "PINNED_NPM_CACHE_OR_INSTALL_UNAVAILABLE", "NATIVE_SCHEMA_OR_RUNTIME_CHANGE_OUTSIDE_SCOPE",
}


def status_record(outcome, control=CONTROL, *, stamp=None):
    # The source runner emits only the fixed private schema; never write raw errors.
    code = outcome.get("code")
    if code is None:
        state = "ready"
    elif code == "GITHUB_KEYCHAIN_ITEM_MISSING":
        state = "auth_missing"
    elif code == "GITHUB_KEYCHAIN_LOCKED_OR_DENIED":
        state = "auth_locked_or_denied"
    elif code in ("GITHUB_KEYCHAIN_ITEM_INVALID", "GITHUB_ACTIONS_READ_DENIED"):
        state = "auth_rejected"
    elif code in ("CI_CANDIDATE_NOT_AVAILABLE", "CI_CANDIDATE_NOT_READY", "GITHUB_ARTIFACT_EXPIRED",
                  "MAIN_ADVANCED", "PREP_LOCK_BUSY", "PREP_ACTIVE_DEPLOYMENT"):
        state = "pending"
    else:
        state = "blocked"
    result = {"schemaVersion": "ops.ngs-local-preparation-status.v1", "target": "ngs-local", "state": state,
              "checkedAt": stamp or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if state == "ready":
        head = outcome.get("sha")
        need(matches(SHA, head) and outcome.get("state") in ("ready", "current", "registered"), "NGS_LOCAL_PREP_REFUSED")
        result.update(sha=head, outcome=outcome["state"])
    else:
        result["code"] = code if code in EXPECTED_WAIT else "NGS_LOCAL_PREP_REFUSED"
    private_directory(control)
    path = control / STATUS
    if os.path.lexists(path):
        read_private(path, 4096, mode=0o600)
    atomic_write(path, json_bytes(result))
    return result


def main():
    need(len(sys.argv) == 1, "NO_NGS_PREP_ARGUMENTS")
    return prepare_once(KeychainGitHubEvidence(), load_assembler(CONTROL))


if __name__ == "__main__":
    failed = False
    try:
        result = main()
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.SubprocessError,
            tarfile.TarError, zipfile.BadZipFile, RecursionError) as e:
        code = str(e)
        result = {"target": "ngs-local", "state": "not_registered",
                  "code": code if code in EXPECTED_WAIT else "NGS_LOCAL_PREP_REFUSED"}
        failed = True
    try:
        # A second runner observing the existing exclusive lock is not the owner
        # of preparation status and must not replace the active run's result.
        if result.get("code") != "PREP_LOCK_BUSY":
            status_record(result)
    except (OSError, ValueError):
        result = {"target": "ngs-local", "state": "not_registered", "code": "NGS_LOCAL_PREP_STATUS_UNAVAILABLE"}
        failed = True
    print(json.dumps(result, separators=(",", ":")), flush=True)
    if failed:
        sys.exit(1)
