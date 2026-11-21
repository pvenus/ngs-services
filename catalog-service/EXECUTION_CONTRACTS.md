# Versioned worker execution contracts

`catalog_service/execution_contracts.json` is reviewed, code-versioned configuration, not user-authored knowledge. Its eight contracts preserve video processing requirements, storage/execution boundaries, NPC read-only preparation bindings, and processing-spec validation.

- `GET /v1/catalog/execution-contracts`: list configuration.
- `GET /v1/catalog/execution-contracts/{id}` and `POST /v1/catalog/execution-contracts/query`: read explicit configurations.
- `GET /v1/catalog/execution-context/{actionId}`: returns `executionContracts`, never user knowledge.
- `/v1/catalog/knowledge` and descendants, and PDS `/v1/data/knowledge` and descendants: retired (410).

Service/action/template metadata retain their previous stored reference fields for read compatibility. These references now resolve only against the code registry, with unknown IDs rejected. `template.processing-spec.knowledge` is a read-only alias for `template.processing-spec.contract`; no database data migration or deletion is performed. Registration bootstrap no longer creates knowledge records.

The decision service returns the exact configuration IDs as `contextEntryIds`. Worker orchestration checks those IDs against the catalog response before execution. PDS concept journals remain independent of the retired knowledge domain. Previously persisted workflow sessions are not rewritten by this change.
