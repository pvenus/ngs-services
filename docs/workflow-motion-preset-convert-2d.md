# Claim-bound 3D motion preset to separate 2D preset

The input is a formal `motion-preset.convert-2d` request with canonical
`ngs.motion-preset-convert-2d.v1` payload. PDS reads the original current
3D preset through the existing Filex motion authority. The workflow does
not grant a worker access to Filex credentials, general registration, or
local file paths.

The existing PDS startup discovers the registered private Planner DB,
private loopback Filex motion writer (port 8096) and fixed Homebrew
`/opt/homebrew/bin/ffprobe`, resolving and validating its local binary.
No new public route, writer version, credential or launch environment
setting is required on that configured Mac. If any prerequisite is absent
the lane stays unavailable. An operator can explicitly disable it with
`PDS_WORKFLOW_MOTION_2D=0`. Capability is
`GET /v1/motion-presets/workflow-convert-2d`; only the exact
`workflow.motion-preset-convert-2d.v1` enabled receipt is usable.

The first claimed image read pins the existing source preset hash, which
commits the eight reference hashes and timing, and the formal payload.
The PNG, eight directional MP4s, and basis are immutable intermediates in
an owner-private PDS stage directory keyed by official request ID. The
workers get no stage paths. Each write verifies the exact running Planner
claim, source pin and upstream task/result references. A clip additionally
requires its own original hashed MP4 and the one shared image. Clip timing
(frame count, fps, duration) is probed against the source and any actual
output dimensions are recorded. Replay with differing bytes is rejected.

Finalization rechecks all eight exact completed direction receipts against
their bytes and publishes one separate document through the existing
Filex motion writer's batch lock, requiring the original source hash
and absence of the target in its CAS read set. It verifies all eight
content-addressed assets, the committed receipt and the indexed result.
Orphaned content-addressed uploads after a conflicting CAS are not a
published variant. No intermediate deletion or source overwrite occurs.
Existing readers receive optional
`variant:{kind:"2d",sourcePresetId,sourcePresetHash}` on 2D documents.
