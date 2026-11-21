"""Private NGS candidate verification. Preparation never deploys or relaunches."""

import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import tempfile
import zipfile

REPO = "pvenus/ngs-platform"
WORKFLOW = ".github/workflows/prepare-ngs-release.yml"
REF = "refs/heads/main"
NODE = "24.21.0"
TAR = "ngs-local.tar"
SHA = re.compile(r"[a-f0-9]{40}")
DIGEST = re.compile(r"[a-f0-9]{64}")
NUMBER = re.compile(r"[1-9][0-9]{0,19}")
LIMIT = 32 * 1024 * 1024
OUTER_LIMIT = LIMIT + 1024 * 1024
ARCHIVE_PATH = re.compile(
    r"README\.md|package(?:-lock)?\.json|source-manifest\.json|"
    r"dev/[A-Za-z0-9._-]+\.(?:sql|json)|docs/[A-Za-z0-9._-]+\.md|"
    r"local-miniflare/(?:README\.md|package(?:-lock)?\.json)|"
    r"sites/npcgen/build/[A-Za-z0-9._-]+\.(?:mjs|mts|js|html|css)|sites/npcgen/tests/[A-Za-z0-9._-]+\.mjs"
)


class Refused(ValueError):
    pass


def need(value, code):
    if not value:
        raise Refused(code)


def shape(value, keys):
    return isinstance(value, dict) and set(value) == set(keys.split())


def matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def digest(value):
    return hashlib.sha256(value).hexdigest()


def number(value):
    need(type(value) is int and value > 0 and NUMBER.fullmatch(str(value)), "GH_ID_INVALID")
    return str(value)


def unique(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, "DUPLICATE_JSON")
        result[key] = value
    return result


def json_load(data):
    return json.loads(data, object_pairs_hook=unique)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode()


def verify_metadata(head, workflow, run, artifact):
    need(matches(SHA, head), "HEAD_SHA_INVALID")
    need(isinstance(workflow, dict) and workflow.get("path") == WORKFLOW, "WRONG_WORKFLOW")
    workflow_id = number(workflow.get("id"))
    need(isinstance(run, dict) and run.get("workflow_id") == int(workflow_id) and run.get("path") == WORKFLOW
         and run.get("head_sha") == head and run.get("head_branch") == "main" and run.get("event") == "push"
         and run.get("status") == "completed" and run.get("conclusion") == "success", "RUN_NOT_VERIFIED")
    run_id, attempt = number(run.get("id")), number(run.get("run_attempt"))
    repo, source_repo = run.get("repository"), run.get("head_repository")
    need(isinstance(repo, dict) and isinstance(source_repo, dict)
         and repo.get("full_name") == source_repo.get("full_name") == REPO
         and number(repo.get("id")) == number(source_repo.get("id")), "FOREIGN_SOURCE_REPOSITORY")
    need(isinstance(artifact, dict) and artifact.get("expired") is False
         and artifact.get("name") == "ngs-local-source-" + head + "-" + attempt
         and type(artifact.get("size_in_bytes")) is int and 0 < artifact["size_in_bytes"] <= OUTER_LIMIT
         and isinstance(artifact.get("digest"), str) and artifact["digest"].startswith("sha256:")
         and matches(DIGEST, artifact["digest"][7:]), "ARTIFACT_NOT_VERIFIED")
    proof = artifact.get("workflow_run")
    need(isinstance(proof, dict) and proof.get("id") == int(run_id) and proof.get("head_sha") == head
         and proof.get("head_branch") == "main"
         and proof.get("repository_id") == proof.get("head_repository_id") == repo.get("id"), "ARTIFACT_RUN_MISMATCH")
    return {"runId": run_id, "runAttempt": attempt, "artifactId": number(artifact.get("id"))}


def source_members(raw):
    need(len(raw) <= LIMIT and len(raw) >= 1024, "TAR_SIZE_INVALID")
    files, dirs = {}, set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive:
            name = member.name
            need(name not in files and name not in dirs and len(files) + len(dirs) < 1024, "TAR_DUPLICATE_OR_LIMIT")
            if member.isdir():
                need(not name.startswith(("/", ".")) and all(part not in ("", ".", "..") for part in name.split("/")),
                     "TAR_DIRECTORY_INVALID")
                dirs.add(name)
                continue
            need(member.isfile() and ARCHIVE_PATH.fullmatch(name)
                 and not re.search(r"(?:^|/|\.)private(?:\.|/|$)", name, re.I)
                 and 0 <= member.size <= 8 * 1024 * 1024, "TAR_FILE_INVALID")
            value = archive.extractfile(member).read()
            need(len(value) == member.size, "TAR_FILE_TRUNCATED")
            files[name] = value
    allowed_dirs = {parent.as_posix() for name in files for parent in PurePosixPath(name).parents if parent.as_posix() != "."}
    need(dirs <= allowed_dirs and "source-manifest.json" in files, "TAR_SCOPE_INVALID")
    index = json_load(files["source-manifest.json"])
    need(isinstance(index, dict) and isinstance(index.get("files"), dict)
         and index["files"] == {k: digest(v) for k, v in files.items() if k != "source-manifest.json"}, "TAR_SOURCE_MANIFEST_MISMATCH")
    return files


def verify_release(head, workflow, run, artifact, payload):
    evidence = verify_metadata(head, workflow, run, artifact)
    need(isinstance(payload, bytes) and len(payload) == artifact["size_in_bytes"]
         and digest(payload) == artifact["digest"][7:], "ZIP_DIGEST_MISMATCH")
    with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
        parts = bundle.infolist()
        need(len(parts) == 2 and {p.filename for p in parts} == {TAR, "release.json"}
             and all(not p.is_dir() and not stat.S_ISLNK(p.external_attr >> 16) and not p.flag_bits & 1 for p in parts),
             "ZIP_UNEXPECTED_FILES")
        need(bundle.getinfo("release.json").file_size <= 4096 and bundle.getinfo(TAR).file_size <= LIMIT, "ZIP_TOO_LARGE")
        meta = json_load(bundle.read("release.json"))
        raw = bundle.read(TAR)
    need(shape(meta, "schemaVersion repository workflow headSha runId runAttempt nodeVersion archive spaceOverlay")
         and meta["schemaVersion"] == "ngs.ops-release.v1" and meta["repository"] == REPO
         and meta["workflow"] == WORKFLOW and meta["headSha"] == head and meta["runId"] == evidence["runId"]
         and meta["runAttempt"] == evidence["runAttempt"] and meta["nodeVersion"] == NODE
         and shape(meta["archive"], "file sha256 bytes") and meta["archive"] == {"file": TAR, "sha256": digest(raw), "bytes": len(raw)}
         and shape(meta["spaceOverlay"], "path treeSha validation")
         and meta["spaceOverlay"]["path"] == "space-hosting/overlay" and matches(SHA, meta["spaceOverlay"]["treeSha"])
         and meta["spaceOverlay"]["validation"] == "source-only", "INNER_MANIFEST_MISMATCH")
    return {"head": head, "raw": raw, "files": source_members(raw), "evidence": {**evidence, "archiveSha256": digest(raw)}}


def private_directory(path):
    s = path.lstat()
    need(path == path.resolve() and stat.S_ISDIR(s.st_mode) and s.st_uid == os.getuid()
         and stat.S_IMODE(s.st_mode) == 0o700, "PRIVATE_DIRECTORY_REQUIRED")


def private_file(path, mode=None):
    s = path.lstat()
    need(path == path.resolve() and stat.S_ISREG(s.st_mode) and s.st_uid == os.getuid() and s.st_nlink == 1
         and (stat.S_IMODE(s.st_mode) == mode if mode is not None else not s.st_mode & 0o022), "PRIVATE_FILE_REQUIRED")


def tree_sha(root):
    # Same byte/mode/in-tree dependency link contract as the Mac executor.
    rows = []
    for p in sorted(root.rglob("*")):
        s = p.lstat()
        need(s.st_uid == os.getuid(), "RELEASE_TREE_OWNER_DRIFT")
        if stat.S_ISLNK(s.st_mode):
            deps = root / "local-miniflare/node_modules"
            need(p.is_relative_to(deps) and p.resolve(strict=True).is_relative_to(deps), "RELEASE_LINK_DRIFT")
            value = ["link", os.readlink(p)]
        elif stat.S_ISREG(s.st_mode):
            need(not s.st_mode & 0o022, "RELEASE_TREE_MODE_DRIFT")
            h = hashlib.sha256()
            with p.open("rb") as file:
                for block in iter(lambda: file.read(1024 * 1024), b""):
                    h.update(block)
            value = ["file", stat.S_IMODE(s.st_mode), h.hexdigest()]
        else:
            need(stat.S_ISDIR(s.st_mode) and not s.st_mode & 0o022, "RELEASE_TREE_MODE_DRIFT")
            value = ["directory", stat.S_IMODE(s.st_mode)]
        rows.append([p.relative_to(root).as_posix(), value])
    return digest(json.dumps(rows, ensure_ascii=True, separators=(",", ":")).encode())


def atomic_write(path, payload):
    fd, temp = tempfile.mkstemp(prefix=".prepare-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
        dirfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def publish_ngs_local_ready(control, record):
    """Project a verified Local release into the provisioned OPS cycle.

    Invoke only from the successful preparation pipeline. No path or SHA comes
    from HTTP. A missing target or runner leaves the prepared release offline.
    """
    private_directory(control)
    root=control/'targets'
    target=root/'ngs-local'
    try:
        private_directory(root)
        private_directory(target)
        runner=target/'run-once'
        private_file(runner)
    except FileNotFoundError:
        return None
    attrs=runner.lstat()
    need(stat.S_IMODE(attrs.st_mode) in (0o500,0o700) and 0<attrs.st_size<=1024*1024,
         'NGS_LOCAL_RUNNER_UNTRUSTED')
    bundle=record['provenance']['archiveSha256']
    public={"schemaVersion":"ops.deploy-target-ready.v1","target":"ngs-local","sha":record['sha'],
            "artifactSha256":bundle,"runnerSha256":digest(runner.read_bytes()),"verifiedAt":record['verifiedAt']}
    require=control/'prepared.json'
    private_file(require,0o600)
    need(json_load(require.read_bytes())==record,'NGS_LOCAL_PREPARED_MOVED')
    atomic_write(target/'ready.json', json_bytes(public))
    return public


def write_prepared(verified, control, installed, execution, prior_plist, *, expected_prior_sha256, stamp=None):
    head, proof = verified["head"], verified["evidence"]
    for directory in (control, installed, execution, control / "mac-releases"):
        private_directory(directory)
    need(installed.name == "project-page-github-" + head and execution.name == head, "RELEASE_ROOT_MISMATCH")
    archived = verified["files"]
    parents = {str(parent) for name in archived for parent in PurePosixPath(name).parents}
    for path in installed.rglob("*"):
        relative = path.relative_to(installed).as_posix()
        need(relative in archived or relative in parents
             or relative == "local-miniflare/node_modules" or relative.startswith("local-miniflare/node_modules/"),
             "UNEXPECTED_INSTALLED_SOURCE")
    for relative, content in archived.items():
        p = installed / relative
        private_file(p)
        need(p.read_bytes() == content, "INSTALLED_SOURCE_MISMATCH")
    # A source-only directory cannot become ready accidentally.
    pkg = installed / "local-miniflare/node_modules/miniflare/package.json"
    lock = installed / "local-miniflare/package-lock.json"
    private_file(pkg)
    private_file(lock)
    require_lock = json_load(lock.read_bytes())["packages"]["node_modules/miniflare"]["version"]
    need(json_load(pkg.read_bytes()).get("version") == require_lock, "INSTALLED_WORKERD_MISMATCH")
    need(matches(DIGEST, expected_prior_sha256), "PRIOR_PLIST_DIGEST_INVALID")

    def require_original_plist():
        private_file(prior_plist)
        need(digest(prior_plist.read_bytes()) == expected_prior_sha256, "PRIOR_PLIST_CHANGED")

    require_original_plist()
    now = stamp or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    need(matches(re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ"), now), "VERIFIED_STAMP_INVALID")
    dt.datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ")
    manifest = {"schemaVersion": "ops.ngs-mac-release.v1", "sha": head, "repo": REPO, "ref": REF,
                "workflow": {"headSha": head, "headBranch": "main", "conclusion": "success",
                             **{key: proof[key] for key in ("runId", "runAttempt", "artifactId")}},
                "archiveSha256": proof["archiveSha256"], "treeSha256": tree_sha(installed),
                "expectedPriorPlistSha256": expected_prior_sha256}
    require_original_plist()
    package = control / "mac-releases" / head
    package.mkdir(mode=0o700, exist_ok=True)
    private_directory(package)
    saved = package / TAR
    if saved.exists():
        private_file(saved, 0o600)
        need(digest(saved.read_bytes()) == proof["archiveSha256"], "IMMUTABLE_SOURCE_TAR_MISMATCH")
    else:
        atomic_write(saved, verified["raw"])
    atomic_write(package / "manifest.json", json_bytes(manifest))
    record = {"schemaVersion": "ops.ngs-prepared-deploy.v1", "sha": head, "verifiedAt": now,
              "mac": {"ready": True}, "provenance": proof}
    require_original_plist()
    atomic_write(control / "prepared.json", json_bytes(record))
    publish_ngs_local_ready(control, record)
    return record


class GitHubEvidence:
    """Read-only existing gh transport. No login, token provisioning, or output logging."""

    def __init__(self, executable):
        self.executable = Path(executable)
        need(self.executable.is_absolute() and self.executable.is_file(), "GH_READ_TRANSPORT_UNAVAILABLE")

    def read(self, route, limit=1024 * 1024):
        need(route.startswith("repos/" + REPO + "/") and not any(c in route for c in "\r\n"), "GH_ROUTE_INVALID")
        env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat"}
        with tempfile.TemporaryFile() as out:
            done = subprocess.run([str(self.executable), "api", "--hostname", "github.com", "--method", "GET", route],
                                  env=env, stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  stdout=out, timeout=60, check=False)
            out.seek(0)
            raw = out.read(limit + 1)
        need(done.returncode == 0 and len(raw) <= limit, "GH_READ_FAILED")
        return raw

    def metadata(self, route):
        return json_load(self.read(route))


def verified_main_candidate(github):
    from urllib.parse import urlencode
    base = "repos/" + REPO + "/"
    main = github.metadata(base + "git/ref/heads/main")
    head = main.get("object", {}).get("sha")
    need(main.get("ref") == REF and main.get("object", {}).get("type") == "commit" and matches(SHA, head), "MAIN_REF_INVALID")
    workflow = github.metadata(base + "actions/workflows/" + WORKFLOW.rsplit("/", 1)[-1])
    need(workflow.get("path") == WORKFLOW, "WRONG_WORKFLOW")
    endpoint = base + "actions/workflows/" + number(workflow.get("id")) + "/runs?"
    runs = github.metadata(endpoint + urlencode({"branch": "main", "event": "push", "head_sha": head, "per_page": 1}))
    queue = runs.get("workflow_runs")
    need(isinstance(queue, list) and len(queue) == 1, "CI_CANDIDATE_NOT_AVAILABLE")
    run = queue[0]
    need(run.get("head_sha") == head and run.get("event") == "push" and run.get("head_branch") == "main"
         and run.get("status") == "completed" and run.get("conclusion") == "success", "CI_CANDIDATE_NOT_READY")
    run_id, attempt = number(run.get("id")), number(run.get("run_attempt"))
    name = "ngs-local-source-" + head + "-" + attempt
    bags = github.metadata(base + "actions/runs/" + run_id + "/artifacts?" + urlencode({"name": name, "per_page": 2}))
    items = bags.get("artifacts")
    need(isinstance(items, list) and len(items) == bags.get("total_count") == 1, "CI_ARTIFACT_AMBIGUOUS")
    artifact_id = number(items[0].get("id"))
    # Artifact-by-ID endpoint is the authenticated checksum and run association.
    artifact = github.metadata(base + "actions/artifacts/" + artifact_id)
    need(number(artifact.get("id")) == artifact_id, "ARTIFACT_ID_MISMATCH")
    verify_metadata(head, workflow, run, artifact)
    outer = github.read(base + "actions/artifacts/" + artifact_id + "/zip", limit=artifact["size_in_bytes"])
    result = verify_release(head, workflow, run, artifact, outer)
    # A newer main push while preparation was pending is handled by a later poll.
    latest = github.metadata(base + "git/ref/heads/main")
    need(latest.get("ref") == REF and latest.get("object", {}).get("sha") == head, "MAIN_ADVANCED")
    return result
