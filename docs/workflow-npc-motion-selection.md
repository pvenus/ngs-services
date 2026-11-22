# NPC motion preset selection

The NPC form offers the metadata for completed eight-direction motion
presets, including generated 2D presets. Opening or refreshing the picker
does not read, probe, or transfer the MP4 objects. The picker follows the
existing Local/Space catalog replica and refreshes its small list while
open. The noncombat and combat buttons only replace the editable draft
selection; the server does not supply a default or a substitute.

The new create and replace envelopes carry 1–8 explicit entries:
`{presetId,posePreset,assetSetSha256}`. The fingerprint is SHA-256 of the
compact JSON array `["ngs.motion-assets.v1", presetId, posePreset,
directionHashes]`, with the eight `[direction,sha256]` pairs in
n/ne/e/se/s/sw/w/nw order. It pins the source bytes for the same preset
ID; previews do not have to be downloaded to form the request.

Each selected preset has disjoint animation/postprocess lanes. Character
appearance still comes from the existing direction-specific portrait; the
motion source defines the action, including sword attack. Output metadata
uses its verified pose family and its exact chosen preset ID; two
variants cannot overwrite each other. The server rejects stale or
incomplete selected sources instead of falling back to walk/run. Old
already planned jobs retain their previous workflow.

This includes the NPC Filex writer's exact claim-bound media scope. Before
enabling the new NGS intake in production, the updated NPC Filex writer,
PDS/RS/Planner and owning workers have to be active. Updating only the
front-end is insufficient.
