# Exact published catalog folder deletion (staged)

The standalone `file.delete` request (schema `ngs.file-delete.v1`) accepts one
published managed prop or NPC folder. It plans exactly one `file_delete` task on the existing
file worker (no new process/model and no separate batch request). Selection,
hashing and safety checks must not involve an LLM. The precise typed Planner
is the execution registry for this machine-only operation; the separate
Laya creative generation registry does not make deletion decisions.

```json
{"schemaVersion":"ngs.file-delete.v1","projectId":"binari","target":{"root":"canonical-files/binari/props","path":"prop-id","kind":"directory"},"expected":{"revision":2,"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}
```

The caller must use the selected published prop's manifest revision and SHA-256.
PDS independently binds the whole exact claim to the trusted Planner row, and
the Writer compares the live head and its manifest while holding the prop
writer lock before moving the one folder. All versions and animations move
with it to the existing private quarantine. Request history is not deleted.
A stale pin is an explicit pre-move failure; ambiguity does not become success
or warrant dispatching unpinned deletion. A pending stale request must not use
a fresher pin implicitly.

Only the owner-local PDS with the already-enabled managed prop workflow turns
this on by default. `PDS_WORKFLOW_PROP_FOLDER_DELETE=0` explicitly opts out;
`PDS_WORKFLOW_PROP_FOLDER_DELETE=1` is available for isolated deployments.
Enable only after the matching planner, RS, PDS, Filex Writer and existing file
worker have been staged together and the file worker runs in its existing
`--single-target` mode. The PDS capability must advertise
`canonical-files/binari/props` on `GET /v1/storage/delete-target`, and RS must
advertise the type in `request_submit`. A client/consumer must not offer
deletion if not supported. No live operation or deployment is part of staging.

## NPC selection pin

```json
{"schemaVersion":"ngs.file-delete.v1","projectId":"binari","target":{"root":"canonical-files/binari/npcs","path":"npc.example","kind":"directory"},"expected":{"creationTaskId":"workflow.task.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}
```

Read the selection pin from the same replicated NPC row the person saw:
the canonical per-NPC `metadata.json` has `creationBasis.requestId`, the exact
preset task ID. Do not query current generation at button press or replace
the pin after acceptance. Only expose the public preset task ID, not the
private generation token. The committed Filex preset receipt pins that
task to its original NPC ID and private generation. PDS verifies the
receipt, same current metadata creation task, and same active generation
before using the existing fenced NPC folder move. Filex validates generation
and by-source revision again under the Writer lock. It quarantines the whole
folder, updates the catalog index, and retires old leases as one transaction.
The earlier NPC replacement path remains exactly as before.

The additive `PDS_WORKFLOW_NPC_STANDALONE_DELETE=1` is also off by default and
requires `PDS_WORKFLOW_NPC_FOLDER_DELETE=1`. Importantly the NPC root appearing
on the generic capability endpoint means the established replacement flow
is available, **not** that a standalone NPC delete is available. Probe the
dedicated `GET /v1/storage/delete-target/npc-standalone` capability first.
The standalone payload still uses the same official `file.delete` type and
writes to the same exact single-target PDS worker port. Legacy NPCs without a
valid current preset receipt are not accepted; legacy props without managed
metadata are not accepted either.

## Remaining catalog kinds

Background and cinematic sources are still separate files under shared
roots; a full item has no exact single folder to move. Cinematic clips may
also exist under separate immutable sequence or legacy result IDs. Do not
substitute archival/hiding or deletion of the broad shared directory for
full-item deletion.

## Cinematic scene selection (split physical units)

A cinematic source and its animation results are not stored in a single folder.
An owner-local caller first obtains authoritative membership from
`GET /v1/storage/delete-target/cinematic-inventory?sourceSha256=<sha256>`;
the dedicated capability is `GET /v1/storage/delete-target/cinematic`. These
are PDS loopback endpoints, not browser or public writer access. This feature
is disabled unless `PDS_WORKFLOW_CINEMATIC_DELETE=1` and the existing cinematic
Filex scope advertises `workflowPinnedCinematicTargetDeleteV1`.

Each returned `{root,path,kind}` is submitted individually as `file.delete`
with `expected:{sourceSha256}`. Root is
`canonical-files/binari/cinematics`. The inventory includes archived
sequence folders, legacy result files, and the canonical source sidecar/PNG.
Queue animation removals first (legacy video before its sidecar), then source
sidecar, then source PNG. The file worker defers a dependent delete if the
corresponding earlier requests are in progress or retryable. Individual
requests retain individual outcomes; no batch delete, no new service, no
shared quarantine or archive index removal.

Inventory and exact deletion require a verifiable source/reference; in-use
cinematic render/playback, shared source aliases, or ambiguous legacy output
fail closed. The same Filex writer lock fences late publication and rechecks
that no public child remains before retiring a source. A race can produce
a safe partial outcome (remaining source or animation still present); it
cannot silently detach a concurrently committed animation. Retry from fresh
authoritative membership, not visible-only UI copies. Recovery remains the
common writer quarantine for each exact target.

## Map folders

The existing `file.delete` request handles exactly one map folder using
`target.root="canonical-files/binari/cinematics/maps"`, `target.path=<mapId>`,
`target.kind="directory"`, and `expected:{revision,officialRequestId}` from the
latest published map row selected by the user. The map identifier and latest
publisher are distinct after the first edit. No source background is deleted.

Owner-local deployments with the existing map workflow default to support this
scope; set `PDS_WORKFLOW_MAP_DELETE=0` to disable it. Intake and the existing
file worker require `GET /v1/storage/delete-target/map` to report the exact
root and schema `workflow.single-target-delete.v1`. The exact Planner resolves
one `file_delete` task on the existing worker, with no model or separate runtime.

The Filex writer compares the latest revision and latest publisher under the
same lock as map publication. A stale or wrong pin fails before mutation.
The writer installs a per-map retirement marker and moves the map folder,
including immutable revisions, to the existing private recovery quarantine.
The exact operation can recover an interrupted move; unrelated requests cannot
reuse its marker, and a late map publication cannot recreate a retired map.
