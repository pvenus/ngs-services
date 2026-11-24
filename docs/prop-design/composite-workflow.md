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
