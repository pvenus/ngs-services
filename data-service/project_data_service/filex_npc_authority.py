"""Current-only NPC authority. PDS DB retains receipts, never NPC body history.

The Filex source flag is explicit and fail-closed. Deployment/migration must not
set it until the separately authorised NPC writer contains a verified snapshot.
"""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from .filex_npc_files import encode, decode
from .filex_npc_store import FilexNPCStore, NPCTransport
from .filex_read import digest, private_read, require

CONFIG = Path.home() / "Library/Application Support/NPCGen/pds-filex-npc.json"
MARKER = "_pdsCurrent"

class FilexCurrentNPC:
    def __init__(self, store, external_inventory=None, replacement_reader=None):
        self.store = store
        self.external_inventory = external_inventory
        self.replacement_reader = replacement_reader

    def _read(self):
        snapshot = self.store.snapshot()
        files = copy.deepcopy(snapshot["documents"])
        marker = files["metadata.json"].pop(MARKER, None)
        require(isinstance(marker, dict) and type(marker.get("revision")) is int,
                "NPC_AUTHORITY_NOT_INITIALIZED")
        return snapshot, marker, decode(files)

    def current(self):
        snapshot, marker, document = self._read()
        return {**marker, "document": document, "sourceVersion": snapshot["sourceVersion"],
                "authority": "filex"}

    def history(self):
        value = self.current()
        return [{k: value[k] for k in ("revision", "savedAt", "note")}]

    def revision(self, number):
        value = self.current()
        return value if value["revision"] == number else None

    def save(self, expected, note, document):
        from .npc_ownership import GUARD
        guard = GUARD.get()
        creating = isinstance(guard, dict) and guard.get('operation') == 'create'
        if creating:
            document = copy.deepcopy(document)
            document['generationEpoch'] = guard['requestId']
        request_hash = digest([expected, note, document])
        request_id = guard['requestId'] if creating else "npc.write." + request_hash
        # Durable operation receipt survives later writes, without old body backups.
        with self.store.factory() as transport:
            prior = transport.receipt(request_id)
        if prior is not None:
            return self._saved_result(prior, request_id, request_hash, expected, note, document)
        snapshot, marker, before_document = self._read()
        if expected != marker["revision"]:
            with self.store.factory() as transport:
                prior = transport.receipt(request_id)
            return self._saved_result(prior, request_id, request_hash, expected, note, document) if prior else None
        files = encode(document)
        require(MARKER not in document, "NPC_RESERVED_FIELD_CONFLICT")
        files["metadata.json"][MARKER] = {
            "revision": expected + 1,
            "savedAt": datetime.now(timezone.utc).isoformat(),
            "note": note,
            "operationHash": request_hash,
        }
        hashes = {k: snapshot["hashes"].get(k) for k in set(files) | set(snapshot["documents"])}
        outcome = self.store.apply(request_id, request_hash,
                                  before=snapshot["documents"], after=files, expected_hashes=hashes)
        return self._saved_result(outcome["receipt"], request_id, request_hash, expected, note, document)

    @staticmethod
    def _saved_result(receipt, request_id, request_hash, expected, note, document):
        require(receipt.get("requestId") == request_id and receipt.get("inputHash") == request_hash
                and receipt.get("status") == "committed", "NPC_COMMIT_OUTCOME_UNCERTAIN")
        marker = receipt.get("resultMetadata")
        require(isinstance(marker, dict) and marker.get("revision") == expected + 1
                and marker.get("note") == note and marker.get("operationHash") == request_hash
                and isinstance(marker.get("savedAt"), str), "NPC_RECEIPT_METADATA_MISMATCH")
        files = encode(document)
        files["metadata.json"][MARKER] = marker
        from .filex_npc_files import PREFIX
        require(receipt.get("hashes", {}).get(PREFIX + "metadata.json") == digest(files["metadata.json"]),
                "NPC_RECEIPT_ROOT_MISMATCH")
        require(all(key.startswith(PREFIX) and key[len(PREFIX):] in files
                    and digest(files[key[len(PREFIX):]]) == hash_value
                    for key, hash_value in receipt["hashes"].items()), "NPC_RECEIPT_BODY_MISMATCH")
        # Return this operation's exact result, not a racing writer's current body.
        return {**marker, "document": copy.deepcopy(document), "authority": "filex"}

    def asset_get(self, asset_id):
        with self.store.factory() as transport:
            return transport.asset_get(asset_id)

    def asset_put(self, asset_id, data):
        with self.store.factory() as transport:
            return transport.asset_put(asset_id, data)

    def delete_character(self, request_id, input_hash, character_id, expected):
        with self.store.factory() as transport:
            prior = transport.receipt(request_id)
        if prior is not None:
            require(prior.get('inputHash') == input_hash and prior.get('deletedCharacter') == character_id,
                    'NPC_DELETE_REQUEST_REUSED')
            require(prior.get('status') == 'committed' and prior.get('backupVerified') is True,
                    'NPC_DELETE_OUTCOME_UNCERTAIN')
            # Replay reads the immutable receipt only; scratch is never inspected or cleaned.
            return {**prior['resultMetadata'], 'deletionReceipt': prior}
        snapshot, marker, document = self._read()
        if expected != marker['revision']:
            return None
        metadata = copy.deepcopy(snapshot['documents']['metadata.json'])
        metadata['characterOrder'] = [x for x in metadata['characterOrder'] if x != character_id]
        metadata['generationEpoch'] = request_id
        result_document = copy.deepcopy(document)
        result_document['characters'] = [x for x in result_document['characters'] if x['id'] != character_id]
        result_document['generationEpoch'] = request_id
        metadata[MARKER] = {'revision': expected + 1, 'savedAt': datetime.now(timezone.utc).isoformat(),
                            'note': 'NPC canonical owner-generation deletion', 'operationHash': input_hash,
                            'documentHash': digest(result_document)}
        generation_id = None
        with self.store.factory() as transport:
            if callable(getattr(transport, 'delete_prepared', None)):
                generation = transport.get_generation(character_id)
                require(generation.get('npcId') == character_id and generation.get('state') in ('active', 'delete-staged', 'deleting')
                        and type(generation.get('activeLeases')) is int and generation['activeLeases'] >= 0, 'NPC_DELETE_GENERATION_NOT_IDLE')
                generation_id = generation['generationId']
                replacement = None
                if generation['activeLeases']:
                    require(generation['state'] == 'active' and callable(self.replacement_reader), 'NPC_DELETE_GENERATION_NOT_IDLE')
                    replacement = self.replacement_reader(character_id,generation_id,request_id,snapshot['sourceVersion'])
                    require(isinstance(replacement,dict), 'NPC_DELETE_REPLACEMENT_EVIDENCE_REQUIRED')
                kwargs = {'replacement_evidence':replacement} if replacement is not None else {}
                staged = transport.stage_delete(character_id, generation_id, request_id, snapshot['sourceVersion'], **kwargs)
                require(staged.get('npcId') == character_id and staged.get('generationId') == generation_id
                        and staged.get('requestId') == request_id and staged.get('sourceVersion') == snapshot['sourceVersion'],
                        'NPC_DELETE_STAGED_BINDING')
                retired = staged.get('replacementRetirement')
                if replacement is not None:
                    require(isinstance(retired,dict) and retired.get('proofHash')==replacement.get('proofHash'), 'NPC_DELETE_REPLACEMENT_FENCE_MISSING')
                transport.prepare_canonical_delete(character_id, generation_id, request_id)
                receipt = transport.delete_prepared(request_id, input_hash, character_id,
                                                    snapshot['sourceVersion'], metadata, generation_id)
                require(receipt.get('backupId') == staged['backupId']
                        and receipt.get('backupManifestHash') == staged['backupManifestHash']
                        and receipt.get('replacementRetirement') == retired,
                        'NPC_DELETE_STAGED_ARCHIVE_CHANGED')
                require(receipt.get('generationId') == generation_id
                        and receipt.get('deletionScope') == 'canonical-owner-generation.v1'
                        and receipt.get('externalDataDisposition') == 'preserved-uninspected'
                        and 'externalOwnerManifestHash' not in receipt,
                        'NPC_EXTERNAL_RECEIPT_BINDING')
            else:
                receipt = transport.delete_character(request_id, input_hash, character_id,
                                                     snapshot['sourceVersion'], metadata)
        require(receipt.get('status') == 'committed' and receipt.get('backupVerified') is True,
                'NPC_DELETE_OUTCOME_UNCERTAIN')
        return {**receipt['resultMetadata'], 'deletionReceipt': receipt}

    def restore_character(self, request_id, input_hash, backup_id, expected):
        from .npc_ownership import GUARD
        guard = GUARD.get()
        require(isinstance(guard, dict) and guard.get('operation') == 'restore'
                and guard.get('requestId') == request_id, 'NPC_RESTORE_GUARD_REQUIRED')
        with self.store.factory() as transport:
            prior = transport.receipt(request_id)
            if prior is not None:
                require(prior.get('inputHash') == input_hash and prior.get('backupId') == backup_id
                        and prior.get('generationId') == guard['generationId']
                        and prior.get('status') == 'committed', 'NPC_RESTORE_REPLAY_MISMATCH')
                return {'restorationReceipt': prior}
            preview = transport.backup_preview(backup_id)
        snapshot, marker, document = self._read()
        require(expected == marker['revision'] and snapshot['sourceVersion'] == guard['expectedSourceVersion'],
                'NPC_RESTORE_SOURCE_CHANGED')
        owner = preview['characterId']
        require(owner == guard['npcId'] and owner not in snapshot['documents']['metadata.json']['characterOrder'],
                'NPC_RESTORE_OWNER_CONFLICT')
        files = copy.deepcopy(snapshot['documents'])
        require(all(k.startswith(owner + '/') and not any(x.startswith('.') for x in k.split('/'))
                    for k in preview['documents']), 'NPC_RESTORE_PREVIEW_SCOPE')
        files.update(preview['documents'])
        metadata = files['metadata.json']
        metadata['characterOrder'] = [*metadata['characterOrder'], owner]
        metadata['generationEpoch'] = request_id
        metadata.pop(MARKER, None)
        restored_document = decode(files)
        metadata[MARKER] = {'revision': expected + 1, 'savedAt': datetime.now(timezone.utc).isoformat(),
                            'note': 'NPC verified whole-character restoration', 'operationHash': input_hash,
                            'documentHash': digest(restored_document)}
        with self.store.factory() as transport:
            receipt = transport.restore_character(request_id, input_hash, backup_id,
                                                  snapshot['sourceVersion'], metadata)
        require(receipt.get('status') == 'committed' and receipt.get('generationId') == guard['generationId']
                and receipt.get('restoredCharacter') == owner and receipt.get('backupVerified') is True,
                'NPC_RESTORE_OUTCOME_UNCERTAIN')
        return {'restorationReceipt': receipt}

    @classmethod
    def configured(cls, path=CONFIG, storage_root=None, external_inventory=None, external_host_reader=None):
        cutover = Path(storage_root) / 'domains/npc-filex-authority.json' if storage_root is not None else None
        activated = cutover is not None and cutover.exists()
        if not path.exists():
            require(not activated, "NPC_FILEX_CONFIG_MISSING_AFTER_CUTOVER")
            return None
        config = json.loads(private_read(path))
        require(config.get("authority") == "filex", "NPC_AUTHORITY_CONFIG_INVALID")
        require(isinstance(config.get("storageRoot"), str), "NPC_STORAGE_ROOT_BINDING_REQUIRED")
        if storage_root is not None and Path(config["storageRoot"]).resolve() != Path(storage_root).resolve():
            require(not activated, "NPC_FILEX_CONFIG_ROOT_MISMATCH_AFTER_CUTOVER")
            return None  # Independent test/storage instances must never acquire live authority.
        from .npc_ownership import make_transport
        replacement_reader = None
        if config.get('externalInventoryConfigFile'):
            provider = json.loads(private_read(Path(config['externalInventoryConfigFile'])))
            if provider.get('schemaVersion') == 'npc-external-delete-provider.v1' and provider.get('enabled') is True and provider.get('scope') == 'current-generation' and provider.get('hostEvidenceSocket'):
                from .npc_retirement_host_client import client
                replacement_reader = client(provider['hostEvidenceSocket'], replacement=True)
        if external_inventory is None and config.get('externalInventoryConfigFile'):
            from .npc_external_provider import configured as inventory_configured
            external_inventory = inventory_configured(config['externalInventoryConfigFile'], lambda: make_transport(config), host_reader=external_host_reader)
        return cls(FilexNPCStore(lambda: make_transport(config)), external_inventory=external_inventory, replacement_reader=replacement_reader)


def migration_files(envelope):
    """Pure, lossless cutover plan, never an implicit DB fallback or write."""
    files = encode(envelope["document"])
    require(MARKER not in envelope["document"], "NPC_RESERVED_FIELD_CONFLICT")
    files["metadata.json"][MARKER] = {k: envelope[k] for k in ("revision", "savedAt", "note")}
    return files
