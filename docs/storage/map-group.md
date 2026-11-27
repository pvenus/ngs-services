# Map group publication

Filex stores each new revision under
`canonical-files/binari/cinematics/maps/<mapId>/`. The existing
`<revision>.json` name now holds the immutable commit manifest. Its exact
fields are `schemaVersion: "ngs.map-group.v1"`, `projectId`, `mapId`,
`revision`, `updatedAt`, `officialRequestId`, `title`, `regions`, and
`parts: {start, portals, collisions}`. Each part value is the lowercase
SHA-256 of the actual corresponding file bytes:

- `<revision>.start.json`: `{startRegionId}`, with optional `startPoint:{x,y}` in normalized thousandths
- `<revision>.portals.json`: `{connections}`
- `<revision>.collisions.json`: `{colliders}`, or `{}` if the merged graph
  did not have a collider field. An explicit empty list is preserved.

JSON is compact, sorted by keys, encoded as UTF-8 without ASCII escaping and
terminated by one LF. Parts are written and verified before the manifest is
published under the existing Filex lock. Ignore parts that have no manifest.
Once a manifest exists, readers must match each part's raw bytes to its digest;
never construct a graph using incomplete or unverified parts. An abandoned
pre-manifest write can be replaced by an eligible retry. An exact replay
returns the first committed version without rewriting it.

The scoped Filex API and the PDS projection still exchange one merged
`ngs.map-metadata.v1` document; result refs still end in `<revision>.json`.
A legacy merged document at that path is still readable and is not rewritten.
Pinned folder deletion still binds the latest numeric revision and publisher.

Generated map geometry can include validated colliders, in addition to portal
hit areas and arrival points. The submitted title, regions, selected start
region, and portal topology remain fixed. Updates must match the request
exactly.

The current `ngs.map-generate.v2` request requires a `design` object:
`startNotes`, `portalNotes`, and `collisionNotes` (trimmed strings of up
to 1,000 UTF-16 code units, and allowed to be empty), and `collisionMode`
(`suggest` or `none`). Its existing document worker has three observable jobs,
`map_start` → `map_portals` → `map_collisions`. The first two record exact
canonical JSON (without a trailing LF) in nullable `workflow_tasks.map_part_json`
alongside a digest-bound receipt in `result_ref`. The third task alone
publishes the group; the PDS checks the saved predecessor bytes, receipts,
and task dependencies before publication and again on completion readback.
A collision mode of `none` requires an explicit empty collider array.
Legacy `ngs.map-generate.v1` and manual `ngs.map-update.v1` remain single-task.
