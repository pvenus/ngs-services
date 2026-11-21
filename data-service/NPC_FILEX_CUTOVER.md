# NPC Filex authority

`FilexCurrentNPC` is an explicitly configured, current-only NPC authority.
PDS mediates authenticated HTTP to a separate NPC writer; workers do not receive
filesystem mounts or the writer credential. Domain current/save/category/capture
read Filex, with complete changed files and durable receipt metadata (no old body
versions). A storage-root cutover marker prevents silent DB fallback if config is
lost or points elsewhere. Existing SQLite data remains preserved but is not the
active NPC body source.

`GET /v1/npcs/files` provides one coherent canonical snapshot to the NGS replica.
It is server-only and fails closed when unavailable. Replica code is responsible
for retaining last-good files, manual/menu refresh, and ACL verification.
Portrait bytes and publication reads use the Filex asset route. The dedicated
character deletion route verifies a complete folder and referenced-asset backup;
ordinary saves cannot silently delete files. Old unrelated assets are retained.

## Verified local cutover

On 2026-11-14, the user approved NPC-only writer/mount/credential and restart
changes. The local writer was started at `127.0.0.1:8094`. Its mount is exactly
`canonical-files/binari/npcs`; Filex has the same subtree read-only. Knowledge
writer and authentication remain separate. Unauthenticated request: 401;
authenticated capability: NPC namespace and lockedBatch.

With the official consumer stopped and no active request leases, the current
NPC rev83 (one Seojin, occupation 별감, four JSON files, no referenced portraits)
was copied by `scripts/migrate_npc_filex.py`. Decoded body hash:
`1f5a6948011d112f15dc3ba76e22fe249c359bb2c1d263d53e38e877348bd2e2`.
PDS `/api/npcs` and `/v1/npcs/files` then matched each other and the preserved
DB source exactly. No actual NPC deletion or new content/model generation was
performed for this migration. NGS UI/deployment checks are tracked separately.

## Tests and operations

`cd data-service && PYTHONPATH=.:../laya-service python3 -m unittest discover -s tests`
`cd filex-writer && python3 -m unittest`

The migration command defaults to read-only planning. `--apply` requires explicit
NPC config and `--source-frozen`; an occupied nonmatching target is rejected.
After verification it records the permanent authority marker. Never delete that
marker to fall back to stale DB content. Revision counters are concurrency tokens,
not retained body versions. No Space deployment, old knowledge changes, per-item
backups, or existing backup removal are included.
