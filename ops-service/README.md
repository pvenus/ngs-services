# OPS official requests and services

This owner-local view binds only to `127.0.0.1:8090`. It does not start legacy
consumers or submit or cancel requests. The request list reads the Request
Service's MCP `request_list`; request detail reads the dedicated Workflow
ledger without writing to it.

The **알람** tab at `/alarms` shows only Workflow tasks whose status is
`failed` and whose `retry_count` is at least 3, newest first (up to 200).
A third retry that is running and any completed task are excluded. The page
loads on open or manual refresh; it sends no push or other notifications.
Before the retry migration, a valid ledger without `retry_count` returns an
empty list; missing or unreadable ledgers are shown as an error.

Staging verification:

```sh
/opt/homebrew/bin/python3.12 -B -m unittest -q
node --check app.js
node --test test_alarms_ui.mjs
```

## Deployment controls (disabled by default)

`/deployments` is a separate owner-local screen. There are three groups:
NGS (Local and Space), services (Request Service, Workflow Planner, and
the backend stack containing PDS), and six workers (document, file,
image, animation, postprocess, and music). The PDS runs in a seven-service
stack today and is not a standalone restart target. OPS itself is never
an OPS deployment target: its independent Mac self-update is documented in
`tools/ops-self-update/README.md`.

The HTTP action is `POST /api/deployments` with exactly
`{"action":"wake","target":"worker-audio"}`. Use the
target ID shown by the status API, or `all` for every listed target.
The browser sends no SHA, command, path, flags or credentials for GitHub or
the provider. Only verified candidates for those targets are eligible.
`all` is not a fallback: it starts nothing if a selected target is
unavailable. A failure during execution stops the remaining batch. Results
are recorded per target; it is not a distributed transaction or a rollback
of targets that already finished. The button, checked background sweep,
target-specific rollback and verification all go through fixed code paths.

`NGS_OPS_DEPLOY_ENABLED=1` enables the owner-local controller, and
`NGS_OPS_DEPLOY_AUTOMATIC=1` additionally enables the OPS loop (one minute
between checks by default). Neither switch is enabled by this source.
Only the existing approved native launcher should set them. Provision
`~/Workspaces/NGS/runtime/deploy-control` (0700) and an `owner-key` (0600)
containing a random 64-character lowercase hex key. Enter it only in the
loopback OPS deployment screen. No key is prefilled, passed in a URL, or
logged. The browser receives a short-lived HttpOnly same-origin cookie and
session-bound CSRF token. Heartbeats do not grant deployment access.

The cycle consumes per-target trusted readiness and a fixed `run-once`
adapter under the private path `targets/<id>/`. No candidate is generated
on button press. CI source proof is not deployment readiness; a trusted
preparation step must verify it and prepare the specific runtime before
publishing `ready.json`. The fixed Local NGS bridge reads the independently
prepared exact SHA/digest and uses the existing Mac executor and its durable
health receipt. Space remains unavailable until an unattended publisher
that preserves the site's audience and proves its deployed SHA is present.
A prepared Space bundle is not a deployed Space version.

The status API displays independently verified last deployment SHA and time,
not build time, or no observation. For a verified preexisting NGS baseline
only, the owner may supply private `observed.json` (0600), never writable via
HTTP:

    {"schemaVersion":"ops.ngs-observed-deployments.v1",
     "local":{"sha":"<canonical SHA>","deployedAt":"2026-11-22T00:00:00Z"},
     "space":{"sha":"<canonical SHA>","deployedAt":"2026-11-22T00:00:00Z",
              "deploymentId":"<native deployment ID>","providerSha":"<provider SHA>"}}

Use `null` for an environment without verified proof. Only canonical NGS
SHAs may be compared, not the provider SHA.
