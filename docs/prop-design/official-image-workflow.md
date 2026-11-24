# Official prop image workflow (staging)

The shared Request Service accepts type `prop.image-generate`, schema
`ngs.prop-image-generate.v1`. Exact payload fields are `schemaVersion`,
`projectId: "binari"`, `propId`, `state`, `lane: "detail" | "pixel"`,
`intent`, and `designContextSha256`. The digest identifies the current
ordered PDS canonical visual and prop design sources, not a constant
or user-supplied prompt override. A caller preparing an official request
must obtain the approved current digest before intake. The same request
envelope applies to Local and the verified Space queue, not separate variants.

The Planner binds exactly one `prop_image_generate` image task to an
immutable canonical payload. The existing image-worker entrypoint claims it
only after its typed PDS capability and the pinned renderer are ready.
It reads exact canonical visual style/prohibitions and active prop Markdown,
verifies the requested digest, and passes visual guidance, prop guidance,
and explicit intent to the existing native image provider in that order.
This produces one isolated PNG, not a prop pack or an NPC portrait.

PDS revalidates the exact running claim and current design pins, checks
actual alpha samples (not just an alpha-bearing header), publishes through
the existing authenticated Filex media writer and reads back the immutable
receipt and bytes. The relative result reference is
`media-artifacts/assets/<sha256>.png`; full canonical storage is
`canonical-files/binari/media-artifacts/assets/<sha256>.png`. The receipt
binds the official request, claim attempt, artifact and design digest.
Normal project replica sync is used, with no overlay or alternate storage.
Ambiguous publication/commit requires reconciliation, not another render.

Standard OPS source deployment requires no new runtime flags. PDS registers the
same-host capability only with the existing canonical Filex Concept source,
private Planner pin and existing scoped Filex Media gateway. RS advertises and
accepts prop requests only when that exact typed read-only PDS probe succeeds.
A temporary PDS outage does not hide accepted requests or block completion and
cancellation. Verify all four ordinary roles (PDS, Image, Planner, RS) after
deployment before sending new work. NGS UI activation remains separate.

Disposable verification (set the Workers candidate path):
```sh
PROP_IMAGE_EXECUTION_WORKER_MODULE=/absolute/path/workflow-production/image/prop-image-worker.mjs \
  node --test tests/prop-image-workflow.test.mjs
```
This uses actual Request Service/Planner/Image task code, PDS HTTP, the
concept writer, and the existing media Filex HTTP writer. Only the image
provider is replaced with a recording synthetic renderer. It does not
send a production request, invoke a real model, or change live Filex.
