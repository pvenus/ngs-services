"""Verify fixed workflow target evidence and stage source without running it."""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import io
import os
import plistlib
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import zipfile

from mac_target_apply import TARGETS, validate_and_rewrite
from prepare_ngs_release import (DIGEST, REF, SHA, digest, json_bytes,
                                 json_load, matches, need, number, private_directory,
                                 private_file, shape, tree_sha)

SERVICES = "pvenus/ngs-services"
WORKERS = "pvenus/ngs-workers"
WORKFLOWS = {SERVICES: "service", WORKERS: "worker"}
STACK = ("data-service", "catalog-service", "execution-service", "workflow-decision-service",
         "file-service", "video-alpha-service", "workflow-service")
SPECS = {
    SERVICES: {
        "request-service": (("request-service",), ("request-service/request-service.mjs", "request-service/request-mcp.mjs")),
        "workflow-planner": (("workflow-planner",), ("workflow-planner/cli.mjs",)),
        "backend-stack": (("runtime", "contracts", *STACK), ("runtime/stack.py", *(r + "/run.py" for r in STACK))),
    },
    WORKERS: {},
}
for name, main, native in (
    ("document", "document", None), ("file", "file", None), ("image", "image", "native-image"),
    ("animation", "animation", "native-wan"), ("postprocess", "postprocess", "native-postprocess"),
    ("audio", "candidate-audio-resident", "candidate-native-audio-resident"),
):
    key = "worker-" + name
    SPECS[WORKERS][key] = (tuple("workflow-production/" + x for x in (main, native) if x),
                           ("workflow-production/" + TARGETS[key].entry,))
LIMIT = 64 * 1024
SOURCE = {".py": "python", ".mjs": "js", ".cjs": "js", ".js": "js", ".json": "json"}
NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
SUFFIX = {".py", ".mjs", ".cjs", ".js", ".json", ".md", ".toml", ".sql", ".txt", ".yaml", ".yml", ".html", ".css"}
WORKER_SUM = re.compile(r"workflow-production/(?:animation|document|file|image|postprocess)/"
                        r"vendor/planner-(?:[0-9a-f]{7,40}|pixel-sprite-v1)/SHA256SUMS")


def permitted_source(path, family):
    return (path.name == ".gitignore" or path.suffix in SUFFIX
            or family == WORKERS and WORKER_SUM.fullmatch(path.as_posix()) is not None)


def verify_target_artifact(head, workflow, run, artifact, raw, *, repo):
    """Evidence from authenticated GitHub/public canonical API, exact ZIP bytes."""
    need(repo in WORKFLOWS and matches(SHA, head), "TARGET_REPO_OR_HEAD_INVALID")
    stem = WORKFLOWS[repo] + "-targets"
    path = ".github/workflows/prepare-" + stem + ".yml"
    need(workflow.get("path") == path and run.get("path") == path
         and run.get("workflow_id") == int(number(workflow["id"]))
         and run.get("head_sha") == head and run.get("head_branch") == "main"
         and run.get("event") == "push" and run.get("status") == "completed"
         and run.get("conclusion") == "success", "TARGET_RUN_NOT_VERIFIED")
    rid, attempt = number(run["id"]), number(run["run_attempt"])
    repo_id = int(number(run["repository"]["id"]))
    need(run["repository"]["full_name"] == run["head_repository"]["full_name"] == repo
         and run["head_repository"]["id"] == repo_id, "TARGET_RUN_SOURCE_DENIED")
    aid = number(artifact["id"])
    association = artifact["workflow_run"]
    need(artifact["name"] == f"{stem}-{head}-{attempt}" and artifact["expired"] is False
         and type(artifact["size_in_bytes"]) is int and isinstance(raw, bytes)
         and len(raw) == artifact["size_in_bytes"] <= LIMIT
         and artifact["digest"] == "sha256:" + digest(raw)
         and association["id"] == int(rid) and association["head_sha"] == head
         and association["head_branch"] == "main" and association["repository_id"] == association["head_repository_id"] == repo_id,
         "TARGET_ARTIFACT_MISMATCH")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        xs = z.infolist()
        need(len(xs) == 1 and xs[0].filename == "release.json" and not xs[0].is_dir()
             and not stat.S_ISLNK(xs[0].external_attr >> 16) and not xs[0].flag_bits & 1
             and 0 < xs[0].file_size <= LIMIT, "TARGET_ARTIFACT_SCOPE_INVALID")
        p = json_load(z.read(xs[0]))
    need(shape(p, "schemaVersion repository ref headSha runId runAttempt targets")
         and all(p[k] == val for k, val in {"schemaVersion": "ops.target-source-readiness.v1",
                 "repository": repo, "ref": REF, "headSha": head, "runId": rid, "runAttempt": attempt}.items())
         and shape(p["targets"], " ".join(SPECS[repo])), "TARGET_PROOF_SHAPE_INVALID")
    for key, (roots, entries) in SPECS[repo].items():
        v = p["targets"][key]
        need(shape(v, "roots entrypoints sourceFingerprint checked")
             and shape(v["roots"], " ".join(roots))
             and all(matches(SHA, h) for h in v["roots"].values())
             and v["entrypoints"] == list(entries)
             and v["sourceFingerprint"] == hashlib.sha256(json_bytes(v["roots"]).rstrip(b"\n")).hexdigest()
             and shape(v["checked"], "python js json")
             and all(type(n) is int and 0 <= n <= 10_000 for n in v["checked"].values()),
             "TARGET_PROOF_SCOPE_INVALID")
    return {"proof": p, "artifactSha256": digest(raw), "artifactId": aid}


def git(repo, *args):
    return subprocess.check_output(["/usr/bin/git", "-C", str(repo), *args], timeout=30, stderr=subprocess.DEVNULL)


def tracked_tree(repo, head, family, roots):
    files = {}
    counts = dict.fromkeys(SOURCE.values(), 0)
    for root, oid in roots.items():
        need(git(repo, "rev-parse", head + ":" + root).decode().strip() == oid, "TARGET_GIT_TREE_DRIFT")
        raw = git(repo, "ls-tree", "-r", "-z", head, "--", root)
        for record in raw.rstrip(b"\0").split(b"\0"):
            flags, name = record.split(b"\t", 1)
            mode, kind, blob = flags.split()
            name = name.decode()
            path = Path(name)
            need(mode in {b"100644", b"100755"} and kind == b"blob" and name.startswith(root + "/")
                 and all(NAME.fullmatch(part) or part == ".gitignore" for part in path.parts)
                 and permitted_source(path, family)
                 and not any(part.lower() in {"private", "credentials", "tokens", "secrets"} for part in path.parts)
                 and name not in files, "TARGET_TRACKED_SOURCE_SCOPE_INVALID")
            files[name] = (blob.decode(), int(mode[-3:], 8))
            if path.suffix in SOURCE:
                counts[SOURCE[path.suffix]] += 1
    return files, counts


def tracked_targets(verified, repo):
    """Cross-check every reported tree, check count and entrypoint against exact Git blobs."""
    p = verified["proof"]
    out = {}
    for target, row in p["targets"].items():
        files, counts = tracked_tree(repo,p["headSha"],p["repository"],row["roots"])
        need(counts == row["checked"] and all(x in files for x in row["entrypoints"]), "TARGET_GIT_CHECK_COVERAGE_DRIFT")
        out[target] = files
    return out


def assemble_source(verified, repo, releases, *, public_source_parent=False):
    """Install code only, with all fixed scopes for this commit, before any ready state."""
    if public_source_parent:
        # The existing Mac code/releases parent is owner-controlled 0755.
        # Only canonical, public Git blobs are staged; the new child is 0700.
        attrs = releases.lstat()
        need(releases == releases.resolve() and stat.S_ISDIR(attrs.st_mode)
             and attrs.st_uid == os.getuid() and stat.S_IMODE(attrs.st_mode) in (0o700, 0o755),
             "TARGET_RELEASE_PARENT_UNTRUSTED")
    else:
        private_directory(releases)
    p = verified["proof"]
    family = p["repository"]
    head = p["headSha"]
    prefix = "ngs-services-github-" if family == SERVICES else "workflows-github-"
    dst = releases / (prefix + head)
    files = {}
    for scope in tracked_targets(verified, repo).values():
        for name, spec in scope.items():
            rel = name if family == SERVICES else name.removeprefix("workflow-production/")
            need(rel not in files or files[rel] == spec, "CONFLICTING_TARGET_CLOSURES")
            files[rel] = spec
    lock = os.open(releases, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        need(not os.path.lexists(dst), "TARGET_SOURCE_ALREADY_STAGED")
        with tempfile.TemporaryDirectory(prefix=".ops-target-source-", dir=releases) as staging:
            tree = Path(staging) / "src"
            tree.mkdir(mode=0o700)
            for relative, (oid, mode) in files.items():
                data = git(repo, "cat-file", "blob", oid)
                need(len(data) <= 8 * 1024 * 1024, "TARGET_BLOB_TOO_LARGE")
                q = tree / relative
                for parent in reversed([n for n in q.parents if n != tree and n.is_relative_to(tree)]):
                    parent.mkdir(mode=0o700, exist_ok=True)
                    private_directory(parent)
                with q.open("xb") as writer:
                    writer.write(data)
                q.chmod(0o700 if mode & 0o100 else 0o600)
            need(not os.path.lexists(dst), "TARGET_SOURCE_ALREADY_STAGED")
            os.rename(tree, dst)
    finally:
        os.close(lock)
    return dst


def verify_equivalent(verified, repo, target, prior_root):
    """Return a content-only digest for exact source/no-op; no claimed deployed SHA."""
    p = verified["proof"]
    selected = tracked_targets(verified, repo)[target]
    family = p["repository"]
    expected, files = {}, {}
    for name, (oid, _mode) in selected.items():
        rel = name if family == SERVICES else name.removeprefix("workflow-production/")
        path = Path(rel)
        if path.suffix == ".md" or path.name == ".gitignore":
            continue  # no executable imports or runtime fixtures are ignored
        expected[rel] = oid
        f = prior_root / path
        private_file(f)
        content = f.read_bytes()
        need(hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest() == oid,
             "TARGET_RUNTIME_BYTES_CHANGED")
        files[rel] = digest(content)
    for name in p["targets"][target]["roots"]:
        root = prior_root / (name if family == SERVICES else name.removeprefix("workflow-production/"))
        for f in root.rglob("*"):
            if "__pycache__" in f.relative_to(prior_root).parts:
                need(not f.is_symlink() and (f.is_dir() or f.is_file() and f.suffix == ".pyc"),
                     "UNTRACKED_RUNTIME_SOURCE")
                continue
            if f.is_symlink() or f.is_file() and f.suffix != ".md" and f.name != ".gitignore":
                need(not f.is_symlink() and f.relative_to(prior_root).as_posix() in expected, "UNTRACKED_RUNTIME_SOURCE")
    return digest(json_bytes(files))


def equivalent_record(verified, target, observed_sha, runner, selector, expected_selector_sha, runtime_digest):
    need(matches(SHA, observed_sha) and matches(DIGEST, runtime_digest), "EQUIVALENCE_DIGEST_INVALID")
    private_file(runner, 0o500)
    private_file(selector)
    need(digest(selector.read_bytes()) == expected_selector_sha, "TARGET_SELECTOR_CHANGED")
    return {"schemaVersion": "ops.deploy-target-equivalent.v1", "target": target,
            "sha": verified["proof"]["headSha"], "observedSha": observed_sha,
            "artifactSha256": verified["artifactSha256"], "runnerSha256": digest(runner.read_bytes()),
            "selectorSha256": expected_selector_sha, "runtimeTreeSha256": runtime_digest,
            "verifiedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def request_storage_identity(selector):
    args, env = selector["ProgramArguments"], selector.get("EnvironmentVariables")
    need(args[2:] == [] and type(env) is dict and isinstance(env.get("REQUEST_OFFICIAL_INBOX_DIR"), str),
         "REQUEST_SELECTOR_STORAGE_DRIFT")
    directory = Path(env["REQUEST_OFFICIAL_INBOX_DIR"])
    ledger = directory / "official-requests.sqlite"
    need(directory.is_absolute() and str(directory) == env["REQUEST_OFFICIAL_INBOX_DIR"]
         and directory != directory.parent, "REQUEST_SELECTOR_STORAGE_DRIFT")
    # Stat the existing owner-private ledger only. Never run candidate
    # JavaScript or open the database during preparation.
    try:
        private_directory(directory)
        private_file(ledger, 0o600)
        folder, db = directory.lstat(), ledger.lstat()
    except (OSError, ValueError) as error:
        raise ValueError("REQUEST_SELECTOR_STORAGE_DRIFT") from error
    return directory, folder.st_dev, folder.st_ino, db.st_dev, db.st_ino


def request_candidate(verified, repo, release, home, expected_selector_sha):
    """Return fixed Request Service manifests only; provisioning/publication is separate."""
    p = verified["proof"]
    name = "request-service"
    need(p["repository"] == SERVICES and matches(DIGEST, expected_selector_sha), "REQUEST_CANDIDATE_INVALID")
    old_catalog = TARGETS[name]
    base = home / "Workspaces"
    prefix = base / old_catalog.workspace / "code/releases"
    need(release == prefix / (old_catalog.release_prefix + p["headSha"]), "REQUEST_RELEASE_MISMATCH")
    private_directory(release)
    files = tracked_targets(verified, repo)
    expected_source = {n: item for scope in files.values() for n, item in scope.items()}
    listed_source = set()
    for local in release.rglob("*"):
        need(not local.is_symlink() and (local.is_dir() or local.is_file()), "REQUEST_RELEASE_UNSAFE_SOURCE")
        if local.is_file():
            rel = local.relative_to(release).as_posix()
            need(rel in expected_source, "REQUEST_RELEASE_UNTRACKED_SOURCE")
            private_file(local)
            want, _ = expected_source[rel]
            data = local.read_bytes()
            need(hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == want,
                 "REQUEST_RELEASE_SOURCE_DRIFT")
            listed_source.add(rel)
    need(listed_source == expected_source.keys(), "REQUEST_RELEASE_SOURCE_MISSING")
    selector = home / "Library/LaunchAgents" / (old_catalog.label + ".plist")
    private_file(selector)
    raw = selector.read_bytes()
    need(digest(raw) == expected_selector_sha, "TARGET_SELECTOR_CHANGED")
    proposed = validate_and_rewrite(raw, release, old_catalog, base)
    need(request_storage_identity(plistlib.loads(raw)) == request_storage_identity(plistlib.loads(proposed)),
         "REQUEST_SELECTOR_STORAGE_DRIFT")
    return {"schemaVersion": "ops.mac-target-candidate.v1", "target": name, "sha": p["headSha"],
            "archiveSha256": verified["artifactSha256"], "treeSha256": tree_sha(release),
            "expectedPriorPlistSha256": expected_selector_sha}


def prepare_audio_candidate(verified, repo, release, home, expected_selector_sha):
    """Prepare only the exact worker source and separate owner-local audio binding."""
    from audio_cutover import stage_audio_binding
    p = verified['proof']
    name = 'worker-audio'
    catalog = TARGETS[name]
    base = home / 'Workspaces'
    head = p['headSha']
    need(p['repository'] == WORKERS and matches(DIGEST, expected_selector_sha)
         and release == base / 'Workers/code/releases' / (catalog.release_prefix + head),'TARGET_AUDIO_RELEASE')
    private_directory(release)
    selected = {n.removeprefix('workflow-production/'): spec for n,spec in tracked_targets(verified,repo)[name].items()}
    seen = set()
    for folder in ('candidate-audio-resident','candidate-native-audio-resident'):
        private_directory(release / folder)
        for path in (release / folder).rglob('*'):
            need(not path.is_symlink() and (path.is_dir() or path.is_file()),'TARGET_AUDIO_UNSAFE_SOURCE')
            if not path.is_file():
                continue
            rel = path.relative_to(release).as_posix()
            private_file(path)
            data = path.read_bytes()
            need(rel in selected and hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest() == selected[rel][0],
                 'TARGET_AUDIO_SOURCE_DRIFT')
            seen.add(rel)
    need(seen == selected.keys(),'TARGET_AUDIO_SOURCE_MISSING')
    selector = home / 'Library/LaunchAgents' / (catalog.label + '.plist')
    private_file(selector)
    raw = selector.read_bytes()
    need(digest(raw) == expected_selector_sha,'TARGET_SELECTOR_CHANGED')
    validate_and_rewrite(raw,release,catalog,base)
    proof = stage_audio_binding(plistlib.loads(raw)['ProgramArguments'],release,head)
    private_file(selector)
    need(selector.read_bytes() == raw,'TARGET_SELECTOR_CHANGED')
    return {'schemaVersion':'ops.mac-target-candidate.v1','target':name,'sha':head,
            'archiveSha256':verified['artifactSha256'],'treeSha256':tree_sha(release),
            'expectedPriorPlistSha256':expected_selector_sha,'audioBindingSha256':proof}
