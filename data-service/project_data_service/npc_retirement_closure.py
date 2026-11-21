"""Pure current-generation restore-closure gate; no deletion or global claim.

Inputs must come from authoritative, version-pinned PDS graph/archive readers,
never a client body. Unsupported reference schemas must set complete=False.
Shared dependencies are copied into the backup for self-contained restoration;
that does NOT grant ownership or permission to delete their original files.
"""
from .filex_read import digest, require


def verify(graph, archive, fence, npc, generation):
    require(graph.get('npcId') == npc and graph.get('generationId') == generation
            and graph.get('complete') is True and graph.get('unsupportedReferences') == []
            and graph.get('sourceVersion'), 'RESTORE_GRAPH_INCOMPLETE')
    require(archive.get('verified') is True and archive.get('backupId')
            and archive.get('sourceVersion') == graph['sourceVersion']
            and archive.get('npcId') == npc and archive.get('generationId') == generation,
            'RESTORE_ARCHIVE_UNVERIFIED')
    require(fence.get('npcId') == npc and fence.get('generationId') == generation
            and fence.get('admissionBlocked') is True and fence.get('activeLeases') == 0
            and fence.get('legacyResumeBlocked') is True, 'RETIREMENT_FENCE_INCOMPLETE')
    nodes = graph.get('nodes', {})
    roots = graph.get('roots', [])
    require(roots and isinstance(nodes, dict), 'RESTORE_GRAPH_EMPTY')
    pending, seen = list(roots), set()
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        require(key in nodes, 'RESTORE_REFERENCE_MISSING')
        node = nodes[key]
        require(node.get('sha256') and node.get('verifiedBytes') is True, 'RESTORE_BYTES_UNVERIFIED')
        require(archive.get('entries', {}).get(key) == node['sha256'], 'RESTORE_ARCHIVE_MISSING_BYTES')
        require(node.get('referenceScanComplete') is True and isinstance(node.get('references'), list), 'RESTORE_REFERENCE_SCAN_INCOMPLETE')
        seen.add(key)
        pending.extend(node['references'])
    # Unreachable entries cannot hide an omitted root/schema family.
    require(seen == set(nodes), 'RESTORE_GRAPH_UNREACHABLE')
    return {'schemaVersion': 'npc-current-generation-restore-closure.v1',
            'npcId': npc, 'generationId': generation, 'sourceVersion': graph['sourceVersion'],
            'backupId': archive['backupId'], 'closureHash': digest(graph),
            'requiredAssetCount': len(seen), 'restoreClosureVerified': True,
            'globalCoverageComplete': False, 'legacyMustBePreserved': True,
            'deletionAuthorized': False}
