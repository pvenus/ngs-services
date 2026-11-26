"""Separate candidate-only approval evidence; never touches Filex Compose or runtime."""
import ast
from datetime import UTC, datetime
import fcntl
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from urllib.parse import urlencode
import zipfile

from prepare_ngs_release import (
    REF, SHA, Refused, atomic_write, digest, json_bytes, json_load, matches, need,
    number, private_directory, private_file,
)

from filex_writer_apply import CAPS, LEGACY_CAP_DIGEST, SEPARATION_CAPS, SEPARATION_CAP_DIGEST, PROP_CAPS, PROP_CAP_DIGEST, CAPABILITY_DIGESTS, PROP_ENV, capability_markers

REPO = "pvenus/ngs-services"
WORKFLOW = ".github/workflows/prepare-filex-writer.yml"
TARGET = "filex-concept-writer"
PREFIX = "filex-writer/"
LIMIT = 64 * 1024
MAX_SOURCE = 8 * 1024 * 1024
HEX = re.compile(r"[a-f0-9]{64}")
IMAGE_ID = re.compile(r"sha256:[a-f0-9]{64}")
BASE = re.compile(r"python:3\.12-slim@sha256:[a-f0-9]{64}")
FILES = (
    "server.py", "single_target_delete.py", "portrait_assets.py", "media_store.py",
    "media_validation.py", "npc_store.py", "npc_assets.py", "concept_store.py",
    "concept_lifecycle.py", "concept_markdown.py", "motion_store.py", "fenced_store.py",
    "owned_store.py", "ownership_plan.py", "npc_document.py", "npc_planning_markdown.py",
    "migration_store.py", "frame_maintenance.py", "native_pixel_direction.py",
    "workflow_native_media.py", "workflow_audio_store.py",
)
CLOSURE = {"Dockerfile", *FILES}
PROP_FILES = ("prop_package_contract.py", "prop_package_store.py")
PROP_CLOSURE = CLOSURE | set(PROP_FILES)
UNIFIED_CLOSURE = PROP_CLOSURE | {'scope_registry.py', 'scopes.json'}
# This discriminator binds smoke and target kind, not the registry itself.
# The exact registry blob is always separately covered by the source proof.
UNIFIED_CAP_DIGEST = digest(b'ngs.filex-writer-scopes.v1')
SCHEMA = "ops.filex-writer-source.v1"


def git(repo, *args):
    return subprocess.check_output(["/usr/bin/git", "-C", str(repo), *args],
                                   timeout=30, stderr=subprocess.DEVNULL)


def closure(repo, head):
    """Exact committed blobs only; never use the host worktree as build context."""
    need(matches(SHA, head), "FILEX_COMMIT_INVALID")
    root = git(repo, "rev-parse", f"{head}:filex-writer").decode().strip()
    need(matches(SHA, root) and git(repo, "cat-file", "-t", root).strip() == b"tree",
         "FILEX_TREE_INVALID")
    names = {}
    for row in git(repo, "ls-tree", "-r", "-z", head, "--", "filex-writer").split(b"\0"):
        if not row:
            continue
        flags, path = row.split(b"\t", 1)
        path = path.decode()
        need(path.startswith(PREFIX), "FILEX_SOURCE_SCOPE_INVALID")
        name = path.removeprefix(PREFIX)
        if name not in UNIFIED_CLOSURE:
            continue
        mode, kind, oid = flags.decode().split()
        need(mode == "100644" and kind == "blob" and matches(SHA, oid)
             and name not in names, "FILEX_SOURCE_SCOPE_INVALID")
        names[name] = oid
    need(set(names) in (CLOSURE, PROP_CLOSURE, UNIFIED_CLOSURE), "FILEX_SOURCE_MISSING")
    blobs = {}
    for name, oid in names.items():
        size = int(git(repo, "cat-file", "-s", oid))
        need(0 < size <= MAX_SOURCE, "FILEX_SOURCE_SIZE_INVALID")
        blobs[name] = git(repo, "cat-file", "blob", oid)
        need(len(blobs[name]) == size, "FILEX_SOURCE_SIZE_INVALID")
        if name.endswith(".py"):
            compile(blobs[name], name, "exec", dont_inherit=True)
    # A changed Dockerfile, privilege assumption, or runtime file requires source review.
    if set(blobs) == UNIFIED_CLOSURE:
        from filex_unified_runtime import registry
        registry(blobs['scopes.json'])
        need(blobs['Dockerfile']==dockerfile('python:3.12-slim',unified=True),'FILEX_DOCKERFILE_DRIFT')
        return root, blobs
    scoped = set(blobs) == PROP_CLOSURE
    need(scoped == (declared_capabilities(blobs["server.py"]) == PROP_CAP_DIGEST), "FILEX_SOURCE_CAPABILITIES_INVALID")
    need(blobs["Dockerfile"] == dockerfile("python:3.12-slim", prop=scoped), "FILEX_DOCKERFILE_DRIFT")
    return root, blobs


def dockerfile(base, *, prop=False, unified=False):
    if unified:
        return (f"FROM {base}\nWORKDIR /app\n"
                f"COPY server.py scope_registry.py scopes.json {' '.join(FILES[1:] + PROP_FILES)} /app/\n"
                "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\n"
                "USER 1000:1000\n"
                'CMD ["python", "-B", "/app/server.py"]\n').encode()
    return (
        f"FROM {base}\n"
        "WORKDIR /app\n"
        f"COPY {' '.join(FILES + (PROP_FILES if prop else ()))} /app/\n"
        "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WRITER_BIND=0.0.0.0 "
        "WRITER_ROOT=/canonical WRITER_TOKEN_FILE=/run/secrets/writer_token\n"
        "USER 1000:1000\n"
        'CMD ["python", "-B", "/app/server.py"]\n'
    ).encode()


def source_proof(repo, head, run_id, attempt):
    tree, files = closure(repo, head)
    return {"schemaVersion": SCHEMA, "repository": REPO, "ref": REF, "headSha": head,
            "runId": run_id, "runAttempt": attempt,
            "rootTree": tree, "files": {k: digest(v) for k, v in files.items()}}


def current_main(github):
    ref = github.metadata("repos/" + REPO + "/git/ref/heads/main")
    obj = ref.get("object", {})
    need(ref.get("ref") == REF and obj.get("type") == "commit" and matches(SHA, obj.get("sha")),
         "FILEX_MAIN_INVALID")
    return obj["sha"]


def acquire(github, repo):
    """GitHub transport is owner-provisioned, read-only and separate from runtime."""
    prefix = "repos/" + REPO + "/"
    head = current_main(github)
    workflow = github.metadata(prefix + "actions/workflows/" + WORKFLOW.rsplit("/", 1)[-1])
    wid = number(workflow.get("id"))
    need(workflow.get("path") == WORKFLOW, "FILEX_WORKFLOW_MISMATCH")
    runs_route = prefix + f"actions/workflows/{wid}/runs?" + urlencode(
        {"branch": "main", "event": "push", "head_sha": head, "per_page": 1})
    runs = github.metadata(runs_route).get("workflow_runs")
    need(type(runs) is list and len(runs) == 1, "FILEX_WORKFLOW_NOT_READY")
    rid, attempt = number(runs[0].get("id")), number(runs[0].get("run_attempt"))
    run = github.metadata(prefix + f"actions/runs/{rid}")
    need(number(run.get("id")) == rid and number(run.get("run_attempt")) == attempt,
         "FILEX_WORKFLOW_NOT_READY")
    repo_id = int(number(run["repository"]["id"]))
    need(run.get("workflow_id") == int(wid) and run.get("path") == WORKFLOW
         and run.get("head_sha") == head and run.get("head_branch") == "main"
         and run.get("event") == "push" and run.get("status") == "completed"
         and run.get("conclusion") == "success"
         and run["head_repository"]["id"] == repo_id
         and run["repository"]["full_name"] == run["head_repository"]["full_name"] == REPO,
         "FILEX_WORKFLOW_NOT_READY")
    name = f"filex-concept-writer-{head}-{attempt}"
    bag = github.metadata(prefix + f"actions/runs/{rid}/artifacts?" + urlencode({"name": name, "per_page": 2}))
    rows = bag.get("artifacts")
    need(type(rows) is list and len(rows) == bag.get("total_count") == 1, "FILEX_ARTIFACT_AMBIGUOUS")
    aid = number(rows[0].get("id"))
    artifact = github.metadata(prefix + f"actions/artifacts/{aid}")
    size = artifact.get("size_in_bytes")
    association = artifact.get("workflow_run", {})
    need(number(artifact.get("id")) == aid and artifact.get("name") == name
         and artifact.get("expired") is False and type(size) is int and 0 < size <= LIMIT
         and association.get("id") == int(rid) and association.get("head_sha") == head
         and association.get("head_branch") == "main"
         and association.get("repository_id") == association.get("head_repository_id") == repo_id,
         "FILEX_ARTIFACT_SCOPE_INVALID")
    raw = github.read(prefix + f"actions/artifacts/{aid}/zip", limit=size)
    need(type(raw) is bytes and len(raw) == size and artifact.get("digest") == "sha256:" + digest(raw),
         "FILEX_ARTIFACT_DIGEST_MISMATCH")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        need(len(members) == 1 and members[0].filename == "filex-writer.json"
             and 0 < members[0].file_size <= LIMIT and not stat.S_ISLNK(members[0].external_attr >> 16)
             and not members[0].flag_bits & 1, "FILEX_ARTIFACT_SCOPE_INVALID")
        proof = json_load(archive.read(members[0]))
    need(proof == source_proof(repo, head, rid, attempt), "FILEX_GIT_PROOF_MISMATCH")
    latest = github.metadata(runs_route).get("workflow_runs")
    need(type(latest) is list and len(latest) == 1 and number(latest[0].get("id")) == rid
         and number(latest[0].get("run_attempt")) == attempt and latest[0].get("status") == "completed"
         and latest[0].get("conclusion") == "success", "FILEX_WORKFLOW_NOT_READY")
    need(current_main(github) == head, "FILEX_MAIN_ADVANCED")
    return proof, {"artifactId": aid, "artifactSha256": digest(raw)}


def declared_capabilities(server):
    """Read the exact new capability declaration in the verified immutable source."""
    declarations = {}
    for node in ast.walk(ast.parse(server)):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys,node.values):
            if isinstance(key,ast.Constant) and key.value in ("workflowAudioSeparateV1","workflowPropPackageV1"):
                need(key.value not in declarations and isinstance(value,ast.Constant) and value.value is True,
                     "FILEX_SOURCE_CAPABILITIES_INVALID")
                declarations[key.value] = True
    if "workflowPropPackageV1" in declarations:
        need("workflowAudioSeparateV1" in declarations, "FILEX_SOURCE_CAPABILITIES_INVALID")
        return PROP_CAP_DIGEST
    return SEPARATION_CAP_DIGEST if declarations else LEGACY_CAP_DIGEST



# No external dependency/source mounts; scratch volumes exist only in this invocation.
SMOKE = b"""
import json, os, secrets, subprocess, time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

token = secrets.token_hex(24)
Path('/tmp/fixture-token').write_text(token)
child = subprocess.Popen(['/usr/local/bin/python', '-B', '/app/server.py'], env={
    **os.environ, 'WRITER_TOKEN_FILE': '/tmp/fixture-token', 'WRITER_ROOT': '/canonical',
    'WRITER_SCOPE': 'concept', 'WRITER_WORKFLOW_BGM': '1', 'WRITER_WORKFLOW_AUDIO_DELETE': '1',
    'WRITER_PORT': '8093', 'WRITER_BIND': '127.0.0.1', **PROFILE_ENV,
}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
def get(header):
    try:
        with urlopen(Request('http://127.0.0.1:8093/v1/capabilities',headers=header), timeout=.5) as reply:
            return reply.status, json.load(reply)
    except HTTPError as exc:
        return exc.code, None
try:
    for _ in range(30):
        if child.poll() is not None:
            raise SystemExit(2)
        try:
            status, caps = get({'Authorization': 'Bearer ' + token})
            assert status == 200 and json.dumps(caps,sort_keys=True) == json.dumps(EXPECTED_CAPS,sort_keys=True)
            assert get({})[0] == 401
            break
        except URLError:
            time.sleep(.15)
    else:
        raise SystemExit(3)
finally:
    if child.poll() is None:
        child.kill()
    child.wait(timeout=2)
"""


def capability_smoke(profile):
    need(profile in CAPABILITY_DIGESTS, "FILEX_SOURCE_CAPABILITIES_INVALID")
    expected = {LEGACY_CAP_DIGEST:CAPS, SEPARATION_CAP_DIGEST:SEPARATION_CAPS, PROP_CAP_DIGEST:PROP_CAPS}[profile]
    return ("EXPECTED_CAPS = " + repr(expected) + "\nPROFILE_ENV = " +
            repr(PROP_ENV if profile == PROP_CAP_DIGEST else {}) + "\n").encode() + SMOKE


def unified_smoke(rows):
    # No deployment secret or Filex host bind is visible to this isolated probe.
    header = 'SCOPES = ' + repr(rows) + '\n'
    return (header + '''
import hashlib,json,os,subprocess,sys,time
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen
CREDENTIALS = {r['credential'] for r in SCOPES}
tokens = {k:hashlib.sha256(('fixture-' + k).encode()).hexdigest() for k in CREDENTIALS}
for k,v in tokens.items():
    p=Path('/run/secrets')/k/'writer_token'
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:
        f.write(v)
child=subprocess.Popen([sys.executable,'-B','/app/server.py'])
def probe(path,token=None):
    req=Request('http://127.0.0.1:8093'+path,headers={'Authorization':'Bearer '+token} if token else {})
    try:
        with urlopen(req,timeout=1) as response:
            return response.status,json.load(response)
    except HTTPError as error:
        return error.code,None
try:
    last=time.monotonic()+8
    while time.monotonic()<last:
        if child.poll() is not None:
            raise RuntimeError('writer did not start')
        try:
            assert probe('/v1/capabilities')[0]==404
            break
        except URLError:
            time.sleep(.1)
    else:
        raise RuntimeError('writer did not bind')
    for r in SCOPES:
        path='/v1/scopes/'+r['id']+'/capabilities'
        assert probe(path)[0]==401
        status,body=probe(path,tokens[r['credential']])
        assert status==200 and body['namespace']=='canonical-files/binari/'+r['folder']+'/'
        wrong=next((v for k,v in tokens.items() if k!=r['credential']),None)
        assert wrong is None or probe(path,wrong)[0]==401
finally:
    child.terminate()
    child.wait(5)
''').encode()


class IsolatedDocker:
    """Fixed Mac backend. Never references a Compose project, tag, or host mount."""
    executable = "/opt/homebrew/bin/docker"
    platform = "linux/arm64"
    architecture = "arm64"

    def __init__(self, home=None):
        self.home = Path.home() if home is None else Path(home)
        self.socket = self.home / "Workspaces/Filex/runtime/colima/filex/docker.sock"

    def call(self, *args, data=None, timeout=120):
        selected = self.socket.lstat()
        need(self.socket.resolve(strict=True) == self.socket and stat.S_ISSOCK(selected.st_mode)
             and selected.st_uid == os.getuid(), "FILEX_ISOLATED_DOCKER_UNAVAILABLE")
        # No user-selected context/daemon, registry credentials, or host mounts.
        with tempfile.TemporaryDirectory(prefix=".filex-docker-client-") as settings:
            env = {"HOME": str(self.home), "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
                   "DOCKER_CONFIG": settings}
            result = subprocess.run([self.executable, "--host", "unix://" + str(self.socket), *args],
                                    input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    env=env, timeout=timeout, check=False)
        need(result.returncode == 0 and len(result.stdout) <= LIMIT, "FILEX_ISOLATED_DOCKER_FAILED")
        return result.stdout

    def verify_image(self, image):
        need(matches(IMAGE_ID,image), "FILEX_IMAGE_ID_INVALID")
        built = json_load(self.call("image", "inspect", image, timeout=5))
        need(type(built) is list and len(built) == 1 and built[0].get("Id") == image
             and built[0].get("Architecture") == self.architecture and built[0].get("Os") == "linux",
             "FILEX_IMAGE_ID_INVALID")
        return True

    def build_and_smoke(self, context, base, *, capability=LEGACY_CAP_DIGEST):
        need(matches(BASE, base), "FILEX_BASE_NOT_PINNED")
        inspected = json_load(self.call("image", "inspect", base))
        pinned = "python@" + base.split("@")[1]
        need(type(inspected) is list and len(inspected) == 1
             and inspected[0].get("Os") == "linux" and inspected[0].get("Architecture") == self.architecture
             and any(x in inspected[0].get("RepoDigests", []) for x in (pinned, "docker.io/library/" + pinned)),
             "FILEX_OFFLINE_BASE_UNAVAILABLE")
        iid = context.parent / "image-id"
        cid = context.parent / "fixture-id"
        self.call("build", "--network=none", "--pull=false", "--platform", self.platform,
                  "--iidfile", str(iid), str(context), timeout=240)
        image = iid.read_text().strip()
        self.verify_image(image)
        if capability == UNIFIED_CAP_DIGEST:
            from filex_unified_runtime import registry
            rows = registry((context/'scopes.json').read_bytes())
            tmpfs = ['/canonical/'+row['folder'] for row in rows]
            tmpfs += ['/run/secrets/'+name for name in sorted({row['credential'] for row in rows})]
            smoke = unified_smoke(rows)
        else:
            tmpfs = ["/"+path for path in (("canonical", "audio", "tmp", "props") if capability == PROP_CAP_DIGEST else ("canonical", "audio", "tmp"))]
            smoke = capability_smoke(capability)
        try:
            self.call("run", "--rm", "--pull=never", "--cidfile", str(cid), "--network=none",
                      "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                      "--user=501:20", "--memory=256m", "--pids-limit=32",
                      *sum((("--tmpfs", f"{path}:rw,noexec,nosuid,nodev,uid=501,gid=20,mode=0700,size=16m")
                            for path in tmpfs), ()) ,
                      "-i", "--entrypoint=/usr/local/bin/python", image, "-B", "-",
                      data=smoke, timeout=25)
        except (OSError, ValueError, subprocess.SubprocessError):
            if cid.is_file():
                fixture_id = cid.read_text().strip()
                if matches(HEX, fixture_id):
                    try:
                        self.call("rm", "-f", fixture_id, timeout=10)
                    except (OSError, ValueError, subprocess.SubprocessError):
                        pass
            raise
        return image


def prepare(github, repo, control, base, docker=None):
    """Stage proof only. No OPS deploy-ready state or activation is written."""
    try:
        return stage(github, repo, control, base, docker)
    except Refused:
        raise
    except BlockingIOError:
        raise Refused("FILEX_PREPARATION_LOCK_BUSY") from None
    except (OSError, ValueError, KeyError, TypeError, SyntaxError, subprocess.SubprocessError, zipfile.BadZipFile):
        raise Refused("FILEX_PREPARATION_UNAVAILABLE") from None


def stage(github, repo, control, base, docker):
    need(matches(BASE, base), "FILEX_BASE_NOT_PINNED")
    private_directory(control)
    root = control / "filex-writer-prepared"
    backend = docker or IsolatedDocker()
    root.mkdir(mode=0o700, exist_ok=True)
    private_directory(root)
    # This does not control a live selector; never hold the global OPS lock
    # during a source fetch, image build, or isolated smoke.
    lock = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        proof, evidence = acquire(github, repo)
        head = proof["headSha"]
        tree, sources = closure(repo, head)
        need(tree == proof["rootTree"] and {k: digest(v) for k, v in sources.items()} == proof["files"],
             "FILEX_GIT_PROOF_MISMATCH")
        profile = UNIFIED_CAP_DIGEST if "scopes.json" in sources else declared_capabilities(sources["server.py"])
        mark = {"capabilitiesSha256": profile} if profile != LEGACY_CAP_DIGEST else {}
        record_file = root / (head + ".json")
        if os.path.lexists(record_file):
            private_file(record_file, 0o600)
            saved = json_load(record_file.read_bytes())
            need(saved.get("source") == proof and saved.get("provenance") == evidence and saved.get("base") == base
                 and matches(IMAGE_ID, saved.get("imageId"))
                 and (profile == UNIFIED_CAP_DIGEST or capability_markers(saved, {"capabilitiesSha256"}))
                 and saved.get("capabilitiesSha256") == mark.get("capabilitiesSha256"), "FILEX_EXISTING_CANDIDATE_MISMATCH")
            return saved
        with tempfile.TemporaryDirectory(prefix=".filex-source-", dir=root) as temp:
            context = Path(temp) / "context"
            context.mkdir(mode=0o700)
            sources["Dockerfile"] = dockerfile(base, prop=profile==PROP_CAP_DIGEST, unified=profile==UNIFIED_CAP_DIGEST)
            for name, data in sources.items():
                p = context / name
                p.write_bytes(data)
                p.chmod(0o644)
            image = backend.build_and_smoke(context, base, capability=profile)
        need(matches(IMAGE_ID, image), "FILEX_IMAGE_ID_INVALID")
        need(current_main(github) == head, "FILEX_MAIN_ADVANCED")
        record = {"schemaVersion": "ops.filex-writer-candidate.v1", "target": TARGET,
                  "state": "image-verified", "source": proof, "provenance": evidence,
                  "base": base, "imageId": image, "smoke": "isolated-authenticated-capabilities",
                  "preparedAt": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"), **mark}
        atomic_write(record_file, json_bytes(record))
        return record
    finally:
        os.close(lock)


def ci(env, repo, out):
    head = env.get("GITHUB_SHA", "")
    rid = env.get("GITHUB_RUN_ID", "")
    attempt = env.get("GITHUB_RUN_ATTEMPT", "")
    need(env.get("GITHUB_REPOSITORY") == REPO and env.get("GITHUB_REF") == REF
         and env.get("GITHUB_WORKFLOW_REF") == REPO + "/" + WORKFLOW + "@" + REF
         and env.get("GITHUB_EVENT_NAME") == "push" and git(repo, "rev-parse", "HEAD").decode().strip() == head
         and number(int(rid)) == rid and number(int(attempt)) == attempt, "FILEX_WORKFLOW_MISMATCH")
    proof = source_proof(repo, head, rid, attempt)
    out.mkdir(mode=0o700)
    (out / "filex-writer.json").write_bytes(json_bytes(proof))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: prepare_filex_writer.py PROOF-DIRECTORY (CI only)")
    try:
        ci(os.environ, Path(__file__).resolve().parents[2], Path(sys.argv[1]))
    except (OSError, ValueError, SyntaxError, subprocess.SubprocessError):
        raise SystemExit("Filex writer proof failed.") from None
