# Isolated exact NPC workflow deletion
This is a default-off integration candidate. Do not enable with a fixture verifier. Existing NPC deletion/retirement routes, authentication, tokens, launch settings and cleanup/backup requirements are unchanged.

- PDS: loopback, exact Host, no Origin, POST `/v1/npcs/ownership/workflow-delete` with `{binding,claim}`. Binding keys: `officialRequestId,taskId,projectId,npcId,payloadSha256,generationId,expectedSourceVersion,expectedRevision`. The verifier independently resolves that exact binding from the opaque claim, not from request inputs. It must be wired to the authorized formal workflow owner and guarantee that dispatch/confirmation and cancellation cannot invalidate the approved task while a commit is outstanding. No verifier is supplied in this change; the default server returns disabled.
- Writer: explicitly opted-in in its handler composition, existing authenticated channel only, exact `/v1/workflow/delete-character`. Does not obtain a legacy lease, ask for legacy queue status or rely on legacy lease expiry. Holds the same writer lock throughout canonical source/generation/revision comparison and verified backup/atomic owner-file deletion and generation tombstone. Retains retired request IDs and binding audit; does not retain invalidated lease tokens. Does not change the normal delete path.
- PDS returns `workflow.pds-delete-receipt.v1` only after verifying committed backup, exact task and unchanged binding, and writer-confirmed target absence. Writer confirms exact old generation tombstoned, no roster entry/files and removes only empty directories under the exact owner. Replay rechecks current absence; if owner has since been recreated or unexpected content exists, fails closed. Request history and out-of-scope files are not deleted.
- Activation still requires independent review of trusted workflow authority and deployment wiring; direct Filex writes or live probes are not part of testing.

## Private local planner startup composition

The deployment wire is `POST /v1/npcs/ownership/workflow-delete` with only
`{claim:{officialRequestId,taskId,projectId,npcId,payloadSha256,claimStamp}}`.
The staged claim stamp is the value recorded in the exact task by its
pending-to-running CAS; this request cannot provide generation/revision/source.
PDS checks the fixed private planner SQLite identity, schema, running
file/delete task, exact stamp, planned run, deterministic task ID and exact
canonical replace payload/target. It derives storage CAS from authenticated
Filex reads; receipt replay uses the original recorded CAS. Read-only
rollback-journal SQLite transaction remains open through the result from
Filex, excluding concurrent planner commits during that authorization window.
The supported workflow has no cancellation or requeue of already-running
delete tasks; ambiguous Filex outcomes must reconcile under that immutable
claim and must not be auto-retried or revoked while in flight.

PDS activation requires three `PDS_WORKFLOW_DELETE_PLANNER_*` env values
(DB, DEV, INODE) chosen from existing private 0700 directory and 0600 DB owned
by the PDS process user. No legacy DB/queue is consulted. This env is stripped
from non-data stack children. Exact owner, mode, inode and rollback journal
are checked at startup and per use. A new DB inode or WAL deployment requires
review, not an automatic downgrade. The Filex NPC writer alone takes
`WRITER_WORKFLOW_EXACT_NPC_DELETE=1`; existing bearer authorization, bindings,
UID, mounts and isolation remain unchanged. Disabled deployments retain the
old behavior. Non-mutating GET on the same PDS route verifies planner readiness
and genuine writer opt-in without selecting a task. Do not use a production
POST as a health probe.
