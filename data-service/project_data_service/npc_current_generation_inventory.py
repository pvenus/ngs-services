"""Server-only current-generation retirement inventory. Not globally complete.

No config fallback: authoritative generation/closure/legacy proof readers must
be injected. Existing whole-history deletion provider remains unchanged.
"""
import copy
from .filex_read import require, digest
from .npc_retirement_closure import verify as verify_closure
from .npc_external_inventory import TrustedExternalInventory
from .npc_external_provider import ledger_snapshot

class CurrentGenerationInventory(TrustedExternalInventory):
    def __init__(self, coordinator, evidence_reader):
        self.coordinator = coordinator
        self.evidence_reader = evidence_reader

    def prepare(self, npc, generation, request, staged=None):
        require(isinstance(staged, dict), 'STAGED_RETIREMENT_REQUIRED')
        def verify(db):
            evidence = copy.deepcopy(self.evidence_reader(npc, generation, request))
            require(evidence.get('npcId') == npc and evidence.get('generationId') == generation
                    and evidence.get('requestId') == request, 'RETIREMENT_EVIDENCE_BINDING')
            require(evidence.get('scope') == 'current-generation'
                    and evidence.get('currentGenerationCoverageComplete') is True
                    and evidence.get('unresolvedCurrentPaths') == []
                    and evidence.get('allRegisteredHostsVerified') is True,
                    'CURRENT_GENERATION_INCOMPLETE')
            require(evidence.get('ledgerSnapshotHash') == ledger_snapshot(db,npc,generation), 'RETIREMENT_LEDGER_CHANGED')
            require(evidence.get('legacyPreserved') is True
                    and evidence.get('legacySnapshotHash')
                    and evidence.get('globalCoverageComplete') is False,
                    'LEGACY_PRESERVATION_REQUIRED')
            if staged is not None:
                require(staged.get('npcId') == npc and staged.get('generationId') == generation
                        and staged.get('requestId') == request, 'STAGED_RETIREMENT_BINDING')
                fence = dict(staged.get('fence', {}))
                # The writer fences canonical publication; only the host evidence
                # reader can prove historical worker resume is blocked as well.
                fence['legacyResumeBlocked'] = evidence.get('legacyResumeBlocked') is True
                closure = verify_closure(staged.get('graph', {}), staged.get('archive', {}), fence, npc, generation)
                evidence['restoreClosure'] = closure
                evidence['stagedSnapshotHash'] = digest(staged)
            closure = evidence.get('restoreClosure', {})
            require(closure.get('restoreClosureVerified') is True
                    and closure.get('npcId') == npc and closure.get('generationId') == generation
                    and closure.get('backupId') and closure.get('closureHash'), 'RESTORE_CLOSURE_REQUIRED')
            require(evidence.get('legacyResumeBlocked') is True, 'LEGACY_RESUME_NOT_FENCED')
            # Migrated NPCs may have no scratch ledger generation until their
            # first worker runs. Only verified staged canonical authority plus
            # complete live host inventory may seed an empty local generation.
            prior = db.execute('SELECT generation FROM npc_generation WHERE npc=?',(npc,)).fetchone()
            if prior is None:
                require(not db.execute('SELECT 1 FROM owned WHERE npc=?',(npc,)).fetchone(), 'UNBOUND_EXISTING_OWNERSHIP')
                db.execute('INSERT INTO npc_generation VALUES(?,?,?,NULL,NULL,NULL)',(npc,generation,'active'))
            else:
                require(prior['generation'] == generation, 'RETIREMENT_OLD_GENERATION')
            # This complete evidence is included inside the durable backup owner
            # manifest, which in turn is hash-bound by the writer deletion receipt.
            return evidence
        return self.coordinator.prepare_delete(npc,generation,request,coverage_guard=verify)

    def cleanup(self, npc, generation, request):
        result = super().cleanup(npc,generation,request)
        return {**result, 'scope':'current-generation', 'globalCoverageComplete':False,
                'legacyPreserved':True, 'wholeHistoryErased':False}
