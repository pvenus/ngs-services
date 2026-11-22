# NPC motion preset selection

The NPC form offers the metadata for completed eight-direction motion
presets, including generated 2D presets. Opening or refreshing the picker
does not read, probe, or transfer the MP4 objects. The picker follows the
existing Local/Space catalog replica and refreshes its small list while
open. The noncombat and combat buttons only replace the editable draft
selection; the server does not supply a default or a substitute.

The authorized payload shapes are `ngs.npc-one-stop.selection.v1` and
`ngs.npc-one-stop.selection.replace.v1`. Their exact top-level fields are
`schemaVersion`, `projectId`, `intent`, `character`, `sourceRefs` and
`motionSelection`. Each of the 1–8 selected records is
`{presetId,posePreset,assetSetSha256}`, where pose is `idle` or `combat`.
The fingerprint is SHA-256 of compact UTF-8 JSON
`["ngs.motion-assets.v1",presetId,posePreset,[[direction,sha256],...]]`
in order `n,ne,e,se,s,sw,w,nw`. IDs are path-safe and unique ignoring case.

PDS only accepts an exact running Planner claim. The selected animation
or frame task key ends with `_motion_<slot>_<direction>`, where slot is
the first 16 lowercase hex digits of SHA-256 of the exact UTF-8 preset ID.
An animation depends on the existing exact directional idle portrait for
both idle and combat. Postprocess depends on the corresponding exact
animation, not on another direction or preset.

Selected scoped animation reads include `posePreset`, `assetSetSha256`,
`referenceSha256` and `referencePresetHash` in addition to the existing
motion, direction and portrait ID. Workers verify the exact reference and
full preset hash on the existing private motion download. Selected scoped
frame reads include the selected pose and fingerprint and the exact
canonical raw sequence. Document, image, static-pixel and deletion task
identities do not change.

For selected replacement, both NPC deletion entry points prove all picks
against one authenticated live motion catalog snapshot immediately before
the existing NPC delete invocation. A mismatch refuses without an NPC
write. Existing committed recoverable-delete receipts remain replayable and
require their original exact binding. The two writers do not share a
transaction: if the source changes after the delete preflight, the selected
media stage refuses the changed asset set rather than substituting it.

The NPC writer publishes under `<true-pose>/<exact-preset>/<direction>`
for raw, alpha and pixel manifests, and checks the exact owner generation,
portrait/raw identity and selected source binding under its existing lock.
Selected writes require the authenticated NPC writer capability
`workflowSelectedMotionV1` (native media still must be enabled). Older
legacy task keys/read DTOs and idle/walk or idle/run paths do not change.
No motion or media writer update is needed; their stored 3D/2D sources
remain read-only to this workflow. The deployed NPC-only image must include
the new `filex-live-roles/npc-owned` source; updating the Backend Stack or
the OPS concept-writer image alone does not install it.
