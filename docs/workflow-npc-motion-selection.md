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

## Generate or regenerate only selected NPC animations

The NPC Animation tab uses the separate request type
`npc.animation.regenerate` and schema `ngs.npc-animation.regenerate.v1`.
Exact fields are `{schemaVersion,projectId,npcId,motionSelection}`; the
same preset fingerprint contract applies. The request can target an
existing active owned `npc.*` or `character.*` generation. Request Service
serializes animation regeneration and whole-NPC generation or replacement
for the same NPC while the request is active. Different NPCs do not block
one another.

The Planner creates only eight direction animation tasks and their exact
postprocess tasks per selected preset; it does not delete the NPC, rewrite
planning, or request new portraits. The animation roots have no same-run
portrait task: PDS/Filex verify the existing published `idle/portrait`
direction for both idle and combat motion slots. Missing or changed source
refs reject the affected lane before generation or publication, with no
default or replacement image. Postprocess still requires the matching
completed animation task.

The animation read additionally pins the active generation, exact portrait
asset, and prior raw/alpha/pixel result basis. The worker returns those
original values with the generated PNGs. The NPC-owned Filex writer
(`workflowRegenerateMotionV1`) stages the raw frames privately per exact
request/preset/direction; its first acknowledgment is not canonical
publication. PDS authorizes the matching postprocess to read just those
staged PNGs. After validation, Filex atomically commits the new raw,
alpha and pixel records and files for that one direction, removes any
obsolete frames from that slot, and cleans the committed staging files.
Existing results stay visible until their new direction is ready; an
entire request is not an all-directions transaction. Other NPCs, poses,
presets and source assets remain untouched. A newly selected empty native
slot is also eligible.

Deploy Request Service, Planner, PDS, the **NPC-owned** Filex writer,
Animation/Postprocess workers and Local/Space clients for this request
family together. Keep ordinary selected creation/replacement, older
static walk/run plans, and 2D motion-preset conversion unchanged.
