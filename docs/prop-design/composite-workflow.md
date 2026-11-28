# Formal prop creation and append-only animation

One official `prop.generate` request asks for one independent prop/package
and interaction behavior. It is not the historical `prop.image-generate`
request or a puzzle generator. Different props or separate behaviors get
separate packages; the puzzle chooses and links exact version pins.

Input `ngs.prop-generate.v1`: project `binari`, prop ID, author intent and
exact `designContextSha256` (current master visual design plus authored
prop-design section). Optional `motion: "still" | "subtle"` defaults to
still. CREATE also accepts one optional published reference
`{propId, revision, sha256}` (manifest SHA), distinct from the new target.
No reference leaves the original still flow unchanged. Regeneration does not
accept this field and continues to be a complete replacement of its own
selected target. The request accepts no number of frames, fixed state list, provider
selection, executable, filesystem path, or prompt override after approval.

Planner's real stage capability registry is `propStageRegistry` in
`workflow-planner/prop-generate-plan.mjs`. A document worker authors typed
design/behavior using the three canonical source inputs. An image worker
produces exactly one pinned base PNG. For reference CREATE, the base-stage
input records the exact pin in the existing image capability. PDS verifies
the same published source before and after reading the hashed detail PNG,
and the native model receives those actual bytes as an isolated image
reference along with the approved appearance direction. The worker fails
rather than substituting a different revision or falling back to text-only
generation. The source remains unchanged and the new target is published as
revision 1. Model reference conditioning is not pixel-identical copying.
If motion is requested, the distinct
animation worker derives RGB frames from that base, using the verified
document contract. This branch is rejected before registration/planning
when the motion capability is not provided. Postprocess normalizes the verified base to static RGBA detail and pixel PNGs.
An optional separate RGB motion branch derives synchronized APNG detail and
pixel from that exact same base; it never replaces the static base. Neither the image nor RGB animation is a playable package.

The final task reads every exact completed same-run member (the final
scalar dependency alone is insufficient). Expected leaf hashes equal the
renderable media pinned by the manifest; the full member lineage is bound
to the Filex publisher proof. The authenticated PDS/Filex publishing
adapter must verify actual member bytes and source provenance and hold the
same workflow claim through an immutable commit. Services do not treat
status rows or a hash-shaped text ref as publication. The parent settles
only when its independently injected trusted readback returns both the
committed receipt and matching manifest. Malformed or unavailable proof
cannot ACK completion. Exhausted same-run failures settle as failed while
downstream tasks stay unperformed.

The immutable manifest is `ngs.prop-package.v1`, result
`prop.package.<canonical-manifest-sha256>`, for an independently selected
`{propId,revision,sha256}` pin. Media has separate detail and pixel lanes,
same observed frame durations for animations, and independently typed pixel
open/detail activate hotspots. No puzzle coordinates, named candle states,
arbitrary scripts, or public paths are inserted into the package.

Static launchers use the normal readiness handshake described below.
This is a source release candidate, not a deployment or proof of live
model execution. The optional model is not verified in production. The
separate single-image workflow and existing demo are untouched.

## Selecting, regenerating and adding one animation

Select a published package by exact `{propId,revision,sha256}`.
`prop.regenerate` (`ngs.prop-regenerate.v1`) takes the same project, prop ID,
`source:{revision,sha256}`, replacement intent, a current design pin, and
optional subtle motion. It runs the full generation graph with a fresh
design, one base PNG and static lanes. No old animations are carried into
the new current package. The previous version stays available to already
pinned puzzles and remains the current version if regeneration fails.

`prop.animation.generate` (`ngs.prop-animation-generate.v1`) takes that
same exact source pin and one named `motionId` plus a human prompt. This is
one independent animation: no preset, no independent image generation, no
batch. The worker must read the pinned package's existing canonical detail
base. The new clip's detail/pixel animation lanes are synchronized RGBA,
with the base canvases preserved. Creation can optionally add one initial
clip (ID `initial`); no operation implicitly rerenders previous clips.
IDs match the package reader's slug constraints; empty, separator-containing,
or out-of-range IDs are rejected. Prompts are bounded to 2,000 UTF-8 bytes.

The canonical product layout is
`canonical-files/binari/props/<propId>/{metadata.json,releases/<revision>/manifest.json,assets/<sha256>.png}`.
Prepare the checked candidate under the real running claim, re-read the
pinned prior manifest, and require a writer CAS of the exact current prop
head (absent for create; the selected revision/hash for successors). A
concurrent advance is a stale selection, not permission to overwrite. The
previous manifest must remain retrievable immutably for independent
completion proof. The fixture verifies these checks but neither writes
live Filex packages nor implements the production publisher here.

### Staged real storage path

The Filex write bridge is the existing service-bearer handler, using a
dedicated owner-bound `props` root and the standard Filex exclusive lock
and atomic-write/fsync primitives. It independently checks the complete
manifest, same-packet member refs and digests, decoded RGBA and APNG timing,
and predecessor preservation for append. It never touches sibling prop
folders. Assets are content-addressed per prop; the immutable release
directory and receipt are sealed before the single atomic metadata head
change. A lost response can recover the exact prior commit; two competing
writes from the same head cannot both become current. Previously published
pinned versions are readable after the head advances. Unknown future
revisions and bytes not explicitly named by that pinned manifest are not
served.

PDS exposes the staged, default-off localhost
`/v1/props/ownership/workflow-package` plus `/head`, `/read`, and `/asset`.
No browser credentials are accepted by these internal worker endpoints.
The PDS composition must inject a trusted exact-running claim scope, a
stage-artifact resolver, and scoped pinned-read authority; callers cannot
supply a Filex path, member receipt, media body, or authorization flag.
This fixture exercises both real HTTP handlers and Sibling Filex storage
using audited fixture stage bytes/claim port. Production mount/provisioning,
the trusted real Planner-claim and stage-artifact ports, production
execution readiness, and authenticated NGS list/show remain explicit
separate gates.

### Claim-bound intermediate transport

The staged composition constructs `RealPropAuthority` from the existing
inode-pinned owner-private `PrivatePlannerClaim`, a private 0700
`PropStageStore`, the actual `PropDesignContext`, and the same narrow
authenticated Filex bridge as the final package. It is injected into both
the shared package and stage routes. The stage DB reader is SQLite mode=ro,
query-only and rollback-journal only. It rederives the exact current DAG
and task input, checks the running claim stamp and completed predecessor,
and holds a read snapshot across stage/file operations and final publish.
It does not update Planner or RS itself.

- Document: GET `/v1/props/ownership/workflow-design` (readiness); POST
  `/read {claim}` yields exact design context and bound task input; POST
  base `{claim,artifact}` stores the one typed design spec.
- Other stage workers: GET `/v1/props/ownership/workflow-stage`; POST
  `/read {claim}` obtains the stage-kind-derived document/media/geometry
  context; POST `/asset {claim,purpose,index}` returns only the bound
  upstream PNG/APNG or indexed RGB PNG. `purpose` is `input` or
  `geometry`, `index` is null outside raw frame retrieval.
- POST stage base `{claim,artifact}` commits one immutable stage:
  design JSON; `{media,base64}` for image and derived lanes;
  `{manifest,frames}` for ordered RGB. PDS derives lineage and exact receipt.
- `409 WORKFLOW_PROP_STAGE_INPUT_PENDING` is transient (no stage write)
  when same-run animation geometry has not finished. Executors must wait
  or use equivalent readiness gates, not mark this as a terminal failure.
  A source change 409 is separately identified.
- Final stages read all exact predecessor receipts, verify lineage, match
  projected title and hotspots to the design, and load raw new lane bytes
  only from the private store. The pinned package evidence endpoints
  require an exact completed Planner publish row and independent Filex
  verification. Production never creates storage implicitly on startup:
  provision and approve the exact dedicated writer mount and private PDS
  stage root first. No client field activates it.

Retest the exact authority with
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=data-service:filex-writer python3 -m unittest discover -s filex-writer -p 'test_prop*http.py'`.
The real stage fixture injects deterministic design context and PNG bytes,
not a production model, and drives genuine Node-created Planner state,
the real snapshot and both HTTP servers.

### Normal release and runtime capability

1. The unified Filex writer owns the configured canonical prop child mount
   and exposes the token-gated `props` scope. Reuse its existing protected
   setup; don't start a second writer or remount the whole canonical root.
2. The existing PDS package composition uses `PDS_WORKFLOW_PROP_STATIC=1`
   and the dedicated owner-private stage root together with the exact
   Planner authority. No caller can select or activate that setup.
3. The animated worker itself renews a private, local Wan TI2V Q8_0 lease
   only while its exact runtime verification succeeds. The default path is
   `~/Workspaces/Workers/runtime/prop-animation/prop-animation-ready.json`.
   Its parent must be an owner-only directory and the file must be a
   single-link, owner-only 0600 regular file. PDS checks the exact v1 schema,
   provider identity, and nonfuture timestamp no older than 120 seconds.
   Missing, stale or malformed proof leaves only `["still"]` enabled.
4. The exact readiness response remains `workflow.prop-package-write.v1`;
   a current verified worker adds `"append-animation"` after `"still"`.
   RS advertises and registers only that individual animation operation,
   and pauses its registered handoff if readiness is lost. Static create
   and regenerate remain independent; `motion:"subtle"` is still not
   admitted by the standard launcher. A later readiness lapse does not
   make already-staged bytes unverifiable, so admitted runs can complete
   valid CPU derivatives and publication.
5. Postprocess obtains the claim-bound manifest from `/prepare` for both
   static and append. Append pins the current revision, carries over every
   prior field, base lane and existing clip, then appends the two verified
   new media lanes. A conflicting head is stale. The same prior append can
   replay only when the exact immutable release already exists.

Use normal GitHub release evidence and ordinary OPS targets, never a
separate deployment script. Fixtures verify readiness denials, real
claim-bound paths, CAS and readback with no model invocation.

#### Candle chroma/headroom append profile

The existing `prop.animation.generate` request can optionally name
`renderProfile: "candle-chroma-headroom-v1"` for the exact prop
`candle-brass-petal-unlit-clean`. No other prop, profile or raw renderer
configuration is accepted. The field changes the request hash, not the task
graph, task input, geometry/source pin, Filex manifest or publication API.
Without the field the existing append behavior and wire shapes are unchanged.

The scoped PDS claim reads expose the profile only to the raw motion and
animated-detail steps. The exact unmodified detail PNG already supplies the
upstream transparency and fixed-object silhouette; animated detail reads it
through its existing geometry asset route. Finish and publish through the
normal append flow, preserving the static PNGs and all prior animations.
Enable append only with the reviewed matching Animation and Postprocess
workers; this is not a separate request lane or a direct Filex write.

### Removing selected animations

`prop.animation.remove` is a file-worker request accepting a prop ID, its
current revision and SHA-256 pin, and 1–30 distinct motion IDs. Filex publishes
a new immutable revision with just those clips omitted, retaining the base
images, design, and interactions. A conflicting revision returns a conflict
instead of overwriting newer work. Prior revisions and their files are not
deleted. Further prop work must use the newly published head and its pin.

### Spirit presentation

`prop.generate` and `prop.regenerate` may include the exact optional field
`presentationProfile: "spirit-v1"`. Without it, the historical workflow and
pixel canvas policy are unchanged. With it, `prop_pixel` and `prop_spirit`
both read the pinned RGBA detail asset and use the canvas
`round(detailWidth / 5) × round(detailHeight / 5)`. The spirit stage is an
existing postprocess-worker operation (`prop.generate.spirit` or
`prop.regenerate.spirit`) with a static `prop.spirit-rgba.<sha256>` receipt.
Publication waits for all required lanes and verifies each descriptor against
the corresponding completed task and actual file bytes.

The package remains `ngs.prop-package.v1`; static `media` carries
`detail`, `pixel`, and `spirit`, while animation clips still carry
`detail` and `pixel` only. Animation append/remove preserves the entire
static media object. Filex asset lookup and replica enumeration include
`spirit` via the same immutable media descriptors as the original lanes.

### Pixel reference generation (`spirit-v2`)

New `prop.generate` and `prop.regenerate` requests can opt into
`presentationProfile: "spirit-v2"`. Earlier requests are unaffected.

- `prop_detail` remains the cinematic original with transparency.
- `prop_pixel_source` uses that exact PNG through the existing claim-bound
  image-stage asset port. It renders a pixel-art source on the same canvas.
- `prop_pixel_detail` normalizes transparency on the pixel-art source.
- `prop_pixel` resizes and corrects that normalized pixel art at dimensions
  rounded to one-fifth of the cinematic canvas. It is not a downsample of
  the cinematic image. `prop_spirit` independently uses the cinematic original
  at the same reduced dimensions.

The two intermediate images stay in staging, not in the canonical package.
The existing immutable publication receipt names all stage outputs, and the
publisher verifies the intermediate bytes and their lineage before exposing
the final detail, pixel, and spirit files. Neither the package schema nor the
two-lane animation output contract changes.

### Prop placement image sizes

An optional `scalePercents` array on Spirit-v2 `prop.generate` requests one or more
placement sizes in the same original generation. Supported percentages are
5, 10, 20, 30, 40, 50, 60, 70, 80, 90 and 100. Duplicate or empty selections
are invalid. Omission retains the existing package and playback defaults.
For each requested percentage the publisher records a still pixel and spirit
PNG in ascending `scaleVariants` order. Their dimensions are derived from
the final detail canvas by rounding each dimension to the nearest integer
and clamping to one pixel. The popup detail and existing image slots are
unchanged. Spirit-v2 requests which select scales retain a full pixel art
basis (`scaleSourcePixel`, not a selectable image).

Issue a separate `prop.scale.generate` request to add sizes to an existing
prop. Its complete payload is
`{schemaVersion:"ngs.prop-scale-generate.v1",projectId:"binari",propId,source:{revision,sha256},scalePercents:[...]}`.
The planner schedules exactly one postprocess publication, without image
or document generation. PDS reads and verifies the pinned source, resizes
its pixel and cinematic images, and publishes one immutable successor via
Filex compare-and-swap. A stale head or already-published size is rejected;
previous pins, assets, and animation remain readable. Legacy packages
without a full-size pixel basis are resampled from their original pixel
slot, so larger output changes dimensions but does not add drawn detail.
