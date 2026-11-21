# Explicit NPC owned-slot migration

Administrative storage migration, not a content-generation worker. No services,
credentials, mount/network permissions, writer selectors or runtime configuration
are changed by this runner.

## Official path

`python3 scripts/migrate_npc_owned.py --plan PLAN --request-id ID` checks the
preflight digest and all imported bytes offline. Applying additionally requires
`--apply --reviewed-plan-hash HASH` and an already reviewed owned-mode PDS endpoint
(`--pds http://127.0.0.1:8030`). A read-only preflight is not deployment approval.

The runner never reads writer credentials or writes Filex paths. Normalized
owner-relative documents, hashes and bytes pass through server-only PDS routes:
POST `/v1/npcs/ownership/migration/{prepare,asset,commit}` and
GET `/v1/npcs/ownership/migration/status?requestId=...`.
Existing loopback/Host/no-Origin checks remain; authenticated writer capability
checks precede mutations. Migration bodies are limited to 64 MiB without changing
other endpoints. Import local paths never appear in the wire plan. This one-time
legacy importer is not a downstream-worker local-path fallback.

## Atomicity and recovery

Prepare requires exact sourceVersion/planHash, global idle leases and no cleanup
or lifecycle reservation. A persistent global migration fence blocks other
admissions/mutations. Same request resumes; changed plan cannot reuse an ID.
There is no TTL, lease steal, automatic rebase or unsafe abort.

Hash-verified imported bytes are staged, not published. Commit verifies all prior
references/imports/prospective slots. It snapshots owner files, root metadata,
prior referenced binaries and imported originals, verifying all hashes before
canonical mutation. Writer computes root revision/documentHash. Slots, JSON,
manifest, receipt and fence completion share a recoverable journal. Only
cooperating PDS readers have multi-file isolation.

After journal creation, resume the SAME writer request/journal. Never edit files
manually or downgrade. Missing/corrupt inputs leave canonical data unchanged and
fence retained for operator repair. No automatic discard is provided.

Pre-migration snapshots are NOT normal deleted-NPC backups: they include root,
legacy and imported copies. General NPC restore must reject them. Data rollback
needs a separately reviewed CAS publication at a NEW revision, without reverting
other NPC changes. No automatic rollback command exists. Preserve all snapshots,
journals and imports until recovery review completes.

## Legacy and activation

Unknown legacy remains preserved in place, outside current-slot publication;
physical quarantine/reclamation is a separate recoverable operation. No age/name
or prefix guessing. Old assets, shared presets, other NPCs and backups are not
removed. This migration intentionally retains recovery/import copies.

Operational apply still needs a compatible frozen build, authoritative nonrunning
job checks, lease zero, complete inventory, reviewed configuration, fresh source
preflight and recovery review. Afterwards verify every hash/download, downstream
admission and NGS manual replica/UI. Do not equate fixtures with live conversion.

## Evidence

`test_npc_owned_migration_http`: actual isolated PDS→authenticated writer HTTP,
Origin/lease/hash denial, binary import, verified snapshot and same-request replay.
The separate `migration/http_fixture.py` copied current originals into temporary
storage and migrated 531 imports to 2,187 slots; all 2,187 hash downloads and the
2,242-file snapshot verified. Unknown legacy survived; live originals unchanged.
Not production migration, actual remote-host execution, or model generation.

Regression at `d14744c`: writer discover 70 executions; selected PDS authority,
race, external cleanup, Filex integration, animation/pose/frame and migration
suites 92 executions passed. Counts include inherited fixture tests, not distinct
features. Migration status HTTP is additionally checked not to expose its token.

## Explicit post-migration generation activation

Workers still never bootstrap generations. An administrator separately invokes
`scripts/activate_npc_owned.py` with the committed migration request, post-migration
source version, wire plan hash and verified backup-manifest hash. It is read-only
unless `--apply` is present. POST `/v1/npcs/ownership/migration/activate` retains the
same local server-only/authenticated writer boundary.

The writer checks the committed migration, exact current source, backup hashes,
manifest-derived lineage and every slot byte, and an absent generation or an
idle active generation. It never revives deleted/retired generations. A new
active generation and activation receipt share one journal; existing idle active
generations retain their identity. Same activation retries preserve the ID.
The activation receipt is historical; workers must subsequently GET the current
generation and admit their own request, not trust its saved activeLeases field.
This helper neither relaxes legacy inventory coverage nor deletes legacy scratch.
