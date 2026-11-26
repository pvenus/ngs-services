# Full-scene cinematic I2V

Sources are **project-owned static PNGs** imported through the trusted Filex owner workflow as
`canonical-files/binari/cinematics/sources/<PNG SHA-256>.png` (≤8 MiB). No external URL,
filesystem path, background-removal policy, worker option, or second source is accepted.

The canonical scope registry adds only `cinematics`, with its own mount/routing and the existing
media binding. The owner-controlled canonical directory must be provisioned before OPS applies
the new writer/PDS pair; Docker does not create host paths. A deployment lacking that scope or a fresh,
private cinematic animation worker proof does not advertise cinematic intake. Both formal I2V
lanes (props and cinematics) require their respective, independent runtime marker for
`wan-i2v-a14b-q4_k_m`; neither can reopen on a 5B marker. Install compatible A14B weights,
verify the same-model resident and smoke-test it in staging before deploying this strict PDS
gate with the new worker. This change is not needed on the existing live 5B release; do not
deploy it ahead of the coordinated cutover. Existing NPC Animate is unaffected.

`cinematic.generate` accepts `ngs.cinematic-generate.v1` with `projectId`, `sourceSha256`, and
`motionPrompt`. Local or owner-verified Space intake checks the source and canonicalizes the request.
Planner registers one `cinematic_render` (`cinematic.generate.render`, existing animation worker).
This route deliberately contains no postprocess/background-removal task: the renderer produces a
full-scene silent H.264 MP4 (45 frames, 24 fps).

The worker fetches only the exact PNG authorized by its active Planner claim. PDS holds a read
transaction for publication and requests the fixed Filex scoped writer. Filex atomically writes
the MP4, reads it back, then publishes an immutable JSON sidecar. Idempotent replay requires the
same source and output bytes; uncertain outcomes are not authoritatively completed by the worker.
Planner independently rereads the result and exact graph before advancing the official request
to completed. Standard project synchronization, not the private worker API, supplies the two
published files to NGS Local/Space.

`canonical-files/binari/cinematics/results/<officialRequestId>.{mp4,json}`
uses `ngs.cinematic-result.v1` with the exact official ID, project ID, source/output digests,
byte count, MIME, width, height, FPS, and frame count. UI should only show playback when both
replica files are present and the video matches the sidecar.

## Native PNG sequences (new requests)

The Local/Space authoring UI now submits `cinematic.sequence.generate` with
`ngs.cinematic-sequence-generate.v1`: the same immutable source SHA and motion
prompt, plus `playback`. The ordinary official Planner has one animation task.
The worker sends no MP4 and performs no masking, interpolation, or encoder
conversion. Each of the 45 native RGB PNG frames is verified separately; the
hidden stage is bounded (4 MiB per PNG, 64 MiB total) and made visible via one
directory rename only after the whole digest-pinned manifest verifies.
`cinematics/sequences/<officialRequestId>/manifest.json` is
`ngs.cinematic-sequence-result.v1`; its frame descriptors index the exact bytes
under `frames/0000.png` through `frames/0044.png`. The 24 fps native rate is
independent of playback. The legacy MP4 scan/read and already-registered
jobs remain supported; production Request Service no longer admits new MP4 jobs.

Both speed and camera curves use integer presentation frames from 0 (maximum
duration 1440 frames). Rates 0–4 and normalized camera focus plus 1–3 zoom
are authored values. The first point is at 0 and successive points strictly
ascend within the duration. Each curve has at most 128 points. This metadata is
passed with the official request, not interpreted by the generation model.

`cinematic.playback.update` applies a checked revision to an already committed
sequence without a GPU or any image writes. The update accepts exactly the
sequence ID, expected revision, and validated playback. The immutable
manifest is revision 0 until the first edit. The canonical mutable JSON
`timeline.json` is `ngs.cinematic-playback-state.v1` and increments once per
approved edit. Already-committed idempotent retries read their immutable
receipt, and stale/competing edits return an explicit conflict. A downloaded
JSON file edited by the user is submitted via the same typed request rather
than written directly to canonical storage.

`cinematic.source.generate` is a separate official image-worker request that
accepts a title and prompt, publishes a 1536×1024 PNG at
`cinematics/sources/<sha256>.png`, and publishes the title and provenance in a
sibling JSON. It does not queue a cinematic sequence; the author selects a
source explicitly when submitting the animation request.

`cinematic.sequences.remove` is a scoped file-worker request and accepts up to
30 distinct committed sequence IDs. It updates the catalog exclusion index
`cinematics/sequence-archive.json`; it does not delete published frames,
manifests, timeline metadata, or legacy MP4 files. All normal catalog views
must honor the archive index.

### Background animation

`background.animation.workflow` admits one or both pinned published stills of a background. The corrected pixel image is the group anchor for paired results; the cinematic reference must match the immutable original publication derived from the same background-generation request. Intermediate pixel generation is not an animation input. The parent request produces independently claimable cinematic and/or pixel tasks on the existing animation lane, both with identical motion and playback metadata. PDS reads exact image bytes and each manifest records the parent, source variant and group anchor. The parent is completed only after every selected result passes publication readback. The single resident renderer on a host does not load a second model to run both.

Background animation postprocessing uses required SeedVR2 7B FP16 video upscaling at exact 2× dimensions on the existing animation worker. The unscaled native Wan sequence is temporary inside that worker and is never sent to PDS/Filex. The emitted 45 RGB PNGs and manifest are the only published result. The manifest records the model, scale and exact source dimensions, and preserves the request's playback metadata. Existing sequences are read-compatible; regular cinematic requests are unchanged.

For the pixel variant only, the same claim runs a deterministic full-canvas shared-palette correction after validated SeedVR2 and before any frame staging. The ordered SeedVR2 frame digest and correction method are recorded under upscale.pixelCorrection; PDS refuses new pixel publications lacking this proof. The palette is the pinned pixel still palette, with no dithering or resize. The cinematic variant bypasses this stage, and legacy manifests remain readable.

New background animation intake (v2) is composition only. It supplies the background-specific prompt and pinned source(s) to the same media operations: render, upscale, palette correction (pixel branch only), and publish. Both source branches are independent. Each operation is a durable OPS task, linked to its preceding operation by task ID. The existing animation resident owns the GPU slots and its protected temporary checkpoint storage; only the final publish task can stage or publish to Filex. Predecessor frames are consumed by verified digest and removed after their successor succeeds. Prior v1 jobs and result IDs are read-compatible.
