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

The macOS Request Service may enable the approved Space client with
`NGS_SPACE_REQUEST_RELAY_ENABLED=1` and
`NGS_SPACE_REQUEST_CREDENTIAL_FILE=/absolute/private/path.json`. The existing
local owner supplies a regular mode-0600 file outside code whose object is
exactly `{token,platformToken}`. No credential is embedded in source or logged.
The client can only call the fixed approved Site's HTTPS
`/api/space-machine-mcp` machine endpoint, uses the existing platform service
authorization and distinct machine token, and does not follow redirects.
Without explicit enablement, the ordinary local launcher contract is unchanged.


The optional `targetDurationSeconds: 60` is valid only on `audio.generate`
with `durationSeconds: null`. Registration preserves the target in the
official request digest. It is a composition target, not a minimum publication
length; existing audio generation and Filex result formats are unchanged.

Audio generation uses the same one-result workflow for both concept sources.
For a catalogue sound concept, supply its `conceptId` and omit `conceptText`.
For direct input, supply the reserved `conceptId: "direct"` and a nonblank
`conceptText`. There is no Filex master-concept lookup in direct mode. PDS
returns the text from the exact claimed request and binds publication to
its UTF-8 SHA-256; the result records only the existing source identity and
hash, never the direct text.

An instrumental request uses `text: ""` and omits `lyricsRequest`. For
user-supplied lyrics, put that exact text in `text`. To request generated
lyrics, use `text: ""` and supply a nonblank `lyricsRequest`; the audio
worker prepares the lyrics before music generation. Both vocal variants
require `durationSeconds: null`. The optional direct concept and lyrics
request each allow multiline content up to 4,000 Unicode characters and
are trimmed at registration. Nonblank supplied lyrics are retained exactly,
including line breaks and leading and trailing whitespace. This extends
`ngs.audio-generate.v1` without changing stored legacy requests or their hashes.

### 3D to 2D motion preset conversion

`request_submit` accepts `motion-preset.convert-2d` with precisely
`{schemaVersion:'ngs.motion-preset-convert-2d.v1',projectId:'binari',sourcePresetId,variantPresetId,label}`.
The source ID uses the existing motion preset identifier grammar. The result
requires a different lowercase slug (2–64 characters); case-only aliases of
the source are rejected. Its single-line label must be trimmed and at most 120
Unicode characters. Verified Space requests use the existing owner-reader
path and the same ledger rules. Only one registered/planned request can target
the same result ID across Local and Space. Intake neither checks mutable Filex
state nor creates output; PDS independently checks the real source, the
result's absence, and authorized publication.

### Retired pending requests

On a bounded Planner intake page, RS settles only previously registered,
not-yet-planned requests whose trusted ledger identity and immutable canonical bytes match and whose
request type/schema or exact operation is unsupported by this release.
Unsupported fresh submissions do not enter the queue. A recognized, still
supported historical request continues normally, and payload corruption,
unbound records, already-planned/running, completed, failed and cancelled
history are not reclassified. Startup and the OPS list do not perform this settlement.

The atomic `registered → failed` transition adds the single reason
`REQUEST_UNSUPPORTED_RECREATE` in a separate SQLite table. Original payload
bytes, hashes and request IDs do not change. Only these failed rows expose
the optional `failureReason` in the read-only operator list. A replacement is a
new request, registered normally using a new idempotency key. The Planner
continues to refuse unsupported graphs, and it does not set this RS status.

### NPC workflow with published motion selection

The `npc.workflow` request also accepts version
`ngs.npc-one-stop.selection.v1` (create) or
`ngs.npc-one-stop.selection.replace.v1` (replace). Both replace the historical
`includeCombat` and preset map with a required `motionSelection` array of exact `{presetId,posePreset,assetSetSha256}` entries. The submitted
array and its order are immutable. Preset IDs are path-safe, unique ignoring
ASCII case, and cannot collide in their deterministic task-key slots. Pose is
`idle` or `combat`, and the selected published asset-set digest is lowercase
SHA-256. Movement defaults are client suggestions, not additions to a request
that the Planner or workers infer. An empty array for new creation runs preset, planning,
and idle/representative portraits only, including the configured pixel portrait outputs.
Same-ID replacement and animation-only regeneration still require a motion. This route does not enable the legacy
combat-portrait workflow or inspect the mutable preset catalog.

Fresh original-only NPC creation uses `npc.workflow` with
`ngs.npc-one-stop.basic.v1`: exact `schemaVersion`, `projectId`, `character`,
`intent`, `sourceRefs`, and boolean `includeCombat` keys. The NPC ID must be
`npc.<slug>`, where slug is a new lowercase 2–80 character ID. The preset
creation does not read a previous character or recover a backup. Treat a
variation as a fresh operation with its own name and ID, not a replacement.

The task graph registers the new NPC, then creates overview, portrait art,
representative pose plan and portrait, and idle pose plan and portrait.
Combat opt-in adds only the combat plan and original center portrait. This
schema cannot enqueue direction, pixel or motion stages and never deletes a
folder. Previously persisted selected-motion workflows use their original
plan and remain untouched.
