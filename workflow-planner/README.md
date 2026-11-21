# Workflow Planner, standalone and planning-only

Node 24+ built-ins; no package installation. There is no HTTP listener and no link
to the legacy workflow launch agents. The local command is:

```sh
node cli.mjs --db "$HOME/Workspaces/Workers/data/workflow-planner/workflow.sqlite"
node --test
```

Use an absolute path, an existing canonical grandparent and a dedicated
owner-only database directory (created 0700 if absent); database mode 0600.
Optional `--once` after the path does one cycle and exits nonzero on a poll
failure or unacknowledged plans. Long-lived mode polls every 3s after each
cycle, retries on the next cycle, logs only summary counts/error codes, and
stops after the current cycle on SIGTERM/SIGINT.

The sole integration is fixed `http://127.0.0.1:8770/mcp` with
`request_workflow_list({limit,cursor?})` and
`request_update_status({officialRequestId,status:'planned'})`.
The latter **must return the same success receipt on an exact replay**
even when the row no longer appears in the registered-only list. On a 409
tool result the local run is permanently not claimable; other failures retain
`awaiting_rs` for repeat polls. Confirmed ACK is the only transition to local
`planned`. No RS database is opened. The test-only dependency injection is
`new PlannerStore(path)`, `pollOnce(store, rs)`, and
`createRSClient({transport:makeLoopbackTransport(ephemeralPort)})`.

Supported official envelopes are exact noncombat `npc.workflow` with
`ngs.npc-one-stop.v1` (45 tasks) or `ngs.npc-one-stop.replace.v1` (46 tasks).
The validated canonical request is stored untouched. Unsupported combat or
invalid/mismatched envelopes are counted, without creating a run or changing RS.
The replacement observes the exact NPC identity and plans an exact-folder-delete
first, with no prior data or artifacts inherited; it does not delete anything.
Common preparation is followed by eight independent branches. Each direction's
portrait can unlock its own walk and run native PNG; each animation unlocks only
its matching frame postprocess. Task IDs are deterministic and dependencies
use one optional same-run foreign key. Full run/tasks insertion is one transaction.

This package has no executor, worker launch, claim/work API, request cancellation,
Filex/PDS access, actual file deletion, output publication or deployment.
Aggregate completion/failure is not reconciled here: that requires a future
trusted task-status writer and settled publication/terminal proof. No runtime
knowledge or other service registration changes are made.

Formal `concept.category.workflow` requests use the same RS handoff and
Planner DB without changing any existing NPC task. Add creates one document
task; update creates one document overwrite task with no file deletion.
Legacy replace is history only for new planning; its persisted graph is not rewritten.
Migrate creates one deterministic document task; remove creates one
scoped file task. Workers still acquire exact tasks themselves.

### Space request provenance

Local registered rows keep the original shape. Verified Space registered
rows also carry `sourceId: "space"` and `sourceKey` (the immutable queue ID).
Planner verifies that the official request ID commits to the source, Binari
project and queue ID, then uses the same type-specific task graph. No SQLite
migration or worker task/claim DTO change. Before rolling back to the old
Planner, stop Space intake and drain any unplanned Space registrations;
already materialized runs remain compatible.
