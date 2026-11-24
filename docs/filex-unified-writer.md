# Filex writer: one service, scoped folders

Production PDS calls one authenticated loopback Filex writer. The Filex
reader/sync service and the Request authority are separate existing roles,
not additional writers. No worker or browser chooses a writer, bearer, disk
path, or scope.

## Fixed scopes

| ID | Canonical child | PDS client |
|---|---|---|
| `npc` | `npcs/` | current NPC, owned PNG and guard/fence operations |
| `concept` | `concept/` | current Master Concept |
| `motion` | `motion-presets/` | presets and motion conversion |
| `media` | `media-artifacts/` | immutable generic media |
| `audio` | `audio/` | official audio generate/separate/delete |
| `props` | `props/` | official immutable prop revisions and assets |

The writer mounts only those named folders. It never mounts the entire
project root read-write. Each scoped route preserves the original domain
validation, ownership proof and durable readback. For example, PDS's local
`/v1/props/ownership/workflow-package` claim goes to Filex
`/v1/scopes/props/workflow/props` only; it does not send the PDS claim to
a generic path or dynamically choose a folder. The scopes can explicitly
share a preauthorized role: existing concept credentials already authorize
audio and props. NPC, motion, and media remain separately authenticated.

## Configuration and cutover

OPS configures the writer listener once (normally `127.0.0.1:8095`) and
supplies PDS one private `PDS_FILEX_WRITER_CONFIG` file:

```json
{
  "schemaVersion": "pds-filex-writer.v1",
  "projectId": "binari",
  "endpoint": "http://127.0.0.1:8095",
  "scopes": [
    {"id":"npc","tokenFile":"/path/to/private/NPC-token-file"},
    {"id":"concept","tokenFile":"/path/to/private/concept-token-file"},
    {"id":"motion","tokenFile":"/path/to/private/motion-token-file"},
    {"id":"media","tokenFile":"/path/to/private/media-token-file"},
    {"id":"audio","tokenFile":"/path/to/private/concept-token-file"},
    {"id":"props","tokenFile":"/path/to/private/concept-token-file"}
  ]
}
```

All six operating scope entries are required, exactly once. An additional
operator-approved folder can be added to the array without another URL. The manifest and secrets
are private regular files owned by the PDS account. Tokens are never embedded
in the manifest, process arguments, logs, release checkout or health response.
Do not add per-feature writer URL environment variables. Existing NPC policy
configuration still owns its storage binding and authorization metadata.
Media ingress and Request authorization are not a Filex writer connection;
the media v2 config has just those keys. Retained v1 writer URL/token fields
are not consulted after the cutover.

A coupled OPS change must select the pinned Filex + PDS releases together.
Check authenticated capabilities for all six scopes and expected namespace,
then confirm PDS `/health.filexWriterScopesSha256` matches the exact local
manifest and check feature boundaries (NPC guarded read, concept snapshot,
motion/media read, audio/props capability). Plain health alone cannot prove
the writer is reachable or has the right storage. No simultaneous legacy
writers may access the same canonical folders. Revert the coupled root,
ports and PDS binding together if cutover verification fails; never run the
old and new writer together or silently fall back to an old URL.

## Extending a domain

Extend the scope/child-folder registry and its narrowly scoped route list
inside the existing service. Add one operator manifest entry for any new approved folder, mount only
that folder,
and add a negative cross-scope test. A new resource kind does not get a
second Dockerfile/server/launch job or caller-controlled root. Prefer adding
a route to an existing folder when its authority is the same. If authorization
needs a new role, define the role explicitly rather than reuse a token by
accident. Never bypass an existing domain proof, transaction, or readback to
make a new route fit.

Verification (disposable roots/listeners and dummy tokens only):

```sh
PYTHONPATH=data-service python3 -B -m unittest discover -s data-service/tests -p test_filex_writer_binding.py
PYTHONPATH=data-service:filex-writer python3 -B -m unittest test_pds_unified_scope_http.JoinedClients.test_production_pds_scope_routes_and_real_writer_keep_storage_isolated
```

The transport test exercises all six real PDS clients, raw PNG/WAV, wrong
credentials and forbidden routes. The joined test additionally uses the
actual unified writer, real scoped stores and a PDS-to-Filex prop commit/read
check; it does not generate media or operate on production.

## OPS local cutover (one existing target)

`Filex 저장기` is the single OPS target for the storage transition. It pairs the
approved Filex CI closure with the Backend Stack archive from the **same**
canonical Services commit. The Backend Stack must not be launched independently
with new writer routing before Filex succeeds. Web Filex on port 5212 remains
running unchanged; its separate existing access rules are not modified here.

One-time owner provisioning uses the existing reviewed private helper and
startup pin procedure in [FILEX-PREPARATION.md](../ops-service/deploy/FILEX-PREPARATION.md).
OPS readiness does not grant authority to install those helpers itself.

- Pin the reviewed new supervisor and helpers, including
  `filex_unified_runtime.py`, in the existing 0700 private helper directory and
  0600 helper manifest. The existing Filex target's `run-once` is the reviewed
  `filex_writer_apply.py` (0500). Its adjacent trusted dependencies are
  `prepare_ngs_release.py`, `filex_unified_runtime.py`, `mac_target_apply.py`,
  `planner_cutover.py` (0600).
- Render the existing Filex owner startup bridge with
  `filex_unified_runtime.startup_bridge(home,control)`. Rebind its exact digest
  through existing `registeredFilex.hashes["start-filex.sh"]` and
  `filex-writer-preparation.json` together with the exact new runner digest.
  Base Compose is not rewritten. After success, the bridge starts only the
  selected unified writer, never the four stopped rollback containers.
- Owner-private `filex-writer-credentials.json` (0600) contains
  `{"schemaVersion":"ops.filex-writer-credentials.v1","credentials":{...}}`
  with the four existing **absolute token-file paths** for
  `npc`, `concept`, `motion`, `media`. No new credentials or copies are
  needed. The Desktop daemon mounts the same existing named token volumes
  read-only. OPS preparation generates the matched PDS manifest per candidate
  from these paths and the single source registry; do not maintain a second
  folder list.
- Provision the exact empty canonical `binari/props` directory and owner
  private 0700 `Workspaces/PDS/data/.filex-prop-stage` if absent. Never move
  or reinitialize an existing canonical folder. A missing mount is a refusal,
  not a command for Compose to create a new host path.

The reviewed candidate-only supervisor builds an isolated no-network smoke,
then seals a manual OPS candidate. Apply backs up the old backend launch
selector and binds an in-progress journal before beginning. It stops and
drains the existing Backend Stack job, stops the exact four old writer
containers, starts only the new writer, verifies every authenticated scope and
its actual mount set, then starts and verifies the paired PDS. A future registry
change uses the same transaction against the prior single writer instead.
Rollback stops the successor before restoring the previous writer(s) and the
unchanged predecessor Backend Stack selector and verifies both. Stopped old
containers/images are retained as rollback assets, not active services. No
canonical files, image tags, volumes, or old release artifacts are deleted.
Unproved rollback retains the journal for owner reconciliation, not retry.
