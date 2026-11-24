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

## Selecting, editing and adding one animation

Select a published package by exact `{propId,revision,sha256}`.
`prop.update` (`ngs.prop-update.v1`) takes the same project and prop ID,
`source:{revision,sha256}`, explicit `scope:"metadata"|"appearance"`, intent
and a current design pin. Metadata edits run only document and final proof.
Appearance edits regenerate one base PNG and its static lanes using the
pinned predecessor. Edits preserve the exact existing ordered animations.

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
