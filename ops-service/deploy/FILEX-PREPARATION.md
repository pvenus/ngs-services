# Filex Writer candidate preparation (not deployment)

The OPS candidate detector leaves Filex inactive by default. To provision this
specific prepare-only path, the separately reviewed runtime installer must pin
the exact `filex_writer_apply.py` and `filex_writer_active.py` together in the
private helper manifest. The fixed helper set also includes
`prepare_filex_writer.py` and `prepare_filex_supervised.py`. Never load
candidate-supplied Python or invoke the native writer apply CLI from the
detector. Installing the reviewed startup bridge, registering the Docker
runtime and allowing a manual apply are separate operations.

The owner-owned mode-0600 control file `filex-writer-preparation.json` is an
explicit opt-in. Its exact keys are:

- `schemaVersion`: `ops.filex-writer-preparation.v1`.
- `base`: a `python:3.12-slim@sha256:<64 lowercase hex>` image already cached
  on the dedicated Filex Docker daemon for `linux/arm64`.
- `runnerSha256`, `composeSha256`, `startupSha256`: independently reviewed
  SHA-256 pins for the fixed manual runner, exact compose and registered
  startup bridge.

The Filex Actions workflow and selected artifact are independent from the main
service-targets workflow. The detector uses the existing read-only Keychain
item only on the fixed canonical Filex Actions routes, and never forwards it
to the ZIP redirect. Exact Git blob closure and SHA match the selected
workflow, attempt and ZIP. The dedicated Filex Docker socket is fixed under
`Workspaces/Filex/runtime/colima/filex/docker.sock`. The preparer builds an
untagged image from the pinned local base and runs an isolated, no-network,
read-only smoke with disposable tmpfs data and fixture credentials. There is
no Compose create/restart, host bind mount, image push, or port publish.
Live Filex state is read only through the pinned runtime preflight.

Under the existing deployment cycle lock, the preparer rechecks canonical
main, exact runner/config pins, the registered live prior selection and the
candidate image ID. It binds `filex-writer-prepared/<sha>.apply.json` to a
byte-identical `targets/filex-writer/candidate.json`, with no overwrite of a
different binding, then publishes standard `ready.json` with
`manualOnly:true`. A failed attempt, unavailable image or mismatched native
state stays blocked for operator reconciliation, not automatic activation.

A prepared Filex image/candidate/readiness record is never evidence of active
writer compatibility. The backend gate consults the separate read-only proof
of an actually applied and still matching writer; missing or mismatched proof
fails closed for changes that require it.
