# Active local service source

This package is a source-only refresh on the existing official `main` history.
It includes the current code serving the local NGS workflow, without copying
live requests, SQLite files, generated assets, model weights, private mount
configuration, credentials, or keys. It does not re-enable dormant services.

| Role | Local binding | Active release basis / source in this tree |
| --- | --- | --- |
| PDS | 8030, including read-only folder MCP `/mcp` | `7756383`, `data-service/` |
| Catalog, execution, workflow decision, file, video alpha, workflow | 8031, 8032, 8040, 8041, 8050, 8060 | `7756383`, `runtime/stack.py` starts the unchanged seven-role stack |
| Official Request Service (RS) | 8770 MCP | `ec48e45`, `request-service/request-service.mjs` |
| Workflow Planner | Dedicated SQLite, no new listener | `ec48e45`, `workflow-planner/cli.mjs` |
| Operations console (OPS) | 8090 | `d077065`, `ops-service/server.py` |
| Filex concept/BGM writer | Private authenticated writer | `7756383`, `filex-writer/` |
| Filex NPC, media, motion writer images | Existing private, narrow writer roles | `2bd69bf`, `0f9ed28`, `3b591ea`, `filex-live-roles/` |

The existing private pilot selector continues to take its exact NPC target
from the trusted host launch environment; no production character ID is
embedded in public source. The selector still requires an explicit match
and the existing project/noncombat constraints.

The NGS browser, 5275 NGS local MCP receiver, RS MCP adapter and PDS MCP
sender remain in canonical `pvenus/ngs-platform` commit
`94741385f19beca9a3bac950381ec012444c9f5d`. The platform deployment owns
these adapters; do not duplicate them here or route the private local
MCP ports to Space. Independent generation workers remain in `ngs-workers`.

The separately running TaskService has no verified dependency from this
current NGS route and is left untouched. Older Gateway and Laya launch
definitions are not part of the running stack. The old stopped workflow host
is not a current worker deployment target. This release does not restart any
of them or reorganize data. Filex's hosted file browser and private mounts are
externally configured; do not apply sample compose settings to live instances.

## Offline smoke and guarded cutover

1. Verify the exact approved GitHub commit. Materialize a new release
   directory without touching existing data roots or prior release directories.
2. Run `runtime/stack.py --storage-root <disposable absolute path> --print-plan`,
   run the RS/Planner JavaScript tests, and run OPS and PDS fixture tests.
3. Before switching the existing LaunchAgents for PDS stack, RS, Planner
   or OPS, confirm no pending/running workflow work and no concurrent deploy.
   Preserve private environment and exact arguments, including the Planner
   database path/inode. Change source paths only. Never use a new private
   SQLite or canonical source root just to get a successful smoke.
4. Verify the seven stack readiness routes, RS MCP discovery/read, Planner
   residency, OPS official request summaries and worker heartbeat names.
   Restore the previous known-good LaunchAgent source paths if verification
   fails. No new business request is required.

## Narrow unused-source cleanup

The unconnected experimental pinned-file export and the retired standalone
SQLite knowledge-catalog implementation are not build or runtime entrypoints.
The unused legacy knowledge seed environment example is no longer advertised.
This does not remove a PDS route, Filex data, a migration script, or a worker.
In particular, the legacy Workflow service (8060) still uses the execution,
file, and video-alpha APIs (8032, 8041, and 8050); retain those services and
their endpoint contracts.

### NPC pose recipes (selected workflow v2)

The existing Shared Planning, Representative/Idle/Direction Portrait, and Native
Media enablement jointly expose the v2 pose implementation; no additional
launch environment flag is needed. The scoped Filex NPC writer advertises
`workflowCombatPortraitWriteV1` only with those gates enabled. Deploy the scoped
Filex writer and PDS before clients accept v2. First-time v2 replacement checks
this capability and the current pinned motion sources before deleting the old
target; a previously confirmed exact delete receipt remains replayable.

For a full v2 request, idle remains the base; each additional pose is derived
from the registered motion association, not the motion name. For each selected
motion the same-direction final pixel task must complete; the animation asset
read then returns its Filex-verified raw source. A v2 animation-only request
uses the exact existing same-pose raw direction (absence fails, never switches
to idle or generates a portrait). The independent regeneration stage and atomic
raw/alpha/pixel replacement remain unchanged. V1 still uses its original
appearance/dependency routing.
