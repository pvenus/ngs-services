# Current-generation deletion: staged snapshot before closure

Local rollout status (2026-11-16): current-generation provider and runtime evidence
are installed for the verified runtime `dfb13b6` / backend-writer `52c605c` baseline.
Isolated dynamic HTTP delete/restore passed; no live NPC deletion was performed.
Unknown legacy files remain preserved and global deletion is disallowed. See
[NPC-owned transport status](NPC_OWNED_TRANSPORT.md). Later source changes need
their own deployment and acceptance.

PDS owned deletion now uses the authenticated writer in this order:

1. Read current revision/source and require the exact active generation with no
   leases (or resume the same staged/deleting request).
2. `stage_delete` persists `delete-staged`, blocking new admissions/publications,
   then produces and verifies the full owner backup. It returns generation/request/
   source-bound graph, archive and fence evidence, not caller-supplied assertions.
3. CurrentGenerationInventory combines that archive with trusted producer/ledger/
   host evidence and a separate proof of blocked legacy resume. It computes the
   restore closure and backs up registered current-generation external files.
4. Bind the exact external inventory hash to writer `prepare_delete`; revalidate
   source and the staged archive. Commit uses the SAME archive and source CAS.
5. Only after the authoritative committed receipt may external scratch move into
   recoverable quarantine. No ambiguous legacy file is removed.

This removes the former cycle where external preparation needed restore proof
that existed only after deletion. The existing whole-history coverage provider
is not relaxed; it accepts the optional staged parameter but retains its own
strict global-complete check. The scoped provider explicitly reports
`globalCoverageComplete:false`, `legacyPreserved:true`, `wholeHistoryErased:false`.
It verifies declared current owner dependencies, not ownership of all history or
shared motion presets. Unknown schema/reference scans block scoped deletion.

Failures after staging preserve canonical and scratch bytes and keep admissions
fenced. Same immutable request may resume after proof/input repair. Different
request IDs cannot steal the fence. No timeout/automatic unfence/cancel path is
introduced. A source CAS change requires review, never silent rebase. Corrupt
backup must not be repaired by silently overwriting its original evidence.

Temporary actual HTTP tests cover delete→whole owner restore under a new generation,
separate recoverable external scratch restore without reactivating jobs, incomplete
proof preservation/retry, competing admission/deletion at the staged barrier, and
backup corruption between closure and commit. No production NPC is deleted or
changed by these tests. On a new installation, runtime evidence/provider installation and deployment are
separate gates; the implementation never enables scoped deletion by configuration
fallback. The local rollout status above is distinct from these synthetic tests.
