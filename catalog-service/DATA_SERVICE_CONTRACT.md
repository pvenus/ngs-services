# Project Data Service persistence contract

This service never opens SQLite. Production supplies a `CatalogRepository` adapter backed by the shared Project Data Service.

## Required collections

- `services`
- `actions`
- `knowledge`
- `templates`

Every resource is an object with a stable string `id` and `status`. The Data Service owns SQLite migrations, transactions, revisions, audit records, and idempotency.

## Required operations and exact envelopes

```http
GET /v1/data/{collection}/{id}
```

Successful response (`200`) is a Data Service record envelope:

```json
{
  "collection": "services",
  "id": "video-alpha",
  "revision": 3,
  "status": "active",
  "resource": {"id": "video-alpha", "status": "active"},
  "createdAt": "2026-09-28T00:00:00Z",
  "updatedAt": "2026-09-28T00:00:00Z"
}
```

Missing resource (`404`):

```json
{"error": "not_found"}
```

The repository maps this `404` to `None` because absence is part of its read contract.

```http
GET /v1/data/{collection}?status=active
```

Successful response (`200`):

```json
{
  "items": [{
    "collection": "services",
    "id": "video-alpha",
    "revision": 3,
    "status": "active",
    "resource": {"id": "video-alpha", "status": "active"},
    "createdAt": "2026-09-28T00:00:00Z",
    "updatedAt": "2026-09-28T00:00:00Z"
  }]
}
```

`status` is optional. The adapter unwraps each `items[].resource` before returning the list to the catalog domain.

```http
PUT /v1/data/{collection}/{id}
Content-Type: application/json
```

Exact request body:

```json
{
  "requestId": "stable-idempotency-key",
  "expectedRevision": 3,
  "reason": "human-readable change reason",
  "resource": {}
}
```

For creation, `expectedRevision` is `0`. For replacement, it is the integer `revision` returned by the immediately preceding item GET. `requestId` is unique per logical write and is reused only when retrying that same write.

Successful response (`200`) is the updated record envelope:

```json
{
  "collection": "services",
  "id": "video-alpha",
  "revision": 4,
  "status": "active",
  "resource": {"id": "video-alpha", "status": "active"},
  "createdAt": "2026-09-28T00:00:00Z",
  "updatedAt": "2026-09-28T00:01:00Z"
}
```

Expected error responses:

- `404 {"error":"not_found"}`: item GET did not find the resource
- `409 {"error":"revision_conflict"}`: revision conflict or mismatched reuse of a request ID
- `422 {"error":"invalid_resource"}`: schema/domain validation failure

The adapter keeps each envelope's top-level `revision` internally for optimistic concurrency, while exposing only `resource` to the catalog domain. It turns write-time `409` and `422` responses into distinct domain repository exceptions. Other non-2xx responses and malformed success envelopes are transport/contract failures.

## Adapter constraints

`HttpCatalogRepository` permits only the four collections above, accepts only HTTP(S) base URLs, and rejects resource IDs containing path separators or traversal segments. It sends no SQL, table name, SQLite path, or storage path.

## Domain/storage naming boundary

The B domain uses Python `snake_case`; the A Data Service validates stored resource fields in `camelCase`. `HttpCatalogRepository` is the anti-corruption boundary and performs collection-specific conversion in both directions. Nested user-defined data such as `content` and `submissionSchema` is preserved rather than recursively renamed.

- services: `fixed_knowledge_ids` ↔ `fixedKnowledgeIds`
- actions: `service_id`, `prerequisite_get`, `prerequisite_step_output`, `required_inputs`, `output_type`, `knowledge_ids`, `template_id` ↔ their camelCase forms
- templates: `artifact_type`, `required_context_ids`, `required_inputs`, `compose_instruction`, `user_question_conditions`, `prerequisite_get`, `execution_tool`, `forbidden_actions`, `submission_schema` ↔ their camelCase forms
- knowledge: the storage-only `entryType` is written as `structured-guidance`; it is removed when hydrating `KnowledgeEntry` domain data because the current domain model has no `entry_type` field

Therefore callers of `CatalogRepository` never need to know the Data Service naming convention, while every resource sent to A satisfies its camelCase schema.

The adapter must not expose SQL, table names, filesystem paths, or SQLite connections to this service. Batch transaction support will be required before catalog bootstrap is enabled in production.
