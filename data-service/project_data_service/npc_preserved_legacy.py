"""Read-only legacy preservation assessment, NOT a delete authorization.

Unknown ownership cannot become complete by assigning files to a partition.
A current-generation retirement would require a separate explicit API/approval;
this module deliberately emits no executable delete candidates.
"""
import hashlib
import os
import stat
from pathlib import Path
from .filex_read import digest, require


def snapshot(roots, *, preserved_links=()):
    """Inventory explicit preserved directories without following links.

    The resulting hash is an observation, not a durable freeze or owner proof.
    Callers must re-observe before accepting an assessment.
    """
    resolved = {}
    for key, raw in sorted(roots.items()):
        path = Path(raw)
        require(path.is_absolute() and path.resolve() == path and path.is_dir(), 'LEGACY_ROOT_INVALID')
        require(not any(path == p or path in p.parents or p in path.parents for p in resolved.values()), 'LEGACY_ROOT_OVERLAP')
        resolved[key] = path
    policies = {}
    for item in preserved_links:
        require(isinstance(item, dict) and set(item) == {'rootKey', 'path', 'target'}, 'LEGACY_LINK_POLICY_INVALID')
        key, relative, target = item['rootKey'], item['path'], item['target']
        require(key in resolved and isinstance(relative, str) and relative
                and not Path(relative).is_absolute() and all(x not in ('', '.', '..') for x in relative.split('/'))
                and isinstance(target, str) and Path(target).is_absolute(), 'LEGACY_LINK_POLICY_INVALID')
        identity = (key, relative)
        require(identity not in policies, 'LEGACY_LINK_POLICY_DUPLICATE')
        policies[identity] = target
    records = []
    links = []
    observed_links = set()
    def link_identity(s):
        return {name: getattr(s, 'st_' + name) for name in ('dev', 'ino', 'mode', 'uid', 'gid', 'size', 'mtime_ns', 'ctime_ns')}
    def observe_link(path, identity):
        require(identity in policies, 'LEGACY_SYMLINK_UNRESOLVED')
        before = path.lstat()
        require(stat.S_ISLNK(before.st_mode), 'LEGACY_LINK_CHANGED')
        target = os.readlink(path)
        after = path.lstat()
        require(target == policies[identity], 'LEGACY_LINK_TARGET_CHANGED')
        require(link_identity(before) == link_identity(after) and os.readlink(path) == target,
                'LEGACY_LINK_CHANGED')
        return {'root': identity[0], 'path': identity[1], 'kind': 'preserved-symlink',
                'target': target, 'identity': link_identity(after), 'targetFollowed': False}

    for key, root in resolved.items():
        for base, dirs, files in os.walk(root, followlinks=False):
            for name in sorted(dirs + files):
                path = Path(base) / name
                identity = (key, str(path.relative_to(root)))
                if path.is_symlink():
                    links.append(observe_link(path, identity)); observed_links.add(identity)
                else:
                    require(identity not in policies, 'LEGACY_LINK_MISSING_OR_REPLACED')
            for name in sorted(files):
                path = Path(base) / name
                if (key, str(path.relative_to(root))) in observed_links: continue
                with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as f:
                    before = os.fstat(f.fileno())
                    require(stat.S_ISREG(before.st_mode), 'LEGACY_NONREGULAR_UNRESOLVED')
                    h = hashlib.sha256()
                    for block in iter(lambda: f.read(1024 * 1024), b''):
                        h.update(block)
                    after = os.fstat(f.fileno())
                fields = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                require(fields(before) == fields(after) == fields(path.stat()), 'LEGACY_CHANGED_DURING_SCAN')
                records.append({'root': key, 'path': str(path.relative_to(root)), 'size': before.st_size, 'sha256': h.hexdigest()})
    require(observed_links == set(policies), 'LEGACY_LINK_MISSING_OR_REPLACED')
    # Recheck the exact link inode and target after the complete filesystem scan.
    for record in links:
        identity = (record['root'], record['path'])
        require(observe_link(resolved[identity[0]] / identity[1], identity) == record, 'LEGACY_LINK_CHANGED')
    records.sort(key=lambda x: (x['root'], x['path']))
    payload = {'roots': {k: str(p) for k,p in resolved.items()}, 'files': records}
    if policies:
        payload.update(preservedLinks=sorted(links, key=lambda r: (r['root'], r['path'])),
                       preservedLinkPolicy=list(preserved_links))
    return {**payload, 'snapshotHash': digest(payload), 'globalCoverageComplete': False, 'ownership': 'unresolved-preserved'}


def assess(preserved, current_roots, expected_snapshot_hash, backup_reader, npc, generation):
    """Assess separation and independently supplied authoritative backup receipt.

    backup_reader is server supplied; it must verify actual archive bytes, not
    merely return the request body's assertion. This remains read-only planning.
    """
    require(preserved['snapshotHash'] == expected_snapshot_hash, 'LEGACY_SNAPSHOT_CHANGED')
    observed = snapshot(preserved['roots'], preserved_links=preserved.get('preservedLinkPolicy', ()))
    require(observed['snapshotHash'] == expected_snapshot_hash, 'LEGACY_SNAPSHOT_CHANGED')
    old = [Path(p) for p in preserved['roots'].values()]
    for raw in current_roots.values():
        p = Path(raw)
        require(p.is_absolute() and p.resolve() == p, 'CURRENT_ROOT_INVALID')
        require(not any(p == x or p in x.parents or x in p.parents for x in old), 'CURRENT_LEGACY_OVERLAP')
    proof = backup_reader(npc, generation)
    require(proof.get('npcId') == npc and proof.get('generationId') == generation and proof.get('verified') is True and proof.get('backupId') and proof.get('manifestHash'), 'AUTHORITATIVE_BACKUP_REQUIRED')
    return {'schemaVersion': 'npc-preserved-legacy-assessment.v1', 'npcId': npc, 'generationId': generation,
            'preservedSnapshotHash': expected_snapshot_hash, 'preservedFileCount': len(observed['files']),
            'backupId': proof['backupId'], 'backupManifestHash': proof['manifestHash'],
            'scope': 'current-generation-only-proposal', 'globalCoverageComplete': False,
            'fullNpcDeletionAllowed': False, 'executable': False, 'deleteCandidates': [],
            'requires': ['explicit-partial-retirement-contract', 'authoritative-owned-inventory', 'admission-fence', 'backup-scope-and-restore-validation']}
