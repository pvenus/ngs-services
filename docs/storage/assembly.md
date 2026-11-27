# Binari assembly metadata

NGS submits a Local official `assembly.generate` or `assembly.update`.
The existing Planner stages one `assembly_metadata` document-worker task.
On generation, the worker reads the PDS-authorized input and calls Codex CLI
once to produce station and attachment positions. It posts the generated
coordinates under the same claim. PDS rechecks the fixed title, puzzle IDs,
puzzle type, map and prop pins, and return reachability before committing.
Version 2 generates one to eight ordered puzzle IDs in the same official
request and CLI call. It takes overall instructions and a short instruction
for each puzzle, which are authoring inputs and not persisted as layout metadata.
An invalid or failed CLI output does not publish the input suggestion.
A manually edited update remains exact and does not invoke the CLI.

Generation selects the newest published static assets for the ritual altar,
water bowl (ash-catcher fallback only), well and well cover. Each pin records an
actual immutable prop revision and manifest SHA-256. Existing maps and props
are never changed. To keep the test playable from the map start, the input suggestion
can place the altar and well in the same region when the directed portal graph
lacks a return path. Manual edits retain the exact map pin, reject unreachable
stations and missing prop bytes, and may remove every puzzle without removing
the assembly. Older intact immutable prop releases remain eligible. A manual update can
persist a puzzle's optional `scale` object with all four role percentages
(`table`, `bowl`, `well`, `lid`) in the integer range 10–300. If absent, the
client displays each prop at 100%. Generation does not author scale and
metadata changes never resample the prop files.

The canonical location is
`binari/cinematics/assemblies/<assemblyId>/<revision>.json` with
`revision.json` pointing at the published head. Files are sorted, compact
UTF-8 JSON terminated by a LF. Filex writes the head last under the existing
cinematic store lock. Prior revisions are immutable; a competing update
with a stale expected revision is rejected. Completed Planner tasks must
independently read back their exact official immutable revision through PDS.

The PDS loopback workflow uses
`/v1/assemblies/ownership/workflow-assembly`, with readiness
`workflow.assembly.v1`. The browser reads the replica rather than acquiring
worker claims or direct Filex permissions.

Map revisions in older or new assembly metadata record publication provenance. Editing an existing assembly validates placement against the latest complete published graph for its map ID. Play clients resolve by map ID, not by that recorded revision, so a portal update does not rewrite assembly data.

An assembly's whole folder can be retired through the existing single-target
`file.delete` workflow. The target root is
`canonical-files/binari/cinematics/assemblies`, the target path is one
assembly ID, and the expected pin is the currently published
`{revision,officialRequestId}`. Filex checks the pin under the same lock as
publication before quarantining that one folder. A durable retirement marker
prevents delayed generation or updates from recreating it. Neither its
linked map nor any pinned prop is deleted.

Manual edits may also add a `binari.ritual-spirit-manifestation` puzzle
through the existing `file.update` target. Each such puzzle has one reachable
`layout.spirit` station, one exact `bindings.spirit` prop pin, empty
`attachments`, and optionally `scale.spirit` (10–300%). A new or changed
binding must have both its pixel and reduced spirit assets at the pinned
revision. It cannot carry the well puzzle's portal conditions. Initial
assembly generation and already-queued legacy well requests remain unchanged.
