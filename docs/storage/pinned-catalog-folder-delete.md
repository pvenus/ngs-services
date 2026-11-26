# Exact published catalog folder deletion (staged)

The standalone `file.delete` request (schema `ngs.file-delete.v1`) accepts one
published managed prop. It plans exactly one `file_delete` task on the existing
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

Legacy props lacking managed metadata are not accepted. NPC replacement
already has a generation-aware folder transaction that updates shared metadata
and retires old leases, but standalone NPC deletion still needs a publication
generation pin available to both NGS replicas. Looking up generation at delete
execution (rather than at selection) is unsafe. Background and cinematic
sources are still separate files under shared roots; a full item has no exact
single folder to move. Cinematic clips may also exist under separate immutable
sequence or legacy result IDs. Do not substitute archival/hiding or deletion
of the broad shared directory for full-item deletion.
