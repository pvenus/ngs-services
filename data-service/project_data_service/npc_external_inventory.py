"""Server-injected deletion inventory. Never constructed from HTTP body fields.
The inventory provider must aggregate ALL registered worker hosts before asserting
coverage. Missing host/legacy ownership keeps deletion disabled rather than
silently treating an empty local ledger as complete.
"""
from .filex_read import require

class TrustedExternalInventory:
    def __init__(self, coordinator, coverage):
        self.coordinator = coordinator
        self.coverage = coverage

    def prepare(self, npc_id, generation_id, request_id, staged=None):
        proof = self.coverage(npc_id, generation_id)
        require(isinstance(proof, dict) and proof.get('complete') is True
                and proof.get('npcId') == npc_id and proof.get('generationId') == generation_id
                and proof.get('producerRegistryHash') and proof.get('unresolvedPaths') == [],
                'NPC_EXTERNAL_INVENTORY_INCOMPLETE')
        return self.coordinator.prepare_delete(npc_id, generation_id, request_id)

    def cleanup(self, npc_id, generation_id, request_id):
        # Coordinator obtains committed receipt via its authenticated PDS adapter;
        # the caller's deletion response is never cleanup authority.
        return self.coordinator.cleanup_deleted(npc_id, generation_id, request_id)

class AuthenticatedInventoryPDS:
    """Transport factory uses the existing private PDS→Filex credentials."""
    def __init__(self, factory): self.factory = factory
    def get_generation(self, npc_id):
        with self.factory() as transport:
            state = transport.get_generation(npc_id)
        return {key: state[key] for key in ('npcId', 'generationId', 'state')}
    def get_deletion_receipt(self, request_id):
        with self.factory() as transport:
            return transport.get_deletion_receipt(request_id)

    def current_equivalent(self, npc_id, generation_id, parent_id, sha256):
        with self.factory() as transport:
            return transport.current_equivalent(npc_id, generation_id, parent_id, sha256)

    def reserve_equivalent(self,npc,generation,parent,sha,reservation):
        with self.factory() as transport:
            return transport.reserve_equivalent(npc,generation,parent,sha,reservation)
    def release_equivalent(self,npc,generation,parent,reservation,token):
        with self.factory() as transport:
            return transport.release_equivalent(npc,generation,parent,reservation,token)
