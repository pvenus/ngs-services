"""Isolated proposal contract. No intake, provider, service, source-write or publish port.

Existing scene and portrait request types retain their original semantics.
This module prepares a distinct typed payload; it cannot execute that payload.
"""
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def episode(sources, target):
    doc = sources['story']['document']
    lines = [doc] + doc.get('storylines', [])
    found = [e for line in lines if line.get('storyId') == target['actId']
             for c in line.get('chapters', []) if c['id'] == target['chapterId']
             for e in c.get('episodes', []) if e['id'] == target['episodeId']]
    if len(found) != 1:
        raise ValueError('EPISODE_NOT_UNIQUE')
    return found[0]


def validate(plan, sources):
    if set(plan) != {'schemaVersion', 'target', 'sourceBindings', 'requests', 'maximumImageCalls'}:
        raise ValueError('PLAN_SCHEMA')
    if plan['schemaVersion'] != 'npc-episode-visual-plan.proposal.v1':
        raise ValueError('PLAN_SCHEMA')
    if plan['sourceBindings'] != {k: digest(v) for k, v in sources.items()}:
        raise ValueError('SOURCE_CHANGED_REVIEW_REQUIRED')
    target = plan['target']
    if set(target) != {'projectId', 'actId', 'chapterId', 'episodeId'} or target['projectId'] != 'binari':
        raise ValueError('TARGET_SCHEMA')
    ep = episode(sources, target)
    nodes = {n['id']: n for n in ep['playFlow']['nodes']}
    npcs = {n['id']: n for n in sources['npcs']['document']['characters']}
    keys, count = set(), 0
    for request in plan['requests']:
        if set(request) != {'key', 'kind', 'peoplePolicy', 'identity', 'slots', 'setActive', 'applyToStory'}:
            raise ValueError('REQUEST_SCHEMA')
        if request['key'] in keys:
            raise ValueError('DUPLICATE_REQUEST')
        keys.add(request['key'])
        if request['setActive'] is not False or request['applyToStory'] is not False:
            raise ValueError('DRAFT_CANNOT_CHANGE_CANONICAL_BINDING')
        kind = request['kind']
        if kind not in {'background', 'event', 'portrait-variant'}:
            raise ValueError('VISUAL_ROLE_REQUIRED')
        if request['peoplePolicy'] not in {'excluded', 'optional', 'required'}:
            raise ValueError('PEOPLE_POLICY_REQUIRED')
        if kind == 'background' and request['peoplePolicy'] != 'excluded':
            raise ValueError('BACKGROUND_IS_ENVIRONMENT_LAYER')
        identity = request['identity']
        if kind == 'portrait-variant':
            if not isinstance(identity, dict) or set(identity) != {'npcId', 'mainPortraitId'}:
                raise ValueError('IDENTITY_REQUIRED')
            npc = npcs.get(identity['npcId'])
            if not npc or not npc.get('activePortraitId') or npc['activePortraitId'] != identity['mainPortraitId']:
                raise ValueError('CURRENT_MAIN_IDENTITY_REQUIRED')
            if not any(p['assetId'] == identity['mainPortraitId'] for p in npc.get('portraits', [])):
                raise ValueError('MAIN_ASSET_NOT_IN_NPC')
            if request['peoplePolicy'] != 'required':
                raise ValueError('PORTRAIT_ONE_PERSON_REQUIRED')
        elif identity is not None:
            raise ValueError('IDENTITY_NOT_ENVIRONMENT_FIELD')
        slots = request['slots']
        if len(slots) != 3 or [s.get('index') for s in slots] != [0, 1, 2]:
            raise ValueError('THREE_DISTINCT_CLAIMS_REQUIRED')
        variants = set()
        for slot in slots:
            if set(slot) != {'index', 'variantKey', 'emotion', 'pose', 'anchors', 'intent'}:
                raise ValueError('SLOT_SCHEMA')
            if not slot['anchors'] or not slot['intent'].strip():
                raise ValueError('VISIBLE_TEXT_EVIDENCE_REQUIRED')
            for anchor in slot['anchors']:
                if set(anchor) != {'nodeId', 'quote'} or anchor['nodeId'] not in nodes or not anchor['quote'] or anchor['quote'] not in nodes[anchor['nodeId']]['body']:
                    raise ValueError('VISIBLE_TEXT_ANCHOR_INVALID')
            if kind == 'portrait-variant':
                if not slot['variantKey'] or slot['variantKey'] in variants or not slot['emotion'] or not slot['pose']:
                    raise ValueError('DISTINCT_REUSABLE_VARIANT_REQUIRED')
                variants.add(slot['variantKey'])
            elif any(slot[k] is not None for k in ('variantKey', 'emotion', 'pose')):
                raise ValueError('ENVIRONMENT_IS_NOT_PORTRAIT_VARIANT')
            count += 1
    if count != plan['maximumImageCalls'] or count > 6:
        raise ValueError('FIRST_BUNDLE_BUDGET_EXCEEDED')
    return {'planHash': digest(plan), 'requestCount': len(keys), 'maximumImageCalls': count,
            'canonicalWrites': 0, 'providerCalls': 0, 'executable': False,
            'reason': 'TYPED_INTAKE_WORKER_AND_UI_ADAPTERS_NOT_INSTALLED'}
