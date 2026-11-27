# Complete metadata replacement
`file.update` carries one known metadata target, the exact revision and producing request ID, and the complete replacement content. It is not a raw-path API and cannot write arbitrary JSON to Filex. The first registered target is the map metadata group. New targets must add a type-specific validation and storage adapter rather than a request type or service. Legacy queued map.update tasks still complete without migration.

The one file task executes without Codex. PDS verifies the durable workflow claim, input digest and background identities. Filex compares the exact prior version and publishing request under its existing lock, writes sidecars first, and exposes the numeric group manifest last. Readers verify all hashes before observing the new revision. A rejected stale edit does not overwrite the new data. The result readback checks the request and complete metadata before workflow completion.


The assembly adapter targets one existing assembly ID under
`canonical-files/binari/cinematics/assemblies` with `kind: "metadata"`.
Its complete editable document is `{title,mapId,puzzles}`. The title and
map ID are immutable; the original map revision and publisher remain stored
as provenance. The current map controls placement and portal validation.
Each puzzle may carry up to 16 unique `portalConditions` entries:
`{portalId,propRole:"bowl",requiredState}`. Valid states are `altar-empty`,
`carried-empty`, `carried-filled`, and `altar-filled`. Added or modified
conditions must target a current portal. An unchanged condition survives
an unrelated edit even if its original portal has since been removed.
Publication and revision CAS use the existing Filex assembly lock and do
not change a bound map or prop.
