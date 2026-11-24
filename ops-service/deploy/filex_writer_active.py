"""Read-only PDS prerequisite: exact active Filex concept/BGM writer provenance."""
from datetime import datetime
from pathlib import Path
import re
import subprocess

from filex_writer_apply import LEGACY_CAP_DIGEST, PROP_CAP_DIGEST, observed_shape
from prepare_filex_writer import BASE, CLOSURE, PROP_CLOSURE, IMAGE_ID, REPO, SCHEMA, closure, declared_capabilities
from prepare_ngs_local_once import read_private
from prepare_ngs_release import DIGEST, REF, SHA, digest, json_load, matches, need, private_directory, shape

TARGET = 'filex-writer'
APPLIED = 'ops.filex-writer-applied.v1'
TERMINAL = 'ops.deploy-target-result.v1'
EVIDENCE = 'ops.filex-writer-candidate.v1'
STAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z')
NUMBER = re.compile(r'[1-9][0-9]{0,19}')
FAIL = 'FILEX_ACTIVE_PROOF_INVALID'


def stamp(value):
    need(matches(STAMP, value), FAIL)
    return datetime.fromisoformat(value)


def receipt(value, expected):
    need(shape(value, 'schemaVersion target attemptedSha state finishedAt observed')
         and value['schemaVersion'] == TERMINAL and value['target'] == TARGET
         and value['state'] == 'deployed' and value['attemptedSha'] == expected
         and shape(value['observed'], 'sha deployedAt') and value['observed']['sha'] == expected
         and stamp(value['observed']['deployedAt']) <= stamp(value['finishedAt']), FAIL)
    return value['observed']['deployedAt']


def applied(value, expected, deployed_at):
    need(observed_shape(value, 'schemaVersion target sha artifactSha256 imageId priorImageId composeSha256 startupSha256 runtimeFingerprint deployedAt')
         and value['schemaVersion'] == APPLIED and value['target'] == TARGET and value['sha'] == expected
         and value['deployedAt'] == deployed_at and matches(IMAGE_ID, value['imageId'])
         and matches(IMAGE_ID,value['priorImageId'])
         and all(matches(DIGEST, value[k]) for k in ('artifactSha256','composeSha256','startupSha256','runtimeFingerprint')),
         FAIL)
    return value


def prepared(value, proof):
    need(observed_shape(value, 'schemaVersion target state source provenance base imageId smoke preparedAt', {'capabilitiesSha256'})
         and value['schemaVersion'] == EVIDENCE and value['target'] in ('filex-concept-writer', TARGET)
         and value['state'] == 'image-verified'
         and value['smoke'] == 'isolated-authenticated-capabilities' and matches(BASE, value['base'])
         and value['imageId'] == proof['imageId'] and value.get('capabilitiesSha256') == proof.get('capabilitiesSha256') and stamp(value['preparedAt']) <= stamp(proof['deployedAt']), FAIL)
    p = value['provenance']
    need(shape(p, 'artifactId artifactSha256') and matches(NUMBER,p['artifactId'])
         and p['artifactSha256'] == proof['artifactSha256'], FAIL)
    s = value['source']
    need(shape(s, 'schemaVersion repository ref headSha runId runAttempt rootTree files')
         and s['schemaVersion'] == SCHEMA and s['repository'] == REPO and s['ref'] == REF
         and s['headSha'] == proof['sha'] and matches(SHA,s['rootTree'])
         and all(matches(NUMBER,s[k]) for k in ('runId','runAttempt'))
         and type(s['files']) is dict and set(s['files']) == (PROP_CLOSURE if proof.get('capabilitiesSha256') == PROP_CAP_DIGEST else CLOSURE)
         and all(matches(DIGEST,v) for v in s['files'].values()), FAIL)
    return s['files']


def _verify(home, control, repo, head, runtime, host):
    need(matches(SHA, head), FAIL)
    folder = control/'targets'/TARGET
    applied_root = folder/'applied'
    staged_root = control/'filex-writer-prepared'
    for d in (control,control/'targets',folder,applied_root,staged_root):
        private_directory(d)
    snapshots = {}
    def exact(path):
        raw = read_private(path, 64*1024, mode=0o600)
        snapshots[path] = raw
        return json_load(raw)
    result = exact(folder/'last-result.json')
    need(type(result) is dict and matches(SHA,result.get('attemptedSha')), FAIL)
    active_sha = result['attemptedSha']
    when = receipt(result,active_sha)
    proof = applied(exact(applied_root/(active_sha+'.json')),active_sha,when)
    verified_files = prepared(exact(staged_root/(active_sha+'.json')),proof)
    # The required *runtime closure* comes from this exact service candidate.
    # If only documentation or other targets changed, the already running
    # writer closure can still satisfy it without a pointless restart.
    _, required = closure(repo,head)
    need(verified_files == {name:digest(body) for name,body in required.items()}
         and declared_capabilities(required['server.py']) == proof.get('capabilitiesSha256',LEGACY_CAP_DIGEST), FAIL)
    need(runtime(home,control,proof,host=host) is True, FAIL)
    need(all(read_private(path,64*1024,mode=0o600)==before for path,before in snapshots.items()), FAIL)
    return True


def filex_active(home, control, repo, head, *, host=None, runtime=None):
    """True only for successful OPS apply + running authenticated exact closure.

    The injectable verifier is for tests only. The production default is the
    separately pinned, fixed-service read-only adapter function. Any missing
    or mismatched evidence keeps Backend Stack's Filex prerequisite pending.
    """
    try:
        if runtime is None:
            from filex_writer_apply import verify_active_candidate
            runtime = verify_active_candidate
        return _verify(Path(home),Path(control),Path(repo),head,runtime,host)
    except (OSError,ValueError,TypeError,KeyError,AttributeError,ImportError,RuntimeError,subprocess.SubprocessError):
        return False
