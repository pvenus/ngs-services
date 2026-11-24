# Filex: one canonical writer

Run exactly one Filex writer container and one HTTP listener. Filex storage
itself remains separate; this is the service that validates narrowly scoped
canonical operations. A folder must not get a separate writer process,
image, Compose service, host port, or copied implementation. PDS is the only
approved caller. It selects a fixed scope from server configuration.

Edit scopes.json to register another canonical child that uses an already
reviewed profile. Each entry declares immutable scope ID, folder, approved
credential and operation profile. Compose/OPS derive the distinct mount
closure from this array: only /canonical/<folder> is writable, never
/canonical or its parent. The four independent credential volumes are read
only; concept, audio and props deliberately share the existing trusted
concept credential but have different operation profiles. Register a truly
new operation policy in scope_registry.py and its domain implementation,
not in a new service, broad storage method or wildcard route. Unknown roots,
operations, methods, tokens, legacy bare paths and cross-scope authority
fail closed.

The sole public-on-loopback writer port is 8095 (container 8093). The
transport is /v1/scopes/<id>/<domain route suffix>; capability reads use
/v1/scopes/<id>/capabilities. Filex keeps the existing per-domain lock,
crash recovery, operation fences, and immutable assets. No domain store
gets access to another root merely because it is in the same process.
Audio uses the already-authoritative concept store to validate its sound
design input, not a shadow copy or independently started service.

Normal OPS cutover must validate the exact candidate and scoped capability
matrix and switch matching PDS clients together; never leave a second
writer running against the same root. Retire legacy writer processes and
old role source on verified cutover. Preserve existing canonical data,
durable receipts and recoverable rollback configuration. No move or cleanup
of canonical assets is part of this change.
