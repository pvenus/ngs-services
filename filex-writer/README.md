# Canonical knowledge writer (not deployed)

Dedicated owner-authenticated HTTP sidecar for PDS. Workers never need this mount.
Only `canonical-files/binari/knowledge/overview.json`, `categories/<categoryId>/metadata.json`, and
`categories/<categoryId>/entries/<entryId>.json` are accessible. The mounted directory is that exact knowledge
root, not the Filex volume root. Complete replacement of one file per request;
no versions, content backup, restoration, automatic migration or deletion API.

Authorization: `Bearer <dedicated token>` on every route, token file >=32 chars.
Never reuse the Filex login password or expose this endpoint to the public Internet.
PDS owns caller/operation authorization and Laya gating. Possession of this secret
is scoped write authority, not proof that an arbitrary user is the owner.

- GET `/v1/capabilities`: atomicCAS true (single-file only)
- GET `/v1/object?key=...`: exact bytes
- GET `/v1/snapshot`: documents keyed by relative path, hashes SHA256 of bytes,
  sourceVersion/manifestHash SHA256 of sorted compact JSON hashes map
- POST `/v1/cas`: key, expectedBase64 (null for absent), base64, requestId,
  inputHash, expectedSourceVersion. Returns requestId/inputHash/key/afterHash/status.
- GET `/v1/requests/<requestId>`: saved receipt/recovery. Encode ID as URL-safe value.
- 401 unauthenticated, 400 invalid, 404 absent, 409 stale/conflict, 503 disk failure.

Exclusive advisory flock covers snapshot, compare and replace. Full temporary
file is fsynced before rename; parent directory is fsynced after rename. Failure
before rename leaves original intact. Failure after rename may mean committed
but acknowledgement lost: query receipt, do not issue a new request ID. Metadata
journal stores only hashes and IDs, not old contents. Prepared receipts reconcile
against current afterHash. Whole catalog transactions are NOT atomic.

**Deployment prerequisites:** review compose; provision dedicated secret outside
Git; grant matching non-root UID/GID access only to canonical knowledge directory;
verify underlying local disk supports flock/rename/fsync. No NFS guarantee. Ensure
Filex and all other processes cannot independently write this canonical namespace:
advisory locks do not constrain non-cooperating writers. Existing Filex service
is untouched by this example. Do not claim global CAS until its canonical write
access is excluded. Configure Filex read access to the same root. No production
mount, permissions, Docker network, credentials or service changed by these files.

`compose.example.yaml` binds host loopback 8093, internal Docker network, read-only
image, no privileges/caps, exact data mount. Remote PDS needs a separately approved
authenticated private/TLS channel; do not change bind to public as a shortcut.
Base image build/pull and live Docker test remain unperformed.

Isolated tests: `cd filex-writer && python3 -B -m unittest -v`.

## NPC batch mode (isolated implementation, not deployed)
Separate process/mount with `WRITER_SCOPE=npc` and root exactly `npcs/`.
Routes retain owner authentication. Knowledge CAS routes and digest semantics are
unchanged. NPC uses `/v1/batch` with `{requestId,inputHash,files,readSet}`. Each file
is `{path,body,expectedHash}`; paths are full canonical NPC keys. Read-set and
snapshot hashes use sorted compact UTF-8 JSON with ensure_ascii=false/allow_nan=false.
The read set includes unchanged dependencies and every write basis.

Global lock prevents cooperating PDS readers seeing a partial multi-file update.
A durable journal references staged NEW bytes; on restart every read first
rolls forward the interrupted transaction. No old contents are retained for edits.
Staging is removed after commit; receipts retain hashes/status only. This is
NOT an atomic directory swap and NOT safe for uncoordinated Filex/direct readers.
Unexpected external edits during recovery block rather than overwrite.
NPC whole-folder deletion/backup endpoint is not implemented by batch: omitted
files are never implicitly deleted. Separate verified deletion contract required.
