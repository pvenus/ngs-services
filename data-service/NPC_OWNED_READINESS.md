# NPC-owned cutover readiness review

Review baseline: backend `9525278` (includes deletion-receipt `473de57`, completed-copy cleanup `c9313f9`, publications `3cd6a2d`), runtime lifecycle contract reported at `d221556`. This is a source/staging review, not activation approval or a production result.

## Passed evidence

- Whole writer discovery suite: 51 executions passed, including current uncommitted backup-preview integrity regression.
- Combined PDS owned-authority/races/external-inventory/Filex/animation/pose/frame suite: 63 executions passed. Some inherited fixture cases repeat; this is not 114 unique scenarios.
- Actual isolated authenticated HTTP PDS→writer tests cover guard propagation, parent/stage receipt binding, create/delete/restore, current-equivalent and deletion receipt reads, wrong Origin rejection and generation replay fences.
- Independent empty-workspace subprocess downloads only through PDS and performs deterministic RGBA downsampling. Delayed output cannot replace newer input-slot state or resurrect a deleted/restored NPC. This is a synthetic transform worker, not actual remote-host/model execution.
- Current live source was read into an isolated temporary copy. SD3 + RGBA528 source imports, stable-slot migration, deletion and new-generation restore verified 2,187 slots, 2,240 files, 136,013,521 bytes and 2,187 hash downloads. Original source/old backups/shared assets remained unchanged. Evidence: staging migration fixture, not production backup certification.

## Production activation conditions

1. One frozen compatible writer/backend/runtime build; every family (planning, central portrait, pose, animation, frames, create/delete/restore, deferred confirmation, cancellation) either propagates original admission identity or deliberately fails closed. Existing unpinned queued work must not acquire the new generation silently.
2. Official request cancellation/terminal cleanup must prove non-running GPU work, all dependencies finalized, no unpublished inputs and complete ownership registration before releasing the lease. No TTL expiry or lease stealing.
3. Server-created inventory provider must prove coverage of all configured producer hosts and legacy paths. Empty ledger is not complete coverage. Preserve audit rows, model caches, shared presets, Library-delivered files and all existing backups.
4. Completed-copy cleanup needs proof that its verified canonical equivalent remains available throughout deletion of the local duplicate. At this review, `current_equivalent` GET and local unlink are separate lock domains: a new writer publication can replace that slot in between. Resolve with a writer-side reservation/fence or an equivalently demonstrated protection; add an actual interleaving regression before enabling irreversible scratch unlink. No production cleanup was run during this review.
5. Real admission freeze and zero active leases/model calls before deployment/cutover. Re-run preflight and invalidate it on any source/hash drift. Save and verify one whole migration recovery snapshot, then run the reviewed authenticated PDS migration request. Do not edit active Filex JSON or replay an old root metadata file directly.
6. After commit, verify every current slot and required raw/alpha source by PDS download, one empty-workspace downstream execution, NGS manual replica refresh/UI, and old-generation late result rejection. Preserve source receipts and distinguish publication from replica/UI success.
7. Register updated worker IO/lifecycle knowledge through the official path and verify real Laya selection. Unit-mocked selection is not real registry acceptance.
8. Keep the old global store until the separate reference-safe reclamation below is reviewed. Production enablement remains off while any required gate is unresolved.

## Legacy shared assets: safe transition, not blind removal

- Inventory all authoritative NPC manifests, full/half frame sources, central SD and directional references. Include every NPC, not only Seojin. Separately inventory registered shared preset/resources and references from other domains.
- Produce a pinned mapping old asset ID → NPC stable slot(s), with hashes, sizes, media types, sourceVersion and exact owner. A hash can occur in several NPCs; a single-owner guess is not proof.
- Copy/import bytes through PDS staging and one CAS stable-slot transaction; verify all current consumers resolve the new authority. Preserve original globals during this phase.
- A legacy object is a reclamation candidate only after all active published references and every supported pending/resumable job are accounted for. Historical audit hashes alone are records, not a reason to fabricate a current content reference. Conversely, a still-supported resumable job must not lose its only input.
- Create a deletion-only/full migration recovery manifest binding exact paths and hashes, then use recoverable quarantine where appropriate. Preserve previous backups and unknown/unowned files. No `rm assets/*`, no prefix/mtime ownership inference, and no per-edit version-backup scheme.
- Restore/recreate uses a new monotonic generation, never the old job tokens. Rollback must preserve other NPC changes and all journal/stage data; do not downgrade the writer until every retained reference is served by the chosen backend.

## Permission and network boundary

No ports, bind addresses, authentication keys, Filex ACLs, security settings, service permissions or operational files were changed by this review. Existing private 8094 writer remains PDS-only. Existing PDS/NGS routes remain loopback/Host/Origin protected. The proposed `ownershipMode` and writer selector are explicit, disabled defaults.

Worker portability means no shared-disk/local-producer-path dependency. Actual remote connectivity is not currently supported: the stack and worker clients bind/select loopback, and the bridge explicitly rejects non-loopback PDS. A future authenticated TLS gateway, device identity, narrowly scoped endpoint/project/NPC permissions and network access would need separate review/authorization. Do not remove loopback checks or hand writer credentials to remote workers to claim completion.

## Follow-up integration at `e601d91`

- The canonical-equivalent race described above now has a writer reservation implementation (`2b8004a`) and matching PDS/coordinator flow (`ebb4eb3`). The reservation is durable, blocks admission/publication/delete/finish for that NPC, has no TTL stealing, and is released only by exact id/token after local removal audit commit. Actual isolated HTTP reserve/release and crash-after-unlink tests pass.
- A source-integration omission was found and corrected in `e601d91`: alphaAssetId bytes/dimensions and half lineage validation are now in the real backend source; half publication no longer downloads obsolete video bytes. Existing legacy undeclared-alpha payloads retain compatibility. New tests reject missing/dimension-mismatched/swapped alpha and forbid any half-path MP4 GET.
- Rerun: writer discovery 55 passes; combined PDS suite 68 passes.
- Additional recovery review identified a batch-level release-retry gap: once local owned rows were removed, a lost release response could leave a reservation that the batch iterator would not revisit. This needs its pending release-audit enumeration regression and fix included in the final staging build. Until confirmed, do not infer production cleanup readiness from the test counts above.

### Release-retry closure at `0fdc708`

The batch retry gap is fixed: cleanup iterates the union of current owned rows and durable `removed` audit rows, then records `released` only after the exact writer reservation release is acknowledged. Network failure before release and success-with-lost-response both retry idempotently, including after unlink. Re-reviewed code and reran the combined PDS suite: **70 passed**. The whole writer suite remains **55 passed**. These resolve the two cleanup findings above in the candidate build; production inventory/configuration, knowledge/live selection, migration and UI gates still apply.
