"""Read-only projection of authored NPC data, without combat-value conversion."""
from copy import deepcopy
import hashlib
import json
from .profile_model import TRAIT_GROUPS, validate_scores, validate_profile


class CombatProfileError(Exception):
    def __init__(self, code, message, status):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def combat_profile(snapshot, npc_id):
    try:
        document = snapshot['document']
        if document.get('schemaVersion') != 1 or not isinstance(document.get('characters'), list):
            raise ValueError('NPC 문서 구조를 확인하세요.')
        if any(not isinstance(c, dict) for c in document['characters']):
            raise ValueError('NPC 목록 구조를 확인하세요.')
        matches = [c for c in document['characters'] if c.get('id') == npc_id]
        if not matches:
            raise CombatProfileError('npc_not_found', '해당 NPC가 최신 개정에 없습니다.', 404)
        if len(matches) != 1:
            raise ValueError('NPC ID가 중복되었습니다.')
        character = matches[0]
        traits = character.get('traits')
        if not isinstance(traits, dict) or set(traits) != set(TRAIT_GROUPS):
            raise ValueError('성향 대분류 4개가 필요합니다.')
        for group, keys in TRAIT_GROUPS.items():
            validate_scores(traits[group], keys, group)
        profile = validate_profile(character)
        return deepcopy({
            'schemaVersion': 1,
            'sourceRevision': snapshot['revision'],
            'savedAt': snapshot['savedAt'],
            'npcId': npc_id,
            'traits': traits,
            'numericProfile': {
                'version': profile['version'],
                'reviewStatus': profile['reviewStatus'],
                'context': profile['context'],
                'groups': {k: profile['groups'][k] for k in ('motives', 'capabilities', 'state')},
                'relationships': profile['relationships'],
            },
        })
    except CombatProfileError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise CombatProfileError('invalid_combat_profile', str(exc), 500) from exc


def encode_profile(value, prefix="combat-profile-v1"):
    """Stable representation bytes and a resource-specific strong ETag."""
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return body, '"' + prefix + '-' + hashlib.sha256(body).hexdigest() + '"'


def etag_matches(header, etag):
    return any(tag.strip().removeprefix('W/') in ('*', etag) for tag in (header or '').split(','))
