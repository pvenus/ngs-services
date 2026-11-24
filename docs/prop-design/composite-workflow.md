# Formal prop creation (staged, not an operational worker)

One official `prop.generate` request asks for one independent prop/package
and interaction behavior. It is not the historical `prop.image-generate`
request or a puzzle generator. Different props or separate behaviors get
separate packages; the puzzle chooses and links exact version pins.

Input `ngs.prop-generate.v1`: project `binari`, prop ID, author intent and
exact `designContextSha256` (current master visual design plus authored
prop-design section). Optional `motion: "still" | "subtle"` defaults to
still. The request accepts no number of frames, fixed state list, provider
selection, executable, filesystem path, or prompt override after approval.

Planner's real stage capability registry is `propStageRegistry` in
`workflow-planner/prop-generate-plan.mjs`. A document worker authors typed
design/behavior using the three canonical source inputs. An image worker
produces exactly one pinned base PNG. If motion is requested, the distinct
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

In this change the real RS and Planner are exercised through an isolated
MCP fixture; only fixture constructors enable the type and inject a test
proof reader. Production service CLI, PDS/worker selectors, actual
end-to-end package execution and puzzle result browsing are not enabled
here. The optional model is not verified in production. The separate
single-image workflow and existing demo are untouched.

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
  verification. Production does not create a staging root, mount props,
  configure the adapter, or enable the dormant routes from a client/env
  override in this change.

Retest the exact authority with
`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=data-service:filex-writer python3 -m unittest discover -s filex-writer -p 'test_prop*http.py'`.
The real stage fixture injects deterministic design context and PNG bytes,
not a production model, and drives genuine Node-created Planner state,
the real snapshot and both HTTP servers.
