"""Live filesystem/ledger evidence; host attestation comes from installed reader.
No execution-only legacy partition is promoted to global ownership coverage.
"""
import json
import hashlib
import sqlite3
from pathlib import Path
from .filex_read import private_read, require, digest
from .npc_external_provider import ledger_snapshot
from .npc_preserved_legacy import snapshot

class EvidenceReader:
    def __init__(self, config, host_reader):
        self.config = config
        self.host_reader = host_reader

    def __call__(self, npc, generation, request):
        c = self.config
        host = self.host_reader(npc, generation, request)
        registry = c['registry']
        require(host.get('npcId') == npc and host.get('generationId') == generation
                and host.get('requestId') == request and host.get('legacyResumeBlocked') is True,
                'LIVE_HOST_FENCE_REQUIRED')
        require(host.get('producerRegistryHash') == digest(registry)
                and set(host.get('coveredHosts', [])) == set(registry['hosts'])
                and set(host.get('coveredProducers', [])) == set(registry['producers'])
                and host.get('verified') is True, 'LIVE_HOST_REGISTRY_REQUIRED')
        require(host.get('unresolvedCurrentPaths') == [], 'LIVE_HOST_CURRENT_SCOPE_UNRESOLVED')
        # The coordinator calls us while its exclusive transaction is held.
        # A separate read-only connection observes the committed inventory.
        db = sqlite3.connect(Path(c['ledger']).as_uri()+'?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in db.execute('SELECT * FROM owned')]
            require(not db.execute('SELECT 1 FROM leases WHERE npc=?',(npc,)).fetchone(), 'LIVE_HOST_ACTIVE_LEASE')
            binding = ledger_snapshot(db,npc,generation)
        finally:
            db.close()
        fresh = snapshot(c['freshRoots'])
        ledger = {(r['root'],r['path']):r for r in rows}
        infra = {}
        for item in host.get('preservedInfrastructureFiles', []):
            require(item.get('kind') in ('request-lock','admission-lock','admission-journal','publication-journal','typed-stage-record')
                    and item.get('disposition') == 'retain-infrastructure'
                    and item.get('requestId'), 'INFRA_PRESERVATION_PROOF_INVALID')
            if item['kind'].endswith('lock'):
                require(item.get('sha256') == hashlib.sha256(b'').hexdigest(), 'INFRA_NONEMPTY_LOCK')
            key = (item['rootKey'],item['path'])
            require(key not in infra, 'INFRA_DUPLICATE_PROOF')
            infra[key] = item['sha256']
        for entry in fresh['files']:
            row = ledger.get((entry['root'],entry['path']))
            if row is None:
                require(infra.get((entry['root'],entry['path'])) == entry['sha256'], 'FRESH_FILE_NOT_ACCOUNTED')
            else:
                require(row['sha'] == entry['sha256'], 'FRESH_FILE_CHANGED')
        # Shared output directories are not scanned as if exclusively owned.
        # The host verifier must derive complete exact allocations from requests.
        actual = {(r['root'],r['path'],r['sha']) for r in rows if r['npc']==npc and r['generation']==generation}
        expected = {(r['rootKey'],r['path'],r['sha256']) for r in host.get('currentOwnedFiles', [])}
        require(actual == expected, 'HOST_LEDGER_ALLOCATION_MISMATCH')
        legacy = snapshot(c['preservedLegacyRoots'], preserved_links=c.get('preservedLegacyLinks', ()))
        require(not any(Path(a)==Path(b) or Path(a) in Path(b).parents or Path(b) in Path(a).parents
                        for a in c['freshRoots'].values() for b in c['preservedLegacyRoots'].values()), 'FRESH_LEGACY_OVERLAP')
        return {'npcId':npc,'generationId':generation,'requestId':request,'scope':'current-generation',
                'currentGenerationCoverageComplete':True,'unresolvedCurrentPaths':[],
                'allRegisteredHostsVerified':True,'ledgerSnapshotHash':binding,
                'legacyPreserved':True,'legacySnapshotHash':legacy['snapshotHash'],
                'globalCoverageComplete':False,'legacyResumeBlocked':True,
                'hostEvidenceHash':digest(host),'freshSnapshotHash':fresh['snapshotHash'],
                'preservedInfrastructureCount':len(infra)}
