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
`PDS_WORKFLOW_MOTION_2D=0`. The unchanged image capability is
`GET /v1/motion-presets/workflow-convert-2d` and exact receipt
`{enabled:true,schemaVersion:"workflow.motion-preset-convert-2d.v1"}`.
Animation and postprocess require `GET <base>/frames` and exact receipt
`{enabled:true,schemaVersion:"workflow.motion-preset-convert-2d.frames.v1"}`.
The new release does not execute the retired combined clip tasks; finish
or settle all already admitted combined conversions before cutover. Their
published catalog results and worker history remain readable.

The first claimed image read pins the existing source preset hash, which
commits the eight reference hashes and timing, and the formal payload.
Each direction has its own lock and immutable binary PNG bundle and MP4.
The shared image, basis and intermediates are owner-private under the
official request ID, never exposed to workers as paths.

- `POST <base>/read`, JSON `{claim}`: image/finalize return
  `{sourcePresetHash}`; Animation frames get the existing
  `{direction,sourcePresetHash,reference,image}`, with exact source MP4
  and shared-image hashes and bytes.
- `POST <base>/frames`, raw `application/octet-stream`: four-byte
  unsigned big-endian JSON header length, UTF-8
  `{claim,frames:[{bytes,sha256}]}`, then ordered concatenated PNG bytes.
  It accepts only the exact active Animation frames claim and completed
  shared-image receipt, source frame count, equal validated PNG geometry,
  exact input lengths and individual SHA-256s.
- `POST <base>/frames/read`, JSON `{claim}`: only exact Postprocess
  encode can read its completed sibling frames receipt. The response is
  binary with the same framing and ordered bytes, but the header is
  `{direction,sourcePresetHash,fps,width,height,frames:[{bytes,sha256}]}`.
  Its receipt is `motion.2d.frames.<direction>.<sha256>`, computed over
  compact UTF-8 JSON with recursively sorted keys for
  `{direction,sourcePresetHash,width,height,frames}`. Array order is
  preserved; `fps` is omitted because the exact source hash binds it.
  Limits: 128 KiB header, 1,000 ordered PNGs, 8 MiB per PNG and 64 MiB
  aggregate PNG bytes; the source MP4 keeps its separate 64 MiB limit.
- `POST <base>`, JSON `{claim,artifact}`: existing image format
  `{base64,sha256}` is unchanged. Postprocess encode uses that same
  artifact shape for its MP4, independently probed for exact source timing
  and staged PNG geometry, bounded to 8 MiB. Finalize accepts only
  `artifact:{}` and all eight exact completed encode dependencies.
  Each write acknowledges only `{ok:true,resultRef}`.

Replay with differing bytes is rejected. Planner reads are detached before
media probes, network I/O or waiting for locks. Only a fresh exact claim and
predecessor check fence atomic local rename or the short Filex catalog CAS.

Finalization rechecks all eight exact completed direction receipts against
their bytes and publishes one separate document through the existing
Filex motion writer's batch lock, requiring the original source hash
and absence of the target in its CAS read set. It verifies all eight
content-addressed assets, the committed receipt and the indexed result.
Orphaned content-addressed uploads after a conflicting CAS are not a
published variant. No intermediate deletion or source overwrite occurs.
Existing readers receive optional
`variant:{kind:"2d",sourcePresetId,sourcePresetHash}` on 2D documents.
