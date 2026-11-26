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

### 2D motion preset DAG

The active typed planner accepts `motion-preset.convert-2d` schema
`ngs.motion-preset-convert-2d.v1`. The original motion preset is read-only;
the requested `variantPresetId` is the independent publication target.

| Task key | Type suffix under `motion-preset.convert-2d` | Worker | Dependency |
| --- | --- | --- | --- |
| `motion_2d_image` | `.image` | image | none |
| `motion_2d_frames_{n,ne,e,se,s,sw,w,nw}` | `.frames` | animation | exact image task |
| `motion_2d_encode_{n,ne,e,se,s,sw,w,nw}` | `.encode` | postprocess | only the matching direction's frame task |
| `motion_2d_finalize` | `.finalize` | postprocess | the exact eight ordered encode task IDs |

All input rows bind the original source and target IDs. Frame and encode inputs
add their direction; final input adds `dependsOnTaskIds` in the table's given direction
order. Finalization leaves the historical scalar dependency column empty,
not as a readiness signal: the postprocess finalizer claim must enforce this
exact eight-ID fan-in against completed encode receipts and
PDS must independently verify all eight before canonical publication. Do not
activate intake until these consumers are installed. No workflow DB schema
migration or change to legacy NPC execution. RS's completed notification
requires the exact persisted graph, status completed on every task, and
matching typed result refs: `motion.2d.image.<sha256>`,
`motion.2d.frames.<direction>.<manifestSha256>`,
`motion.2d.clip.<direction>.<sha256>`, and
`motion.2d.published.<presetHash>`. These refs are worker/PDS evidence, not an
independent Filex read by the Planner. Invalid/partial graphs do not report
completion. The historical Laya configuration is not on this intake path;
registration and routing are in the typed RS family and active planner code.

New requests stage 18 tasks. Replayed requests keep their original task rows;
the existing 10-task graph is accepted only for historical completion evidence.
Do not select the new planner independently of the matching PDS and worker
release, and drain any old active graph on its current installed workers before selecting
the split release.

### NPC selected movement

The selected one-stop schemas (create and replace) keep the existing
preparation and directional portrait tasks, then plan only the exact
selected motions. Each direction/preset yields
`animation_motion_<slot>_<direction>` and
`frames_motion_<slot>_<direction>`, where `slot` is the first 16 lowercase
hex digits of SHA-256 of the exact UTF-8 preset ID. Tasks keep their existing
types and worker lanes. Task inputs pin the exact preset ID in `motion`,
its pose in `posePreset`, and the expected `assetSetSha256`. The validated
directional character portrait supplies appearance for either pose group;
a combat motion is not a request to create a separate combat portrait.
Frames depend on the same exact preset/direction animation. PDS enforces
published source identity at execution. The existing walk/run graph for old
noncombat rows is unchanged.

### NPC portrait input-read ownership

Planner adds a nullable local ownership marker without changing existing rows'
status, retry count, timestamps or dependencies. New image workers use it only
while reading data before rendering. A NULL marker never authorizes an
abandoned/read failure. On the same Mac, a positively absent process plus an
unchanged running claim lets the image worker record an ordinary failure.

Deploy Planner compatibility before enabling the new image worker. The
previous Planner's exact-schema recognizer cannot reopen the extended database.
To roll back to that version, verify no marked claims are outstanding, stop
writers, and remove the column through a reviewed maintenance procedure first.

## Codex worker retry policy
Document and image jobs use a one-minute cooldown and up to 30 automatic
retries after the initial attempt. Other workers retain the ten-minute,
three-retry policy. The planner pins a per-task limit, preserves task state
and already-exhausted attempts during migration, and settles failure using
that limit. This does not retry a publication with unknown outcome.

### Up to five prerequisites

The existing `depends_on_task_id` remains the primary prerequisite and
keeps historical request plans stable. The additive migration adds nullable
`depends_on_task_id_2` through `depends_on_task_id_5`. All non-null
prerequisites must be complete for a worker to claim a task. New plans only
reference earlier steps in the same request; the ledger rejects holes,
duplicates, self-references, malformed extra IDs, and missing/cross-request
extra prerequisites. Task retry/status updates do not rewrite edges.

Deploy readers first (all worker instances, PDS, OPS), then enable writes by
setting `WORKFLOW_MULTI_DEPENDENCIES=1` in the Planner environment. By
default all four new slots are left NULL. Enabling writes applies to new
plans only: existing requests are not rewritten on replay. Keep compatible
readers deployed if later disabling writes, since already-written edges
still govern existing plans.

NPC idle/combat portrait rows retain their respective planning step as the
primary prerequisite and record the same request's representative portrait
as the second prerequisite. PDS continues checking the representative
portrait for older requests too. OPS retains the legacy `dependsOn` value
and adds an ordered `dependencies` array for multi-edge steps.
