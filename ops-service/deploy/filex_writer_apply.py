#!/opt/homebrew/bin/python3.12
"""Fixed owner-click executor for only the registered Filex concept writer."""
from datetime import UTC, datetime
from copy import deepcopy
import fcntl
import http.client
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import time

from prepare_ngs_release import DIGEST, SHA, atomic_write, digest, json_bytes, json_load, matches, need, private_directory, private_file, shape

TARGET = "filex-writer"
PROJECT = "ngs-filex-local"
SERVICE = "concept-writer"
CONTAINER = PROJECT + "-" + SERVICE + "-1"
IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
CONTAINER_ID = re.compile(r"[0-9a-f]{64}")
NAMES = ("env.sh", "start-filex.sh", "start-runtime.sh", "check-file-volume.py", "compose.yaml")
CANDIDATE = "ops.filex-writer-apply-candidate.v1"
APPLIED = "ops.filex-writer-applied.v1"
POLICY = "ops.filex-writer-preparation.v1"
BASE = re.compile(r"python:3\.12-slim@sha256:[a-f0-9]{64}")
CAP_FIELDS = {"capabilitiesSha256", "priorCapabilitiesSha256"}
CANDIDATE_KEYS = "schemaVersion target sha artifactSha256 imageId priorImageId composeSha256 startupSha256 runtimeFingerprint"
PROJECT_PATH = Path("/Volumes/무제/NGS/Filex/files/ngs/copies/canonical-files/binari")
WRITER_ENV = {
    "WRITER_SCOPE": "concept", "WRITER_WORKFLOW_BGM": "1", "WRITER_WORKFLOW_AUDIO_DELETE": "1",
    "WRITER_BIND": "0.0.0.0", "WRITER_ROOT": "/canonical", "WRITER_TOKEN_FILE": "/run/secrets/writer_token",
}
CAPS = {
    "workflowAudioResultV1": True, "workflowAudioDeleteV1": True,
    "conceptRecoverableLifecycleV1": True, "conceptCategoryMarkdownV1": True,
    "conceptCategoryOverwriteV1": True, "conceptSingleTargetDeleteV1": True,
    "releasedFrameCleanupLeaseV1": False, "npcOwnedSlots": False, "generationFence": False,
    "atomicCAS": False, "lockedBatch": True, "namespace": "canonical-files/binari/concept/",
}
SEPARATION_CAPS = {**CAPS, "workflowAudioSeparateV1": True}
LEGACY_CAP_DIGEST = digest(json_bytes(CAPS))
SEPARATION_CAP_DIGEST = digest(json_bytes(SEPARATION_CAPS))
PROP_CAPS = {**SEPARATION_CAPS, "workflowPropPackageV1": True}
PROP_CAP_DIGEST = digest(json_bytes(PROP_CAPS))
CAPABILITY_DIGESTS = (LEGACY_CAP_DIGEST, SEPARATION_CAP_DIGEST, PROP_CAP_DIGEST)
PROP_ENV = {"WRITER_WORKFLOW_PROP_PACKAGES": "1"}
PROP_SOURCE = str(PROJECT_PATH / "props")
PROP_VOLUME = {"type": "bind", "source": PROP_SOURCE, "target": "/props",
               "read_only": False, "bind": {"create_host_path": False}}



def capabilities_digest(value):
    """Recognize only the two full known maps, with exact JSON boolean types."""
    if type(value) is dict:
        payload = json_bytes(value)
        if payload == json_bytes(CAPS):return LEGACY_CAP_DIGEST
        if payload == json_bytes(SEPARATION_CAPS):return SEPARATION_CAP_DIGEST
        if payload == json_bytes(PROP_CAPS):return PROP_CAP_DIGEST
    return None


def capability_markers(record, fields=CAP_FIELDS):
    return type(record) is dict and all(key not in record or record[key] in (SEPARATION_CAP_DIGEST, PROP_CAP_DIGEST) for key in fields)


def observed_shape(record, mandatory, allowed=CAP_FIELDS):
    return (type(record) is dict and set(mandatory.split()) <= set(record) <= set(mandatory.split()) | allowed
            and capability_markers(record, allowed))


# Statically scoped live probe. The token never leaves the existing container.
PROBE = b"""
import json, os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, ProxyHandler, build_opener
url = 'http://127.0.0.1:8093/v1/capabilities'
client = build_opener(ProxyHandler({}))
try:
    client.open(url, timeout=2).close()
    raise SystemExit(2)
except HTTPError as error:
    if error.code != 401:
        raise
token = Path('/run/secrets/writer_token').read_text().strip()
req = Request(url, headers={'Authorization':'Bearer ' + token})
with client.open(req, timeout=2) as reply:
    if reply.status != 200:
        raise SystemExit(3)
    data = reply.read(4097)
    if len(data) > 4096 or not isinstance(json.loads(data), dict):
        raise SystemExit(4)
    print(data.decode().strip())
"""


def filex_code(home):
    return home / "Workspaces/Filex/code"


def target_directory(control):
    return control / "targets" / TARGET


def selector_file(control):
    return target_directory(control) / "active.compose.json"


def startup_bridge(home, control, *, props=True):
    """Owner-provisioned, re-pinned bridge. Never installed by deployment."""
    files = filex_code(home)
    quote = lambda p: shlex.quote(str(p))
    prop_verify = ("\n        scoped = (json.dumps({'services':{'concept-writer':{'image':image,"
                   "'environment':" + repr(PROP_ENV) + ",'volumes':[" + repr(PROP_VOLUME) + "]}}},"
                   "sort_keys=True,separators=(',',':')) + chr(10)).encode()\n"
                   "        if raw not in (exact,scoped):" if props else "\n        if raw != exact:")
    return f"""#!/bin/sh
set -eu
. {quote(files / 'env.sh')}
/usr/bin/python3 {quote(files / 'check-file-volume.py')}
if ! colima status filex >/dev/null 2>&1; then
 {quote(files / 'start-runtime.sh')}
fi
exec /opt/homebrew/bin/python3.12 -B - <<'FILEX_OPS_STARTUP'
import fcntl, json, os, pathlib, re, stat, subprocess
control = pathlib.Path({str(control)!r})
override = pathlib.Path({str(selector_file(control))!r})
base = {str(files / 'compose.yaml')!r}
services = ['filex', 'npc-writer', 'motion-writer', 'media-writer']
fd = os.open(control, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if os.path.lexists(override.parent / 'in-progress.json'):
        raise SystemExit(1)
    command = ['/opt/homebrew/bin/docker', 'compose', '-f', base]
    if not os.path.lexists(override):
        subprocess.run(command + ['up', '-d', 'filex', 'npc-writer', 'concept-writer',
                                  'motion-writer', 'media-writer'], check=True)
    else:
        owner = override.lstat()
        if (override.resolve() != override or not stat.S_ISREG(owner.st_mode)
                or owner.st_uid != os.getuid() or stat.S_IMODE(owner.st_mode) != 0o600):
            raise SystemExit(1)
        raw = override.read_bytes()
        image = json.loads(raw)['services']['concept-writer']['image']
        if not re.fullmatch(r'sha256:[0-9a-f]{{64}}', image):
            raise SystemExit(1)
        exact = (json.dumps({{"services":{{"concept-writer":{{"image":image}}}}}}, sort_keys=True, separators=(",", ":")) + chr(10)).encode()
        {prop_verify.lstrip()}
            raise SystemExit(1)
        subprocess.run(command + ['up', '-d', *services], check=True)
        subprocess.run(command + ['-f', str(override), 'up', '-d', '--no-deps',
                       '--no-build', '--pull', 'never', 'concept-writer'], check=True)
finally:
    os.close(fd)
FILEX_OPS_STARTUP
""".encode()


def override_bytes(image, *, prop=False):
    need(matches(IMAGE, image), "TARGET_CANDIDATE_MISMATCH")
    scope = {"environment": PROP_ENV, "volumes": [PROP_VOLUME]} if prop else {}
    return json_bytes({"services": {SERVICE: {"image": image, **scope}}})


def selector(control):
    p = selector_file(control)
    if not os.path.lexists(p):
        return None, None
    private_file(p, 0o600)
    raw = p.read_bytes()
    data = json_load(raw)
    need(shape(data, "services") and shape(data["services"], SERVICE)
         and type(data["services"][SERVICE]) is dict, "TARGET_SELECTOR_CHANGED")
    image = data["services"][SERVICE].get("image")
    need(matches(IMAGE, image) and raw in (override_bytes(image), override_bytes(image, prop=True)), "TARGET_SELECTOR_CHANGED")
    return image, raw


def runtime_pins(home, control, *, prop=False):
    runtime = home / "Workspaces/OPS/runtime/managed-runtime/ops-runtime-config.json"
    private_file(runtime, 0o600)
    registered = json_load(runtime.read_bytes()).get("registeredFilex")
    code = filex_code(home)
    need(type(registered) is dict and registered.get("schemaVersion") == "ngs.ops-filex.v1"
         and registered.get("container") == PROJECT + "-filex-1"
         and registered.get("codeRoot") == str(code) and registered.get("conceptWriter") is True
         and shape(registered.get("hashes"), " ".join(NAMES)),
         "TARGET_PRIVATE_INPUT_INVALID")
    hashes = {}
    for name in NAMES:
        p = code / name
        private_file(p)
        raw = p.read_bytes()
        hashes[name] = digest(raw)
        need(registered["hashes"][name] == hashes[name], "TARGET_SELECTOR_CHANGED")
        if name == "start-filex.sh":
            bridges = (startup_bridge(home, control),) if prop else (
                startup_bridge(home, control, props=False), startup_bridge(home, control))
            need(raw in bridges, "TARGET_SELECTOR_CHANGED")
    return hashes["compose.yaml"], hashes["start-filex.sh"]


def runtime_parts(row, *, prop=False):
    """Pin the writer's security/runtime config, excluding only container/image identity."""
    need(type(row) is dict and row.get("Name") == "/" + CONTAINER
         and matches(CONTAINER_ID, row.get("Id")) and matches(IMAGE, row.get("Image")),
         "TARGET_CANDIDATE_MISMATCH")
    cfg, host = row["Config"], row["HostConfig"]
    labels = cfg["Labels"]
    need(labels.get("com.docker.compose.project") == PROJECT
         and labels.get("com.docker.compose.service") == SERVICE, "TARGET_CANDIDATE_MISMATCH")
    pairs = [item.split("=", 1) for item in cfg["Env"] if item.startswith("WRITER_")]
    writer_env = dict(pairs)
    need(len(writer_env) == len(pairs) and writer_env == {**WRITER_ENV, **(PROP_ENV if prop else {})}
         and cfg.get("User") == "501:20" and cfg.get("Entrypoint") in (None, [])
         and cfg.get("Cmd") == ["python", "-B", "/app/server.py"] and cfg.get("WorkingDir") == "/app"
         and host.get("Privileged") is False and host.get("ReadonlyRootfs") is True
         and host.get("CapDrop") == ["ALL"] and not host.get("CapAdd")
         and host.get("SecurityOpt") in (["no-new-privileges:true"], ["no-new-privileges=true"])
         and host.get("PortBindings") == {"8093/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8095"}]}
         and host.get("RestartPolicy", {}).get("Name") == "unless-stopped"
         and not any(host.get(key) for key in ("Devices", "DeviceRequests", "PidMode", "VolumesFrom"))
         and set(row["NetworkSettings"]["Networks"]) == {PROJECT + "_writer_loopback"},
         "TARGET_PRIVATE_INPUT_INVALID")
    mounts = {x["Destination"]: x for x in row["Mounts"]}
    expected_mounts = {"/audio", "/canonical", "/run/secrets"} | ({"/props"} if prop else set())
    need(len(row["Mounts"]) == len(mounts) and set(mounts) == expected_mounts,
         "TARGET_PRIVATE_INPUT_INVALID")
    for dest, item in (("/canonical", "concept"), ("/audio", "audio")):
        v = mounts[dest]
        need(v.get("Type") == "bind" and v.get("Source") == str(PROJECT_PATH / item) and v.get("RW") is True,
             "TARGET_PRIVATE_INPUT_INVALID")
    if prop:
        v = mounts["/props"]
        need(v.get("Type") == "bind" and v.get("Source") == PROP_SOURCE and v.get("RW") is True
             and v.get("Propagation", "rprivate") == "rprivate", "TARGET_PRIVATE_INPUT_INVALID")
    secret = mounts["/run/secrets"]
    need(secret.get("Type") == "volume" and secret.get("Name") == "ngs-concept-writer-secret"
         and secret.get("RW") is False, "TARGET_PRIVATE_INPUT_INVALID")
    # Include Docker-wide mapped runtime controls without dynamic/image labels
    # and with no public payload except this digest.
    return {"host": host, "mounts": mounts, "writer": writer_env,
            "ports": cfg.get("ExposedPorts"), "health": cfg.get("Healthcheck")}


def stable_parts(parts):
    """Compare transitions while ignoring only the exact reviewed prop addition."""
    p = deepcopy(parts)
    enabled = "/props" in p["mounts"]
    p["mounts"].pop("/props", None)
    p["writer"].pop(next(iter(PROP_ENV)), None)
    mounts = p["host"].get("Mounts") or []
    if enabled:
        scoped = [v for v in mounts if v.get("Target") == "/props"]
        need(len(scoped) == 1 and scoped[0].get("Type") == "bind"
             and scoped[0].get("Source") == PROP_SOURCE
             and scoped[0].get("ReadOnly", False) is False
             and scoped[0].get("BindOptions", {}).get("CreateMountpoint", False) is False,
             "TARGET_PRIVATE_INPUT_INVALID")
        for k,v in scoped[0].items():
            need(k in {"Type","Source","Target","ReadOnly","BindOptions"} or not v, "TARGET_PRIVATE_INPUT_INVALID")
        need(not any(x for k,x in scoped[0].get("BindOptions", {}).items()
                     if k not in {"CreateMountpoint","Propagation"}), "TARGET_PRIVATE_INPUT_INVALID")
        need(scoped[0].get("BindOptions", {}).get("Propagation", "rprivate") in ("", "rprivate"),
             "TARGET_PRIVATE_INPUT_INVALID")
        mounts = [v for v in mounts if v is not scoped[0]]
    need(not any(v.get("Target") == "/props" for v in mounts), "TARGET_PRIVATE_INPUT_INVALID")
    if mounts:
        p["host"]["Mounts"] = mounts
    elif "Mounts" in p["host"]:
        del p["host"]["Mounts"]
    return p


def runtime_fingerprint(row, *, prop=False):
    return digest(json_bytes(runtime_parts(row, prop=prop)))


class DockerHost:
    executable = "/opt/homebrew/bin/docker"

    def __init__(self, home, control):
        self.home, self.control = home, control
        self.files = filex_code(home)
        runtime = home / "Workspaces/Filex/runtime"
        self.socket = runtime / "colima/filex/docker.sock"
        self.client = runtime / "docker-client"

    def call(self, *args, data=None, timeout=20, code="TARGET_LAUNCH_STATUS_FAILED"):
        p = self.socket
        s = p.stat()
        need(stat.S_ISSOCK(s.st_mode) and s.st_uid == os.getuid() and p.resolve() == p,
             "TARGET_PRIVATE_INPUT_INVALID")
        env = {"HOME": str(self.home), "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
               "DOCKER_CONFIG": str(self.client)}
        result = subprocess.run([self.executable, "--host", "unix://" + str(p), *args],
                                input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=env, cwd=self.files, timeout=timeout, check=False)
        need(result.returncode == 0 and len(result.stdout) <= 256 * 1024, code)
        return result.stdout

    def current(self):
        rows = json_load(self.call("container", "inspect", CONTAINER))
        need(type(rows) is list and len(rows) == 1, "TARGET_LAUNCH_STATUS_FAILED")
        return rows[0]

    def image(self, ref):
        need(matches(IMAGE, ref) or re.fullmatch(r"ngs-concept-writer:[a-f0-9]{7,40}", ref),
             "TARGET_CANDIDATE_MISMATCH")
        rows = json_load(self.call("image", "inspect", ref))
        need(type(rows) is list and len(rows) == 1 and matches(IMAGE, rows[0].get("Id"))
             and rows[0].get("Os") == "linux" and rows[0].get("Architecture") == "arm64",
             "TARGET_CANDIDATE_MISMATCH")
        return rows[0]["Id"]

    def healthy(self, row):
        need(row.get("State", {}).get("Running") is True, "TARGET_HEALTH_HTTP")
        container = row["Id"]
        need(matches(CONTAINER_ID, container), "TARGET_HEALTH_HTTP")
        output = self.call("exec", "-i", container, "/usr/local/bin/python", "-B", "-",
                           data=PROBE, timeout=8, code="TARGET_HEALTH_HTTP")
        capability = capabilities_digest(json_load(output))
        need(capability is not None, "TARGET_HEALTH_HTTP")
        connection = http.client.HTTPConnection("127.0.0.1", 8095, timeout=2)
        try:
            connection.request("GET", "/v1/capabilities")
            response = connection.getresponse()
            need(response.status == 401 and json_load(response.read(4096)) == {"error": "UNAUTHORIZED"},
                 "TARGET_HEALTH_HTTP")
        finally:
            connection.close()
        return capability

    def compose(self, active):
        cmd = ["compose", "--project-name", PROJECT, "--project-directory", str(self.files),
               "-f", str(self.files / "compose.yaml")]
        if active:
            cmd += ["-f", str(selector_file(self.control))]
        return cmd

    def selected_image(self, active):
        model = json_load(self.call(*self.compose(active), "config", "--format", "json"))
        need(model.get("name") == PROJECT, "TARGET_SELECTOR_CHANGED")
        name = model["services"][SERVICE]["image"]
        need(type(name) is str, "TARGET_SELECTOR_CHANGED")
        return self.image(name)

    def recreate(self, active):
        self.call(*self.compose(active), "up", "-d", "--no-deps", "--no-build",
                  "--pull", "never", "--force-recreate", SERVICE, timeout=60,
                  code="TARGET_START_FAILED")


def check_current(host, home, control, active, expected, fingerprint=None, old_id=None, *, capability=None, invariant=None):
    row = host.current()
    _, raw = selector(control) if active else (None,None)
    prop = raw == override_bytes(expected, prop=True) if raw is not None else False
    parts = runtime_parts(row, prop=prop)
    observed = digest(json_bytes(parts))
    current_invariant = stable_parts(parts) if prop or invariant is not None else None
    if invariant is not None:
        need(current_invariant == invariant, "TARGET_PRIVATE_INPUT_INVALID")
    need(row.get("Image") == expected and row.get("Id") != old_id
         and (fingerprint is None or fingerprint == observed), "TARGET_CANDIDATE_MISMATCH")
    labels = row["Config"]["Labels"]
    config = str(filex_code(home) / "compose.yaml")
    expected_files = config + ("," + str(selector_file(control)) if active else "")
    need(labels.get("com.docker.compose.project.config_files") == expected_files,
         "TARGET_SELECTOR_CHANGED")
    live_capability = host.healthy(row)
    need(live_capability in CAPABILITY_DIGESTS and prop == (live_capability == PROP_CAP_DIGEST)
         and (capability is None or live_capability==capability), "TARGET_HEALTH_HTTP")
    # Avoid accepting health from a vanished/replaced container.
    again = host.current()
    need(again.get("Id") == row["Id"] and again.get("Image") == expected
         and again.get("State", {}).get("Running") is True, "TARGET_HEALTH_HTTP")
    return row, observed, live_capability


def filex_prior(home, control, *, host=None):
    """Read-only prerequisite for auto-preparation; does not write a selector."""
    home, control = Path(home), Path(control)
    host = host if host is not None else DockerHost(home, control)
    need(not os.path.lexists(target_directory(control) / "in-progress.json"), "TARGET_SELECTOR_CHANGED")
    base, startup = runtime_pins(home, control)
    active, raw = selector(control)
    if active and raw == override_bytes(active, prop=True):
        need(runtime_pins(home, control, prop=True) == (base,startup), "TARGET_SELECTOR_CHANGED")
    pinned = host.selected_image(active is not None)
    need(active is None or pinned == active, "TARGET_SELECTOR_CHANGED")
    _, fingerprint, capability = check_current(host, home, control, active is not None, pinned)
    return {"priorImageId": pinned, "composeSha256": base, "startupSha256": startup,
            "runtimeFingerprint": fingerprint,
            **({"priorCapabilitiesSha256": capability} if capability != LEGACY_CAP_DIGEST else {})}


def verify_active_candidate(home, control, applied, *, host=None):
    """PDS dependency proof: validate registered selection and exact live runtime."""
    need(observed_shape(applied, CANDIDATE_KEYS + " deployedAt")
         and applied["schemaVersion"] == APPLIED and applied["target"] == TARGET,
         "TARGET_CANDIDATE_MISMATCH")
    expected = {"priorImageId": applied["imageId"], "composeSha256": applied["composeSha256"],
                "startupSha256": applied["startupSha256"], "runtimeFingerprint": applied["runtimeFingerprint"]}
    if "capabilitiesSha256" in applied:
        expected["priorCapabilitiesSha256"] = applied["capabilitiesSha256"]
    current = filex_prior(home, control, host=host)
    active, _ = selector(Path(control))
    need(expected == current and active == applied["imageId"], "TARGET_CANDIDATE_MISMATCH")
    return True


def verified_candidate(directory, control, sha, artifact):
    p = directory / "candidate.json"
    private_file(p, 0o600)
    raw = p.read_bytes()
    bound = control / "filex-writer-prepared" / (sha + ".apply.json")
    private_file(bound, 0o600)
    need(bound.read_bytes() == raw, "TARGET_CANDIDATE_MISMATCH")
    c = json_load(raw)
    need(observed_shape(c, CANDIDATE_KEYS) and c["schemaVersion"] == CANDIDATE and c["target"] == TARGET
         and c["sha"] == sha and c["artifactSha256"] == artifact
         and (c["imageId"] != c["priorImageId"] or (c.get("capabilitiesSha256") == PROP_CAP_DIGEST
              and c.get("priorCapabilitiesSha256") != PROP_CAP_DIGEST))
         and all(matches(IMAGE, c[k]) for k in ("imageId", "priorImageId"))
         and all(matches(DIGEST, c[k]) for k in ("composeSha256", "startupSha256", "runtimeFingerprint")),
         "TARGET_CANDIDATE_MISMATCH")
    p = control / "filex-writer-prepared" / (sha + ".json")
    private_file(p, 0o600)
    proof = json_load(p.read_bytes())
    policy_file = control / "filex-writer-preparation.json"
    private_file(policy_file, 0o600)
    policy = json_load(policy_file.read_bytes())
    need(shape(policy, "schemaVersion base runnerSha256 composeSha256 startupSha256")
         and policy["schemaVersion"] == POLICY and matches(BASE, policy["base"])
         and policy["runnerSha256"] == digest(Path(__file__).read_bytes())
         and all(policy[k] == c[k] for k in ("composeSha256", "startupSha256"))
         and proof.get("base") == policy["base"], "TARGET_PRIVATE_INPUT_INVALID")
    need(proof.get("schemaVersion") == "ops.filex-writer-candidate.v1"
         and proof.get("target") == "filex-concept-writer" and proof.get("state") == "image-verified"
         and proof.get("imageId") == c["imageId"] and proof.get("source", {}).get("headSha") == sha
         and proof.get("provenance", {}).get("artifactSha256") == artifact
         and capability_markers(proof, {"capabilitiesSha256"})
         and proof.get("capabilitiesSha256") == c.get("capabilitiesSha256"),
         "TARGET_CANDIDATE_MISMATCH")
    return c


class ApplyFailure(RuntimeError):
    def __init__(self, phase, code, rollback="not_attempted"):
        super().__init__(code)
        self.phase, self.code, self.rollback = phase, code, rollback


CODES = {"TARGET_INPUT_INVALID", "TARGET_CANDIDATE_MISMATCH", "TARGET_SELECTOR_CHANGED",
         "TARGET_PRIVATE_INPUT_INVALID", "TARGET_PRIOR_NOT_RUNNING", "TARGET_LAUNCH_STATUS_FAILED",
         "TARGET_START_FAILED", "TARGET_HEALTH_HTTP", "TARGET_APPLY_REFUSED", "TARGET_OPERATION_TIMEOUT"}


def diagnostic(error):
    if isinstance(error, subprocess.TimeoutExpired):
        return "TARGET_OPERATION_TIMEOUT"
    code = error.args[0] if len(error.args) == 1 else None
    return code if type(code) is str and code in CODES else "TARGET_APPLY_REFUSED"


def bounded_health(host, home, control, active, expected, fingerprint, old_id=None, *, capability, invariant=None):
    end = time.monotonic() + 20
    while True:
        try:
            return check_current(host, home, control, active, expected, fingerprint, old_id, capability=capability, invariant=invariant)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, http.client.HTTPException) as error:
            if time.monotonic() >= end:
                raise ApplyFailure("health", diagnostic(error)) from None
            time.sleep(.25)


def restore_selector(control, expected, old):
    p = selector_file(control)
    _, actual = selector(control)
    need(actual in (old, expected), "TARGET_SELECTOR_CHANGED")
    if actual == old:
        return
    if old is not None:
        atomic_write(p, old)
    else:
        # Undo only our own just-written bytes; no pre-existing selector is deleted.
        p.unlink()
        fd = os.open(p.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def apply_target(sha, artifact, home, control, *, host=None):
    """Given owner-admitted immutable target proof, replace only concept-writer."""
    phase = "preflight"
    fd = None
    rollback = "not_attempted"
    try:
        need(matches(SHA, sha) and matches(DIGEST, artifact), "TARGET_INPUT_INVALID")
        home, control = Path(home), Path(control)
        directory = target_directory(control)
        private_directory(control)
        private_directory(directory)
        fd = os.open(control, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        candidate = verified_candidate(directory, control, sha, artifact)
        host = host if host is not None else DockerHost(home, control)
        prior, image = candidate["priorImageId"], candidate["imageId"]
        expected_pins = {k: candidate[k] for k in ("priorImageId", "composeSha256", "startupSha256", "runtimeFingerprint",
                                                   "priorCapabilitiesSha256") if k in candidate}
        prior_cap = candidate.get("priorCapabilitiesSha256", LEGACY_CAP_DIGEST)
        next_cap = candidate.get("capabilitiesSha256", LEGACY_CAP_DIGEST)
        need(filex_prior(home, control, host=host) == expected_pins and host.image(image) == image,
             "TARGET_CANDIDATE_MISMATCH")
        active, old = selector(control)
        row, _, _ = check_current(host, home, control, active is not None, prior,
                                  candidate["runtimeFingerprint"], capability=prior_cap)
        prop = next_cap == PROP_CAP_DIGEST
        prior_prop = prior_cap == PROP_CAP_DIGEST
        need(not prior_prop or prop, "TARGET_PRIVATE_INPUT_INVALID")
        if prop:
            root = Path(PROP_SOURCE)
            need(root.resolve(strict=True) == root and root.is_dir() and root.stat().st_uid == os.getuid()
                 and runtime_pins(home, control, prop=True) == (candidate["composeSha256"], candidate["startupSha256"]),
                 "TARGET_PRIVATE_INPUT_INVALID")
        before_id = row["Id"]
        invariant = stable_parts(runtime_parts(row,prop=prior_prop)) if prop else None
        fingerprint = candidate["runtimeFingerprint"]
        armed = directory / "in-progress.json"
        need(not os.path.lexists(armed), "TARGET_SELECTOR_CHANGED")
        applied = directory / "applied"
        applied.mkdir(mode=0o700, exist_ok=True)
        private_directory(applied)
        proof = applied / (sha + ".json")
        need(not os.path.lexists(proof), "TARGET_SELECTOR_CHANGED")
        new = override_bytes(image, prop=prop)
        # Arm durably before first mutation. A hard interruption blocks retries.
        atomic_write(armed, json_bytes({"sha": sha, "priorImageId": prior, "imageId": image,
                                        "priorOverridePresent": active is not None}))
        try:
            phase = "selector"
            atomic_write(selector_file(control), new)
            need(host.selected_image(True) == image, "TARGET_SELECTOR_CHANGED")
            phase = "start"
            host.recreate(True)
            phase = "health"
            _, live_fp, _ = bounded_health(host, home, control, True, image, None if prop else fingerprint, before_id,
                                           capability=next_cap,invariant=invariant)
            need(runtime_pins(home, control) == (candidate["composeSha256"], candidate["startupSha256"])
                 and selector(control) == (image, new), "TARGET_SELECTOR_CHANGED")
        except Exception as error:
            rollback = "failed"
            try:
                restore_selector(control, new, old)
                need(runtime_pins(home, control) == (candidate["composeSha256"], candidate["startupSha256"])
                     and host.selected_image(active is not None) == prior, "TARGET_SELECTOR_CHANGED")
                try:
                    check_current(host, home, control, active is not None, prior, fingerprint, capability=prior_cap)
                except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, http.client.HTTPException):
                    host.recreate(active is not None)
                bounded_health(host, home, control, active is not None, prior, fingerprint, capability=prior_cap)
                armed.unlink()
                rollback = "restored"
            except Exception:
                pass
            raise ApplyFailure(phase, diagnostic(error), rollback) from None
        now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        atomic_write(proof, json_bytes({**candidate, "runtimeFingerprint": live_fp, "schemaVersion": APPLIED, "deployedAt": now}))
        armed.unlink()
        return {"schemaVersion": "ops.deploy-target-receipt.v1", "target": TARGET, "sha": sha,
                "deployedAt": now, "state": "deployed", "health": "verified"}
    except ApplyFailure:
        raise
    except Exception as error:
        raise ApplyFailure(phase, diagnostic(error), rollback) from None
    finally:
        if fd is not None:
            os.close(fd)


def main():
    executable = Path(sys.argv[0]).absolute()
    home = Path.home().resolve()
    control = home / "Workspaces/OPS/runtime/deploy-control"
    sha, artifact = os.environ.get("OPS_DEPLOY_CANDIDATE_SHA"), os.environ.get("OPS_DEPLOY_ARTIFACT_SHA256")
    try:
        if len(sys.argv) != 1 or executable != target_directory(control) / "run-once":
            raise ApplyFailure("preflight", "TARGET_INPUT_INVALID")
        need(matches(SHA,sha) and matches(DIGEST,artifact), "TARGET_INPUT_INVALID")
        p=target_directory(control)/"candidate.json"
        private_file(p,0o600)
        candidate=json_load(p.read_bytes())
        if candidate.get('schemaVersion')=='ops.filex-unified-apply-candidate.v1':
            sys.modules.setdefault('filex_writer_apply',sys.modules[__name__])
            from filex_unified_runtime import CutoverFailure, apply_managed
            try:
                receipt=apply_managed(sha,artifact,home,control)
            except CutoverFailure as error:
                phase={'pause_backend':'unload','hold_writers':'unload','select_writer':'selector',
                       'start_backend':'start','commit':'health'}.get(error.phase,'health')
                raise ApplyFailure(phase,"TARGET_APPLY_REFUSED",error.rollback) from None
        else:
            receipt=apply_target(sha,artifact,home,control)
    except (ApplyFailure, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as e:
        if not isinstance(e,ApplyFailure):
            e=ApplyFailure("preflight",diagnostic(e))
        error=e
        if matches(SHA, sha):
            sys.stdout.buffer.write(json_bytes({"schemaVersion": "ops.deploy-target-error.v1", "target": TARGET,
                "sha": sha, "phase": error.phase, "code": error.code, "rollback": error.rollback}))
        return 1
    sys.stdout.buffer.write(json_bytes(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
