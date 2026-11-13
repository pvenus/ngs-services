"""Explicit-turn mirror journal. Domain state stays in existing authoritative services."""
import copy
import fcntl
import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class MirrorError(ValueError):
    pass


def canonical(value):
    # Mirrors Site canonical(): sorted object keys, original array order.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(canonical(value))


def durable(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    with open(temp, 'wb') as stream:
        stream.write(canonical(value)); stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def snapshot(project, capture, timestamp=None):
    """Capture must contain every reviewed resource; never truncate to fit transport."""
    resources, objects = [], {}
    for logical_id, item in sorted(capture['resources'].items()):
        raw = item['bytes']
        resource = dict(logicalResourceId=logical_id, kind=item['kind'], sha256=sha(raw), size=len(raw), mime=item['mime'])
        if len(raw) > 24576:
            resource['chunks'] = [{'sha256': sha(raw[offset:offset + 24576]), 'size': len(raw[offset:offset + 24576])} for offset in range(0, len(raw), 24576)]
        if 'assetId' in item: resource['assetId'] = item['assetId']
        resources.append(resource); objects[logical_id] = raw
    vector = copy.deepcopy(capture['sourceRevisionVector'])
    revision = digest(vector)
    identity_body={'projectId': project, 'sourceRevisionVector': vector, 'resources': resources}
    if 'consistency' in capture: identity_body['consistency']=copy.deepcopy(capture['consistency'])
    identity = digest(identity_body)
    manifest = dict(schemaVersion='npc-mirror.v1', projectId=project, snapshotId='snapshot.' + identity,
                    sourceRevision=revision, sourceRevisionVector=vector,
                    lastSyncedAt=timestamp or datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'), resources=resources)
    if 'consistency' in capture: manifest['consistency']=copy.deepcopy(capture['consistency'])
    manifest['contentHash'] = digest(manifest)
    return {'manifest': manifest, 'objects': objects, 'gaps': capture.get('gaps', [])}


def export_bundle(root, bundle):
    """Persist reviewed bytes in caller-selected staging, never the source DB."""
    manifest = bundle['manifest']; directory = Path(root) / digest({'projectId': manifest['projectId'], 'snapshotId': manifest['snapshotId']})
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / 'export.lock', 'a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for resource in manifest['resources']:
            raw = bundle['objects'][resource['logicalResourceId']]
            if sha(raw) != resource['sha256'] or len(raw) != resource['size']: raise MirrorError('OBJECT_HASH_MISMATCH')
            path = directory / resource['sha256']
            if path.exists():
                if path.is_symlink() or path.read_bytes() != raw: raise MirrorError('EXPORTED_OBJECT_CONFLICT')
            else:
                with open(path, 'xb') as stream:
                    stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        evidence = {'manifest': manifest, 'gaps': bundle.get('gaps', [])}
        path = directory / 'bundle.json'
        if path.exists():
            if path.is_symlink() or json.loads(path.read_text()) != evidence: raise MirrorError('EXPORTED_MANIFEST_CONFLICT')
        else: durable(path, evidence)
    return directory


def load_bundle(directory):
    directory = Path(directory)
    evidence = json.loads((directory / 'bundle.json').read_text()); manifest = evidence['manifest']
    body = dict(manifest); body.pop('contentHash')
    if digest(body) != manifest['contentHash']: raise MirrorError('MANIFEST_HASH_MISMATCH')
    objects = {}
    for resource in manifest['resources']:
        identity = resource['sha256']
        if not re.fullmatch('[a-f0-9]{64}', identity): raise MirrorError('OBJECT_HASH_INVALID')
        path = directory / identity
        if path.is_symlink(): raise MirrorError('EXPORTED_OBJECT_LINK')
        raw = path.read_bytes()
        if sha(raw) != identity or len(raw) != resource['size']: raise MirrorError('OBJECT_HASH_MISMATCH')
        objects[resource['logicalResourceId']] = raw
    return {**evidence, 'objects': objects}


class Journal:
    """File checkpoints are execution evidence, never a shadow source database."""
    def __init__(self, root):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def locked(self, identity):
        key = sha(identity.encode())
        with open(self.root / (key + '.lock'), 'a+b') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.root / (key + '.json')
            yield path, json.loads(path.read_text()) if path.exists() else {}


def process_approved(journal, request, approval, workflow, publish):
    """workflow.accept/apply/recover/verify use existing local workflow + CAS.

    The workflow must durably deduplicate requestHash before applying; recover()
    resolves response loss. A receipt is independently re-read before publishing.
    publish(receipt) exports that EXACT revision and returns verified Site snapshot.
    """
    body = {k: request[k] for k in ('projectId', 'requestId', 'baseRevision', 'operation', 'targetId', 'logicalResourceId', 'payload', 'idempotencyKey')}
    if 'baseHash' in request: body['baseHash'] = request['baseHash']
    request_hash = digest(body)
    if approval != {'requestHash': request_hash, 'approved': True}:
        raise MirrorError('EXACT_REQUEST_APPROVAL_REQUIRED')
    with journal.locked('change:' + request['projectId'] + ':' + request['idempotencyKey']) as (path, state):
        if state and state['requestHash'] != request_hash: raise MirrorError('IDEMPOTENCY_CONFLICT')
        if not state:
            state = {'requestHash': request_hash, 'request': body, 'status': 'accepted', 'publicationState': 'pending'}
            durable(path, state)
        if state['status'] in ('conflict', 'failed'): return state
        if 'commitReceipt' not in state:
            receipt = workflow.recover(request_hash)
            if receipt is None:
                if workflow.source_revision() != request['baseRevision']:
                    state.update(status='conflict', error='BASE_REVISION_CONFLICT'); durable(path, state); return state
                workflow.accept(body, request_hash)
                state['status'] = 'processing'; durable(path, state)
                # Response loss leaves processing; next turn recover before apply.
                receipt = workflow.apply(body, request_hash)
            if not workflow.verify(receipt, body, request_hash): raise MirrorError('LOCAL_COMMIT_NOT_CONFIRMED')
            if not all(receipt.get(k) for k in ('sourceRevision', 'commitId', 'documentHash')):
                raise MirrorError('COMMIT_RECEIPT_REQUIRED')
            state.update(status='applied', localPhase='source_committed', commitReceipt=receipt)
            durable(path, state)
        if state['publicationState'] != 'published':
            if not workflow.verify(state['commitReceipt'], body, request_hash): raise MirrorError('LOCAL_COMMIT_NOT_CONFIRMED')
            published = publish(state['commitReceipt'])
            if published['sourceRevision'] != state['commitReceipt']['sourceRevision']:
                raise MirrorError('PUBLISHED_REVISION_MISMATCH')
            state.update(publicationState='published', publishedSnapshotId=published['snapshotId'], lastSyncedAt=published['lastSyncedAt'])
            durable(path, state)
        return state
