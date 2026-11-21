"""Producer-side hooks for server-owned scratch, explicit persisted PDS guard.
Host adapters must call register_output for every file before releasing the global
lease. No accepting paths/roots from user request bodies; no discovery by prefix.
"""
from .filex_read import require

class ScratchProducer:
    def __init__(self, coordinator, transport_factory, guard):
        require(isinstance(guard,dict) and all(guard.get(k) for k in ('npcId','generationId','requestId','leaseToken')),
                'NPC_PRODUCER_GUARD_REQUIRED')
        self.coordinator=coordinator
        self.factory=transport_factory
        self.guard=dict(guard)
    def start(self):
        g=self.guard
        # Guard is the original persisted admission, never acquired during publish.
        self.coordinator.activate(g['npcId'],g['generationId'])
        self.coordinator.admit(g['npcId'],g['generationId'],g['requestId'])
    def register_output(self,root_key,relative_path,kind):
        g=self.guard
        return self.coordinator.register(g['npcId'],g['generationId'],g['requestId'],root_key,relative_path,kind)
    def publish(self,callback):
        g=self.guard
        return self.coordinator.publication_gate(g['npcId'],g['generationId'],g['requestId'],callback)
    def finish_published(self,publication_request_id,expected_input_hash):
        g=self.guard
        with self.factory() as transport:
            require(all(transport._guard().get(k)==g[k] for k in ('npcId','generationId','requestId','leaseToken')),'NPC_PRODUCER_GUARD_CHANGED')
            receipt=transport.receipt(publication_request_id)
            require(isinstance(receipt,dict) and receipt.get('status')=='committed'
                    and receipt.get('inputHash')==expected_input_hash
                    and receipt.get('generationId')==g['generationId']
                    and receipt.get('parentRequestId')==g['requestId'],
                    'NPC_PRODUCER_PUBLICATION_UNVERIFIED')
            # Durable PDS receipt permits retry after either lease release.
            # Global lease stays held until local ledger registration is complete.
            self.coordinator.finish(g['npcId'],g['generationId'],g['requestId'])
            return transport.finish()
