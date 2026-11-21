"""Pure PDS-compatible serialization and fail-closed current-only capacity proof.

No clock, files, database, HTTP, credentials or authority issuance. A preview hash
is NOT the future hash until the writer supplies its own actual savedAt. Capacity
is proven for BOTH possible datetime.isoformat() lengths (25 and32 UTF-8 bytes).
"""
import copy
import hashlib
import json
import re
from datetime import datetime

VERSION = 'anchored-concept-capacity.v1'
MAX_TOTAL = 157935591
MAX_DELTA = 1048576
STAMP_EXAMPLES = ('2000-01-01T00:00:00+00:00', '2000-01-01T00:00:00.123456+00:00')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def envelope(expected_revision, note, validated_document, saved_at):
    if type(expected_revision) is not int or expected_revision < 1 or not isinstance(note, str) or not 3 <= len(note.strip()) <= 300:
        raise ValueError('CONCEPT_PREVIEW_INPUT_INVALID')
    if not isinstance(saved_at, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?\+00:00', saved_at):
        raise ValueError('CONCEPT_PREVIEW_TIMESTAMP_INVALID')
    datetime.fromisoformat(saved_at)
    value = {'revision': expected_revision + 1, 'savedAt': saved_at, 'note': note.strip(), 'document': copy.deepcopy(validated_document)}
    canonical(value)  # reject non-JSON/NaN before any writer action
    return value


def expectation(expected_revision, note, validated_document):
    variants = [envelope(expected_revision, note, validated_document, stamp) for stamp in STAMP_EXAMPLES]
    return {'contractVersion': 'pds-concept-envelope-budget.v1', 'expectedRevision': expected_revision,
            'documentHash': digest(validated_document), 'noteHash': digest(note.strip()),
            'maximumEnvelopeBytes': max(len(canonical(v)) for v in variants)}


def check_writer_expectation(value, proof):
    """Writer invokes after validate() + its own clock, BEFORE first INSERT."""
    expected = expectation(value['revision'] - 1, value['note'], value['document'])
    if proof != expected or len(canonical(value)) > proof['maximumEnvelopeBytes']:
        raise ValueError('CONCEPT_PUBLICATION_PREVIEW_MISMATCH')
    return value


def descriptor(key, raw):
    row = {'logicalResourceId': key, 'kind': 'document', 'mime': 'application/json',
           'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    if len(raw) > 24576:
        row['chunks'] = [{'size': len(raw[i:i+24576]), 'sha256': hashlib.sha256(raw[i:i+24576]).hexdigest()} for i in range(0, len(raw), 24576)]
    return row


def checked_manifest(manifest):
    if (manifest.get('schemaVersion') != 'npc-mirror.v1' or manifest.get('projectId') != 'binari'
            or digest({k: v for k, v in manifest.items() if k != 'contentHash'}) != manifest.get('contentHash')
            or digest(manifest.get('sourceRevisionVector')) != manifest.get('sourceRevision')):
        raise ValueError('CONCEPT_PREVIEW_MANIFEST_INVALID')
    rows = {r['logicalResourceId']: r for r in manifest['resources']}
    if len(rows) != len(manifest['resources']) or 'concept' not in rows or len(rows) > 256:
        raise ValueError('CONCEPT_PREVIEW_RESOURCES_INVALID')
    for r in rows.values():
        if type(r['size']) is not int or r['size'] < 1 or not re.fullmatch('[a-f0-9]{64}', r['sha256']):
            raise ValueError('CONCEPT_PREVIEW_RESOURCE_INVALID')
    if any(k == 'concept.revisions' or k.startswith('concept.revision.') for k in rows):
        raise ValueError('CONCEPT_PREVIEW_HISTORY_UNSUPPORTED')
    return rows


def capability_matches(local, site, policy):
    if digest({k: v for k, v in policy.items() if k != 'policyHash'}) != policy.get('policyHash'):
        raise ValueError('CONCEPT_PREVIEW_POLICY_HASH')
    fields = ('contractVersion', 'enabled', 'policyHash', 'projectId', 'siteProjectId', 'anchorSnapshotId',
              'anchorContentHash', 'historyMode', 'maximumTotalBytes', 'maximumTransitionIncreaseBytes',
              'requiresApprovedProposalTransition')
    wanted = {k: policy[k] for k in fields if k not in ('contractVersion', 'requiresApprovedProposalTransition')}
    wanted.update(contractVersion=policy['schemaVersion'], requiresApprovedProposalTransition=True)
    if any(c != wanted for c in (local, site)) or wanted['enabled'] is not True:
        raise ValueError('CONCEPT_PREVIEW_CAPABILITY_MISMATCH')
    if (wanted['contractVersion'] != VERSION or wanted['projectId'] != 'binari' or wanted['historyMode'] != 'current-only'
            or wanted['maximumTotalBytes'] != MAX_TOTAL or wanted['maximumTransitionIncreaseBytes'] != MAX_DELTA
            or wanted['requiresApprovedProposalTransition'] is not True):
        raise ValueError('CONCEPT_PREVIEW_CAPABILITY_UNSUPPORTED')


def transition_preview(base, validated_document, note, saved_at, *, anchor, policy, local_capability, site_capability):
    """Exact serialization for supplied timestamp; not a replacement for TS approval.

    Caller must validate document with the actual PDS validator, verify basis and
    chosen operation, authenticate capabilities, and refresh under its apply lock.
    """
    capability_matches(local_capability, site_capability, policy)
    rows, anchored = checked_manifest(base), checked_manifest(anchor)
    if (policy['anchorSnapshotId'], policy['anchorContentHash']) != (anchor['snapshotId'], anchor['contentHash']):
        raise ValueError('CONCEPT_PREVIEW_ANCHOR_MISMATCH')
    if (len(rows) != policy['resourceCount'] or base['sourceRevisionVector']['concept'] < policy['minimumConceptRevision']
            or digest(sorted((r for k, r in anchored.items() if k != 'concept'), key=lambda r: r['logicalResourceId'])) != policy['fixedResourceDescriptorsHash']
            or sum(r['size'] for r in anchored.values()) != policy['anchorTotalBytes']):
        raise ValueError('CONCEPT_PREVIEW_POLICY_ANCHOR_INVALID')
    nonconcept = lambda rs: {k: v for k, v in rs.items() if k != 'concept'}
    vector = lambda m: {k: v for k, v in m['sourceRevisionVector'].items() if k not in ('concept', 'concept.document', 'currentContent')}
    if nonconcept(rows) != nonconcept(anchored) or vector(base) != vector(anchor) or vector(anchor) != policy['sourceRevisionVector']:
        raise ValueError('CONCEPT_PREVIEW_UNRELATED_CHANGED')
    value = envelope(base['sourceRevisionVector']['concept'], note, validated_document, saved_at)
    raw = canonical(value)
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError('CONCEPT_PREVIEW_RESOURCE_LIMIT')
    after = copy.deepcopy(base)
    nextrows = {**rows, 'concept': descriptor('concept', raw)}
    after['resources'] = [nextrows[k] for k in sorted(nextrows)]
    after['sourceRevisionVector'].update(concept=value['revision'], **{'concept.document': hashlib.sha256(raw).hexdigest(), 'currentContent': digest({k: r['sha256'] for k, r in sorted(nextrows.items())})})
    after['sourceRevision'] = digest(after['sourceRevisionVector'])
    identity = {k: after[k] for k in ('projectId', 'sourceRevisionVector', 'resources')}
    if 'consistency' in after: identity['consistency'] = after['consistency']
    after['snapshotId'] = 'snapshot.' + digest(identity)
    after['lastSyncedAt'] = saved_at
    after.pop('contentHash')
    after['contentHash'] = digest(after)
    before_total = sum(r['size'] for r in rows.values())
    total = sum(r['size'] for r in nextrows.values())
    if before_total > MAX_TOTAL or total > MAX_TOTAL or total - before_total > MAX_DELTA:
        raise ValueError('CONCEPT_PREVIEW_CAPACITY_EXCEEDED')
    if len(canonical(after)) > 2 * 1024 * 1024:
        raise ValueError('CONCEPT_PREVIEW_MANIFEST_LIMIT')
    return {'manifest': after, 'envelope': value, 'envelopeBytes': len(raw), 'totalBytes': total,
            'increaseBytes': total - before_total, 'resourceCount': len(nextrows), 'publicationConfirmed': False}


def budget_preview(base, document, note, *, validate_document, **bindings):
    """No future-hash claim: proves max exact serialization size for both clocks."""
    validated = validate_document(copy.deepcopy(document))
    if validated != document:
        raise ValueError('CONCEPT_PREVIEW_VALIDATOR_NORMALIZED_DOCUMENT')
    variants = [transition_preview(base, validated, note, stamp, **bindings) for stamp in STAMP_EXAMPLES]
    return {'sourceCommitAllowed': True, 'publicationConfirmed': False, 'futureManifestHashKnown': False,
            'maximumTotalBytes': max(v['totalBytes'] for v in variants),
            'maximumIncreaseBytes': max(v['increaseBytes'] for v in variants),
            'resourceCount': variants[0]['resourceCount'],
            'writerExpectation': expectation(base['sourceRevisionVector']['concept'], note, validated),
            'clockVariantEnvelopeBytes': [v['envelopeBytes'] for v in variants]}
