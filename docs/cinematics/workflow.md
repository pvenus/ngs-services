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
