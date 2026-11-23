# NPC motion preset selection

The NPC form offers the metadata for completed eight-direction motion
presets, including generated 2D presets. Opening or refreshing the picker
does not read, probe, or transfer the MP4 objects. The picker follows the
existing Local/Space catalog replica and refreshes its small list while
open. The noncombat and combat buttons only replace the editable draft
selection; the server does not supply a default or a substitute.

New calls use `ngs.npc-one-stop.selection.v2` and
`ngs.npc-one-stop.selection.replace.v2`. Previously admitted v1 requests keep
their original graph and bindings; do not replan or migrate those live requests. Their exact top-level fields are
`schemaVersion`, `projectId`, `intent`, `character`, `sourceRefs` and
`motionSelection`. Select one or more published presets. Both request
families enforce a 64 KiB canonical payload limit with no fixed preset count.
Each selected record is
`{presetId,posePreset,assetSetSha256}`, where pose is `idle` or `combat`.
The fingerprint is SHA-256 of compact UTF-8 JSON
`["ngs.motion-assets.v1",presetId,posePreset,[[direction,sha256],...]]`
in order `n,ne,e,se,s,sw,w,nw`. IDs are path-safe and unique ignoring case.

PDS only accepts an exact running Planner claim. The selected animation
or frame task key ends with `_motion_<slot>_<direction>`, where slot is
the first 16 lowercase hex digits of SHA-256 of the exact UTF-8 preset ID.
## Portrait dependency contract

When registering a motion preset, enter its explicit `posePreset` (portrait
key) in the registered preset record. `idle` and `combat` are the supported
keys today. The preset name, label, and motion ID do not decide the portrait.
A 2D conversion inherits the exact source key. The selected hash pins that
key together with the eight directions, so changing the key is a content
change, not a label edit.

The Planner registers one recipe per supported key (`motionPoseRegistry` in
the exact shared envelope). Full v2 starts with the standard idle recipe and
adds the union of keys selected in the motion presets, once per key:
pose planning → raw center → corrected pixel center → each directional raw
portrait → corresponding directional corrected pixel. The common portrait
art and representative planning are upstream of each pose plan. Multiple
motions using the same key reuse the same nine portraits; an idle-only
selection does not generate combat. Adding a new key requires an explicit
recipe with planning/image/publication support, never a special case keyed
on a motion ID.

Each v2 animation waits for the completed corrected pixel task of its
own key and direction, then reads the corresponding **raw directional
portrait** for the animation model. The corrected result and raw source
must agree on pose, direction, and lineage. Frame postprocess binds to
that exact animation and input lineage. Missing or stale required assets,
mismatched keys/directions, or an unregistered key do not fall back to
idle. The older v1 graph retains the prior idle source solely for already
admitted jobs.

Selected scoped animation reads include `posePreset`, `assetSetSha256`,
`referenceSha256` and `referencePresetHash` in addition to the existing
motion, direction and portrait ID. Workers verify the exact reference and
full preset hash on the existing private motion download. Selected scoped
frame reads include the selected pose and fingerprint and the exact
canonical raw sequence. Existing stage identities are preserved for historical graphs; v2 uses
registered pose branch stage identities and exact private claims.

For selected replacement, both NPC deletion entry points prove all picks
against one authenticated live motion catalog snapshot immediately before
the existing NPC delete invocation. A mismatch refuses without an NPC
write. Existing committed recoverable-delete receipts remain replayable and
require their original exact binding. The two writers do not share a
transaction: if the source changes after the delete preflight, the selected
media stage refuses the changed asset set rather than substituting it.

An existing owned legacy `character.*` NPC can also be replaced. Its
exact lower-case ID and matching suffix slug are kept, with no alias into
`npc.*`; new NPC registrations use `npc.*`. The legacy replacement preset
requires the exact same-run completed delete task, and the NPC-owned writer
checks the corresponding committed deletion receipt against the old
generation before creating a fresh empty preset under that same ID.

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
`npc.animation.regenerate` and schema `ngs.npc-animation.regenerate.v2`.
An already admitted v1 request keeps its original source binding.
Exact fields are `{schemaVersion,projectId,npcId,motionSelection}`; the
same preset fingerprint contract applies. The request can target an
existing active owned `npc.*` or `character.*` generation. Request Service
serializes animation regeneration and whole-NPC generation or replacement
for the same NPC while the request is active. Different NPCs do not block
one another.

The Planner creates only eight direction animation tasks and their exact
postprocess tasks per selected preset; it does not delete the NPC, rewrite
planning, or request new portraits. The animation roots have no same-run
portrait task: PDS/Filex verify the existing published raw portrait for
the exact selected key and direction. Missing or changed source refs
reject the affected lane before generation or publication, with no default
or new portrait generation. Postprocess still requires the matching
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

Deploy Request Service, Planner, PDS, the **NPC-owned** Filex writer and
affected workers before clients can submit v2. Check PDS/Writer pose support
and exact claims with a synthetic fixture before opening replacement intake:
delete is the first step. Update the Local/Space client(s) only after backend
readiness. Old requests and recorded v1 task identities remain unchanged.
The concept-only Filex writer is not a substitute for the NPC-owned writer.

The Planner settles only this regeneration family as failed if the exact
persisted graph contains a definitive failed task with all three retries
exhausted, and every other task is completed, likewise exhausted, or
pending behind an exhausted animation predecessor. A retry cooldown,
runnable lane, or running claim (including an unknown publication outcome)
prevents this transition. The Planner uses the existing Request Service
status API, then records the acknowledged failure locally; original task
history is retained. A new explicit request for this NPC can be submitted
after the official failure. Already committed directions are not rolled back,
and no automatic new request is created.
