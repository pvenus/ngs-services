# Workflow-only NPC preset (inactive candidate)

`POST /v1/npcs/ownership/workflow-preset` accepts exact `{binding,artifact,claim}` only
on the existing local PDS boundary. The binding fields are `officialRequestId`,
`taskId`, `projectId`, `npcId`, `payloadSha256`, `artifactSha256`,
`expectedSourceVersion`, `expectedRevision`, and nullable `predecessorReceiptRef`.
The injected verified authorization must return that exact binding independently.
PDS checks the frozen empty preset, translates supported Act 1/chapter values
to the existing NPC schema, and forwards exact new-owner files plus roster
metadata through its existing authenticated Filex NPC writer transport.

The writer's default-off `POST /v1/workflow/preset` uses its existing lock and
recoverable journal. It checks source/revision and actual target absence under
the lock and commits owner documents, empty asset manifest, roster metadata,
new active generation and receipt as one journaled operation. Exact replay
returns only the original committed receipt for the still-current generation.
It never performs a worker admit or calls prepare-create; regular create routes
are untouched. Existing tombstone/history fields are carried forward. This does
not authorize direct filesystem readers or writers.

Workflow dependency readiness belongs exclusively to the claim query. PDS and
Filex do not query status of the preceding workflow task. If supplied, the
predecessor reference is compared only with Filex's stored deletion receipt and
old generation as an optional storage binding. A legitimate absent target with
no prior state, or with a retired prior generation and null reference, is
accepted. A present target, active old state, wrong optional reference, stale
CAS or unverified authorization rejects. The public adapter receipt uses
`workflow.preset-publication-receipt.v1`; verification is only asserted after
checking the exact committed Filex binding and revision.

No live composition, credential, configuration or alternate auth is installed.
Before deployment both PDS and writer require explicit trusted registration.
The production workflow authorizer must bind the selected task, immutable exact
artifact and CAS, and keep authorization valid through commit (or serialize
claim cancellation with commit). A fresh workflow-state read at ingress alone
is not lifecycle authority. Tests use a synthetic scoped verifier, existing
bearer transport in a temporary loopback fixture, and temporary Filex storage.

## Private planner startup composition

With the existing delete planner DB device/inode and path pins present, opt in
only the data process using `PDS_WORKFLOW_PRESET=1`. Keep the deletion route
enabled as before. The existing NPC writer alone adds
`WRITER_WORKFLOW_EXACT_NPC_PRESET=1` alongside its deletion opt-in. The same
Filex auth/bind/mount/UID are unchanged. GET on the preset route validates the
private planner and actual writer capability without claiming or publishing.

Production POST is exact `{claim,artifact,predecessorReceiptRef}`; the claim
carries officialRequestId, taskId, projectId, npcId, payloadSha256 and the
recorded claimStamp. No caller CAS or worker lifecycle guard is used. The
read-only planner transaction validates the latest exact running document
task and planned run, recomputes the canonical payload digest and task ID,
and requires the artifact to match the authoritative character. PDS pins the
empty preset schema, registry and deterministic categories and derives the
Filex read/CAS itself. The prior Filex receipt is optional storage binding,
not dependent-task readiness. The transaction remains open through the
Filex response. A running task is not automatically revoked or retried.

When the current snapshot changes before any write of the exact preset, the
writer or PDS returns the exact 409 body
`{"error":"WORKFLOW_PRESET_REJECTED_BEFORE_WRITE"}`. A generic conflict,
unconfirmed receipt, or transport error is not a no-write proof. Never emit
the typed rejection after starting a new write for that attempt.
