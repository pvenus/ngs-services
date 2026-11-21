# Request Service: local push-only official registration

Run with Node 22+ (node:sqlite). The CLI binds only 127.0.0.1:8770/mcp.
Set REQUEST_OFFICIAL_INBOX_DIR to the already approved, explicit dedicated
directory used for official-requests.sqlite. It must be mode 0700 and owned
by the service user under an existing real parent; the database is mode 0600.
No default, source registry, polling, actor credential creation, or REST API.

The stateless MCP transport uses JSON-RPC and Streamable HTTP protocol
2025-06-18. It supports initialize, notification, ping, tool discovery and the
business tools request_submit, request_cancel and request_list. Requests require the exact loopback host
and socket; browser Origin, Cookie, Sec-Fetch-Site and forwarded requests are
refused. This is a same-Mac service boundary, not a public or remote endpoint.

request_submit accepts exactly {sourceId:'local',requestType:'npc.workflow',
idempotencyKey,payload}, where payload is either ngs.npc-one-stop.v1 for a new NPC or
ngs.npc-one-stop.replace.v1 for an already known exact NPC in binari. The
replacement has the same fields (existing npc./character. ID need not derive
from slug). NGS is responsible for verifying the existing identity. No
legacy fullRefresh/replacement deletion is performed here. It returns {officialRequestId,status:'registered'}
only after the independent SQLite transaction commits. The idempotency key is
unique per source/project; duplicate NPC IDs/slugs across active new and replacement registrations are
rejected (case insensitive, in the same write transaction). A repeat with the exact key and canonical payload
returns the original receipt; changed input returns 409. The new generic
official_requests table and request.official.* IDs are additive. The previous
official_npc_one_stop schema and its numeric-source rows are not used or
modified. NGS continues to check its own already published NPC identities.

Tool errors use MCP isError with safe {error,status}: REQUEST_SUBMIT_INVALID
or NGS_ONE_STOP_PAYLOAD_INVALID (400), OFFICIAL_REQUEST_IDEMPOTENCY_CONFLICT or
OFFICIAL_REQUEST_NPC_ALREADY_REGISTERED (409), and OFFICIAL_REQUEST_REGISTER_FAILED
(503). No request bodies or credentials are logged.

Registered does not mean a Workflow plan, execution queue, or completed NPC.
There is no Workflow, worker, NGS database, Filex, PDS access or execution.

Test: node --test request-service.test.mjs (disposable SQLite and ephemeral loopback).

request_cancel accepts only {officialRequestId}, scoped to the exact local
official registration. It changes only registered to cancelled, keeps the row
and original payload, and returns {officialRequestId,status:'cancelled'} after
commit. Exact repeated cancellation is harmless; submit with the old same key
returns 409 and cannot resurrect the registration. A fresh submit key can
register the same NPC only when no other registered row owns its ID or slug.

Startup atomically upgrades the exact 13de45f official_requests schema to allow
cancelled and restricts the duplicate-NPC indexes to registered rows. The
previous numeric-source table is untouched. The service will refuse a schema
it does not recognize. Cancellation errors are REQUEST_CANCEL_INVALID (400),
OFFICIAL_REQUEST_NOT_FOUND (404), OFFICIAL_REQUEST_CANCEL_CONFLICT (409), or
OFFICIAL_REQUEST_CANCEL_FAILED (503).

request_list is read-only on the current official_requests ledger for local
OPS. Args are {status?,limit?,cursor?}: omit status to include registered and
cancelled, limit 1–100 (default 50), and pass the previous nextCursor unchanged
with the same status filter. It returns {requests,nextCursor}; the cursor is
null on the last page. Rows contain only officialRequestId, sourceId, projectId,
requestType, schemaVersion, status, registeredAt (Unix ms), targetNpcId and
targetName. Payloads, hashes, submission keys and old numeric-source history
are not returned. Pages order by newest registeredAt then newest insertion;
new registrations after the first page are visible on a fresh list, and
existing statuses reflect the current ledger on each page.

After the CLI starts listening, it sends its own OPS heartbeat (immediately
and then every 60 seconds): POST {"serverName":"rs"} to
http://127.0.0.1:8090/api/service-heartbeat. It accepts HTTP 200 {ok:true};
missing or malformed acknowledgements are nonblocking and do not affect
request registration, listing or cancellation. There are no credentials,
redirects, or configuration values for the heartbeat destination.

The local Planner uses request_workflow_list({limit?,cursor?}) to read only
registered One-Stop rows. It has its own cursor scope, and returns official
request ID, project ID, request type, schema version, exact immutable payload,
payloadSha256, registered status and registeredAt; the service verifies the
stored canonical payload SHA-256 before returning it. Plan consumers should
use a bounded page size such as 10 because this view contains the full payload.
The OPS request_list remains summary-only, with optional filtering across
registered, planned, completed, failed and cancelled.

request_update_status({officialRequestId,status}) permits registered → planned
or planned → completed/failed. An exact same-status retry returns the same
status; invalid transitions return a conflict. Cancellation still acts only
on registered requests (or replays a prior cancellation). Registered and
planned requests both reserve the NPC identity against fresh registrations.
A separate planner owns the actual Workflow contract; this service only stores
the reported state, without planning, dispatching or executing.

Startup atomically upgrades the exact prior ledger checks and indexes to
support workflow states, preserving every existing row, rowid and hash.
The older executable cannot read this newer schema; do not restore an older
database snapshot after real status changes.

Master Concept editing registers `concept.category.workflow` with `operation: update`,
exact existing category ID/title, null group, empty outline, and 1–4000 characters
of requirements. Registration does not read or write canonical content. Fresh
destructive concept `replace` is denied; historical exact replay stays valid.

Fresh add also requires an ordered, exact array of 1–16 distinct trimmed
`requiredSections` labels, each 1–80 code points. Existing recorded add without
this array keeps its historical payload and exact replay.

### Opt-in Space request intake

The existing `request_submit` remains local-only. A launcher supplying the
independently owner-authenticated `readSpaceRequest(queueId)` callback to
`createRequestService` enables exactly one additional loopback MCP tool:
`request_submit_space({queueId})`. That callback must call `space_requests_get`
on the configured owner Site and return the exact immutable object
`{queueId, requestType, idempotencyKey: queueId, payload}`. The MCP caller
cannot supply a payload, origin claim, alternate Site endpoint or key.

RS enforces the same typed request validators for the approved Binari project,
stamps `source_id=space`, and uses the exact queue ID as the source-scoped
idempotency key. It returns only `{officialRequestId,status,registeredAt}`.
The official ID commits to `['request.official.v1','space',projectId,queueId]`.
An exact replay returns the same ID and original time even after state
advances; a different payload for the same key is rejected. Cancel and other
external tool permissions are not widened. Do not activate without a verified
Site adapter and compatible Planner. Native CLI does not configure the reader.
