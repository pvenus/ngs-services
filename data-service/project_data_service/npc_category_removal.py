"""Explicit current-file category removal. Content stays recoverable in place.

This is not a historical backup, file unlink, NPC deletion, or cascade. Only a
planning category is marked deleted and removed from visible preset membership.
"""
import copy
from datetime import datetime, timezone
from .filex_read import digest, require
from .filex_npc_files import encode

def plan(document, character_id, category_id, request_id):
    result = copy.deepcopy(document)
    matches = [c for c in result['characters'] if c.get('id') == character_id]
    require(len(matches) == 1, 'NPC_CATEGORY_DELETE_CHARACTER_REQUIRED')
    character = matches[0]
    matches = [c for c in character.get('planningCategories', []) if c.get('id') == category_id]
    require(len(matches) == 1, 'NPC_CATEGORY_DELETE_TARGET_REQUIRED')
    target = matches[0]
    require(target.get('lifecycleState') != 'deleted', 'NPC_CATEGORY_ALREADY_DELETED')
    # Preserve body and all nested fields. No child data is recursively removed.
    target['lifecycleState'] = 'deleted'
    target['removalRequestId'] = request_id
    preset = character.get('planningPreset')
    if isinstance(preset, dict) and isinstance(preset.get('visibleCategoryIds'), list):
        preset['visibleCategoryIds'] = [c for c in preset['visibleCategoryIds'] if c != category_id]
    return result

def remove(authority, request_id, input_hash, character_id, category_id, expected):
    require(type(expected) is int and expected > 0, 'NPC_CATEGORY_DELETE_REVISION_REQUIRED')
    binding = digest({'requestId':request_id,'characterId':character_id,'categoryId':category_id,'expectedRevision':expected})
    require(input_hash == binding, 'NPC_CATEGORY_DELETE_INPUT_HASH_MISMATCH')
    with authority.store.factory() as transport:
        prior = transport.receipt(request_id)
    if prior is not None:
        require(prior.get('inputHash') == input_hash and prior.get('status') == 'committed', 'NPC_CATEGORY_DELETE_REQUEST_REUSED')
        return {**prior['resultMetadata'], 'removalReceipt':prior, 'removalMode':'logical', 'contentPreserved':True}
    snapshot, marker, document = authority._read()
    if marker['revision'] != expected:
        return None
    result = plan(document, character_id, category_id, request_id)
    files = encode(result)
    files['metadata.json']['_pdsCurrent'] = {'revision':expected+1,'savedAt':datetime.now(timezone.utc).isoformat(),
        'note':'NPC planning category logical removal','operationHash':input_hash}
    outcome = authority.store.apply(request_id,input_hash,before=snapshot['documents'],after=files,expected_hashes=snapshot['hashes'])
    return {**outcome['receipt']['resultMetadata'], 'removalReceipt':outcome['receipt'], 'removalMode':'logical', 'contentPreserved':True}
