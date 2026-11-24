"""Create manual-only Filex readiness; never execute the Filex apply adapter."""
from datetime import UTC, datetime
import os
import re

from prepare_filex_writer import BASE, CLOSURE, PROP_CLOSURE, PROP_CAP_DIGEST, IMAGE_ID, REPO, SCHEMA, TARGET as BUILD_TARGET, prepare
from filex_writer_apply import observed_shape
from prepare_ngs_local_once import lock, read_optional, read_private, runner_digest
from prepare_ngs_release import (
    DIGEST, NUMBER, REF, SHA, atomic_write, json_bytes, json_load, matches,
    need, private_directory, shape,
)


TARGET = 'filex-writer'
CONFIG_NAME = 'filex-writer-preparation.json'
CONFIG_SCHEMA = 'ops.filex-writer-preparation.v1'
CANDIDATE = 'ops.filex-writer-apply-candidate.v1'
READY = 'ops.deploy-target-ready.v1'
STATE = 'ops.filex-writer-candidate.v1'
PRIOR_FIELDS = 'priorImageId composeSha256 startupSha256 runtimeFingerprint'
SOURCE_FIELDS = 'schemaVersion repository ref headSha runId runAttempt rootTree files'
STAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z')


def config(control):
    private_directory(control)
    path = control/CONFIG_NAME
    try:
        raw = read_private(path,8192,mode=0o600)
    except FileNotFoundError:
        return None
    value = json_load(raw)
    need(shape(value,'schemaVersion base runnerSha256 composeSha256 startupSha256')
         and value['schemaVersion']==CONFIG_SCHEMA and matches(BASE,value['base'])
         and all(matches(DIGEST,value[k]) for k in ('runnerSha256','composeSha256','startupSha256')),
         'FILEX_PREP_CONFIG_INVALID')
    return value,raw


def expected_prior(prior, policy):
    need(observed_shape(prior,PRIOR_FIELDS,{'priorCapabilitiesSha256'}) and matches(IMAGE_ID,prior['priorImageId'])
         and all(matches(DIGEST,prior[k]) for k in ('composeSha256','startupSha256','runtimeFingerprint'))
         and prior['composeSha256']==policy['composeSha256'] and prior['startupSha256']==policy['startupSha256'],
         'FILEX_PRIOR_RUNTIME_UNVERIFIED')
    return prior


def image_evidence(value, head, policy):
    need(observed_shape(value,'schemaVersion target state source provenance base imageId smoke preparedAt',{'capabilitiesSha256'})
         and value['schemaVersion']==STATE and value['target']==BUILD_TARGET and value['state']=='image-verified'
         and value['base']==policy['base'] and matches(IMAGE_ID,value['imageId'])
         and value['smoke']=='isolated-authenticated-capabilities'
         and matches(STAMP,value['preparedAt']), 'FILEX_BUILD_PROOF_INVALID')
    source, proof = value['source'], value['provenance']
    need(shape(source,SOURCE_FIELDS) and source['schemaVersion']==SCHEMA and source['repository']==REPO
         and source['ref']==REF and source['headSha']==head and matches(SHA,source['rootTree'])
         and matches(NUMBER,source['runId']) and matches(NUMBER,source['runAttempt'])
         and type(source['files']) is dict and set(source['files'])==(PROP_CLOSURE if value.get("capabilitiesSha256")==PROP_CAP_DIGEST else CLOSURE)
         and all(matches(DIGEST,item) for item in source['files'].values())
         and shape(proof,'artifactId artifactSha256') and matches(NUMBER,proof['artifactId'])
         and matches(DIGEST,proof['artifactSha256']), 'FILEX_BUILD_PROOF_INVALID')
    return value['imageId'], proof['artifactSha256']


def prepare_filex_candidate(github, repo, home, control, head, *, assert_current,
                            read_prior, docker, prepare_image=prepare):
    """Validate prior with a read-only observer, smoke, then register exact manual prep."""
    loaded = config(control)
    if loaded is None:
        return {'target':TARGET,'sha':head,'state':'not_configured','code':'FILEX_PREPARER_NOT_PROVISIONED'}
    policy, policy_raw = loaded
    need(matches(SHA,head), 'FILEX_COMMIT_INVALID')
    directory = control/'targets'/TARGET
    private_directory(control/'targets')
    private_directory(directory)
    runner = runner_digest(directory)
    need(runner==policy['runnerSha256'], 'FILEX_RUNNER_PIN_MISMATCH')
    assert_current(head)
    prior = expected_prior(read_prior(home,control),policy)
    proof = prepare_image(github,repo,control,policy['base'],docker)
    image, archive = image_evidence(proof,head,policy)
    assert_current(head)
    need(docker.verify_image(image) is True, 'FILEX_IMAGE_ID_INVALID')
    next_capability = ({"capabilitiesSha256": proof["capabilitiesSha256"]} if "capabilitiesSha256" in proof else {})
    candidate = dict(schemaVersion=CANDIDATE,target=TARGET,sha=head,artifactSha256=archive,imageId=image,**prior,**next_capability)
    bound = control/'filex-writer-prepared'/(head+'.apply.json')
    ready = dict(schemaVersion=READY,target=TARGET,sha=head,artifactSha256=archive,runnerSha256=runner,manualOnly=True)
    with lock(control/'cycle.lock',create=True):
        need(not os.path.lexists(control/'active.json'),'PREP_ACTIVE_DEPLOYMENT')
        assert_current(head)
        need(config(control)==loaded and runner_digest(directory)==runner
             and expected_prior(read_prior(home,control),policy)==prior and docker.verify_image(image) is True,
             'FILEX_PREPARATION_CHANGED')
        private_directory(bound.parent)
        proof_file = bound.parent/(head+'.json')
        need(json_load(read_private(proof_file,64*1024,mode=0o600))==proof,'FILEX_BUILD_PROOF_INVALID')
        if image==prior['priorImageId'] and next_capability.get('capabilitiesSha256')==prior.get('priorCapabilitiesSha256'):
            return {'target':TARGET,'sha':head,'state':'current'}
        last = read_optional(directory/'last-result.json')
        need(not (last and last.get('attemptedSha')==head and last.get('state')=='failed'),
             'FILEX_RECONCILE_REQUIRED')
        if os.path.lexists(bound):
            need(read_private(bound,8192,mode=0o600)==json_bytes(candidate),'FILEX_PREPARATION_CHANGED')
        else:
            atomic_write(bound,json_bytes(candidate))
        old = read_optional(directory/'ready.json')
        if old and all(old.get(key)==val for key,val in ready.items()):
            need(read_optional(directory/'candidate.json')==candidate,'FILEX_PREPARATION_CHANGED')
            return {'target':TARGET,'sha':head,'state':'ready'}
        # Under the same cycle lock, a final native/CAS check precedes both
        # pointers. If writing ready fails, restore only our staged pointer.
        candidate_file = directory/'candidate.json'
        try:
            previous = read_private(candidate_file,8192,mode=0o600)
        except FileNotFoundError:
            previous = None
        assert_current(head)
        need(config(control)==loaded and runner_digest(directory)==runner
             and expected_prior(read_prior(home,control),policy)==prior,
             'FILEX_PREPARATION_CHANGED')
        atomic_write(candidate_file,json_bytes(candidate))
        try:
            ready['verifiedAt']=datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
            atomic_write(directory/'ready.json',json_bytes(ready))
        except BaseException:
            if previous is None:
                candidate_file.unlink()
            else:
                atomic_write(candidate_file,previous)
            raise
    return {'target':TARGET,'sha':head,'state':'registered'}
