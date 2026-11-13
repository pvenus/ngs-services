"""Lossless latest-character envelope for manual client synchronization."""
from copy import deepcopy


class NpcDetailError(Exception):
    def __init__(self, code, message, status):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def npc_detail(snapshot, npc_id, validate_document):
    try:
        document = snapshot['document']
        if not isinstance(document, dict) or not isinstance(document.get('characters'), list):
            raise ValueError('NPC 원본 목록을 확인하세요.')
        if any(not isinstance(c, dict) or not isinstance(c.get('id'), str) for c in document['characters']):
            raise ValueError('NPC 원본 ID를 확인하세요.')
        matches = [c for c in document['characters'] if c['id'] == npc_id]
        if not matches:
            raise NpcDetailError('npc_not_found', '해당 NPC가 최신 개정에 없습니다.', 404)
        if len(matches) != 1:
            raise ValueError('NPC ID가 중복되었습니다.')
        # Validate the requested character, retaining all original/extension fields.
        selected = deepcopy(matches[0])
        validate_document({**document, 'characters': [selected]})
        return {'schemaVersion': 1, 'sourceRevision': snapshot['revision'],
                'savedAt': snapshot['savedAt'], 'npcId': npc_id, 'data': selected}
    except NpcDetailError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise NpcDetailError('invalid_npc_document', str(exc), 500) from exc
