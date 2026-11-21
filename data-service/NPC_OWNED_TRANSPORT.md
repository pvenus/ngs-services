# NPC-owned artifact transport boundary

## Current local rollout (2026-11-16)

The local rollout is enabled. The verified rollout baseline is runtime `dfb13b6`
and backend/writer `52c605c`; later source commits are not automatically deployed.
NGS and OPS select the trusted host configuration with
`NGS_NPC_OWNERSHIP_REQUIRED=1`, `enabled=true`, and
`cleanupCompletedCopies=true`. This is local PDS mediation, not remote networking.

- Migration/readback evidence verified 2,187 NPC-owned slots through PDS and
  2,242 backup files, with the live source revision unchanged during verification.
- The installed provider uses **current-generation** staged deletion and verified
  whole-owner restore. Unknown historical files and shared presets are preserved;
  `globalCoverageComplete=false` and `globalDeleteAllowed=false`.
- Cleanup is limited to proven fresh-scope duplicate output bytes. It does not
  remove every scratch file, preserved evidence, or ambiguous legacy data.
- Final rollout acceptance records healthy services, idle consumer, zero leases,
  UI acceptance and isolated dynamic-provider HTTP delete/restore. No live NPC
  deletion or new model generation was performed for that acceptance.

Evidence: local `npc-retirement-rollout-20261116/acceptance.json` and its provider
receipt, plus the migration readback evidence. These are recorded checks, not
continuous health assertions. See [current-generation retirement](NPC_CURRENT_RETIREMENT.md).

## Transport contract (opt-in on a new installation)

- Filex writer `WRITER_SCOPE=npc-owned`: stable current slots, recoverable transactional replacement, NPC generation/lease authority, stale publication fencing.
- PDS `ownershipMode=npc-owned-v1`: factory chooses guarded transport, checks writer capability before mutation, propagates server-only request-local guard. No existing config is changed automatically.
- Worker admission: pin NPC generation at intake, then persist the server lease in parent journal. Child requests reuse parent guard with a stage ID, not a new generation. Headers never enter user intent or model prompt. Completed release is idempotent; blocked work retains its lease. Legacy unpinned queued work must not auto-adopt a new generation.
- PDS→Filex authenticated writer protocol continues on existing dedicated 8094 endpoint, token and root ACL. No new port, token, broad bind, or permission is introduced.
- Asset consumers receive immutable hash/size/type/slot identity, download bytes through PDS into their own workspace, and validate hashes before processing. No producer path or Filex mount is provided to consumers.

## Verified boundary, not a remote-host claim

`tests/test_npc_owned_authority.py` runs actual HTTP PDS Handler → authenticated writer fixture. A separate Python subprocess with a different empty working directory receives only a PDS URL and hash, downloads a stable-slot fixture, verifies bytes and writes its private input. It receives neither writer credentials nor a Filex directory. This proves process/workspace independence. It does not prove network access from another physical host, OS-level filesystem isolation, GPU/model portability, or deployment readiness.

## Current access limits

Backend stack binds all services to 127.0.0.1. Active Filex bridge rejects non-loopback PDS binding (`BRIDGE_LOOPBACK_ONLY`). Current NGS gateway validates loopback remote address, Host and Origin. Worker clients also select loopback PDS. Do not remove those checks to make the distributed architecture appear complete.

## Future explicitly approved remote adapter

Retain the same worker interface: generation lookup/admission/finish; required context and artifact download; scoped output publication; receipt lookup. Keep Filex writer private and let only PDS hold its credentials.

A remote deployment requires a separately approved authenticated TLS gateway (or existing equivalent after verification):
- Device/service identity per worker, short-lived credentials with revocation, TLS server identity verification; preferably mutual TLS where deployable.
- Explicit project + NPC generation + parent request + worker role scopes, and read/write endpoint allowlist. No arbitrary file path, URL proxy, filesystem access, management/restart route or writer credential forwarding.
- Bind audience, parent/stage IDs, request hash, byte hash and generation fence; preserve idempotency and partial-upload recovery. Rate/size/concurrency limits must retain large frame-artifact support without broad API access.
- Restrict allowed source machines/network; log IDs/hashes/results without token or full prompt secrets; expire/cancel credentials without automatically stealing active leases.
- Test two real hosts, unreachable PDS, interrupted upload, stale generation after deletion/recreation, duplicate submit, changed hash, unauthorized NPC and hostile redirects before enabling.

These network/authentication/access changes are not implemented or authorized merely by choosing NPC-owned storage. The current work intentionally leaves loopback protections unchanged.

## Historical pre-rollout cutover gates (superseded locally)

The following checkpoint describes prerequisites before the local rollout above.
It remains a checklist for a new installation, not the current local enable state.

All runtime request families need intake pin + guard propagation, including lifecycle/create/restore and deferred portrait finalization. Trusted external ownership inventory must be bound by server deletion coordinator. Migration must cover canonical SD/alpha source artifacts and active slots, preserve all backups and shared presets, and verify whole backup/restore on an isolated copy. Legacy global blobs are not purged by this change. Production selector/config stays off until those gates and actual NGS copy/UI tests pass.

## Historical integration checkpoint

Dedicated writer lifecycle (`50f4335`) and PDS routing (`600eef7`) now cover create and verified restore without borrowing a generation worker lease. Create commits new generation + owner documents in the same journal. Restore fetches verified backup JSON through authenticated writer, derives metadata at PDS, verifies full backup bytes at writer, and commits a new generation. Prior generation jobs cannot re-admit. Source changes between preparation and commit fail closed; no automatic rebase exists.

Trusted external inventory integration (`9202cf2`) remains opt-in: without server-proven all-host inventory coverage, deletion is blocked. Current producer and cancellation hooks must be complete before cutover. There is no permanent-purge operation.

Final focused run at this checkpoint: writer suite 48 passes, combined PDS/authority/animation/pose/external-inventory suite 49 passes (inherited fixture tests repeat some cases). Covered actual isolated HTTP authentication, parent/child receipt binding, stale generation, verified create/delete/restore, independent-process PDS-only download, and origin rejection. No operational migration, real worker execution, deployment, NGS UI verification, or remote-host execution is asserted by these results.

## Race acceptance extension

`tests/test_npc_owned_races.py` starts an actual downstream subprocess in an empty workspace. It receives only a PDS URL and expected hash, downloads an RGBA PNG fixture, pauses at a deterministic barrier, then performs 8×8→4×4 RGBA downsampling locally. This is a synthetic transform worker, not an image-model run or full production frame pipeline.

While that process is paused, tests (1) replace its input slot and (2) attempt deletion, retire the original lease, delete the NPC, and restore it under a new generation. Evidence: active-lease deletion is rejected; late publication cannot overwrite the newer slot due to source CAS; deleted NPC cannot be resurrected by the old guard; the same old guard cannot upload or publish after new-generation restoration. The process completes using its downloaded input despite source replacement. Controlled lease retirement simulates a late completion after an acknowledged terminal retirement, not permission to release a real running job's lease. At this test checkpoint, production cancellation/retirement installation was a separate gate; the current local rollout is described above.

These synthetic race tests are distinct from the read-only live-source→temporary-copy full restoration fixture. That fixture includes current images/videos/frames plus pinned SD/RGBA source inputs, verifies 2,187 stable slots / 2,240 restored files / 136,013,521 bytes and hash downloads, and checks live source remains unchanged. Neither fixture authorizes production deletion or legacy global cleanup.
