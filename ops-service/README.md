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

## Prepared NGS deployment (disabled by default)

`/deployments` is a separate owner-local screen. It does not wait for or
use the heartbeat MCP. Set `NGS_OPS_DEPLOY_ENABLED=1` only after configuring
the prepared-release verifier and the fixed local adapter. The default
leaves every deployment API disabled.

The private owner workflow supplies `~/Workspaces/NGS/runtime/deploy-control`
(0700) with these exact entries:

- `owner-key`: a locally delivered random 64-character lowercase hex owner
  proof (0600). Enter it only in the loopback OPS deployment screen. It is
  neither prefilled nor passed in a URL, environment variable or log. An
  authenticated browser receives a 15-minute HttpOnly same-site cookie and
  a session-bound CSRF token. The existing OPS heartbeat access or
  loopback reachability alone does not grant deployment access.
- `prepared.json` (0600): atomically published by a separate trustworthy
  verifier after validating the canonical GitHub run, actual artifact and
  prepared Mac release. OPS does not fetch/build on button press:

      {"schemaVersion":"ops.ngs-prepared-deploy.v1","sha":"<full GitHub SHA>","verifiedAt":"2026-11-22T00:00:00Z",
       "mac":{"ready":true},"provenance":{"runId":"1","runAttempt":"1","artifactId":"1","archiveSha256":"<64 hex>"}}

  An optional exact `space:{sourceSha,packageBaseSha,archiveSha256}` is
  independent packaging provenance, not a hosted deployment receipt. Even
  when it exists, Space is **pending**. This feature does not use Sites
  browser cookies or invoke an undocumented publishing API.
- `bin/ngs-mac-apply`: the only possible child process; the owner-private
  binary or script receives the exact prepared GitHub SHA and no other
  request inputs. Its success response must be
  `{"sha":"<exact requested SHA>","target":"mac","state":"deployed","health":"verified"}`
  after its own provenance, transaction, and service health checks.
- `jobs/` (0700): one private durable job receipt per SHA, never stored in
  the Request/Workflow ledger. Duplicate actions for the same SHA replay the
  existing result without launching again. A crash with no final receipt shows
  **unknown**; do not auto-retry or delete the receipt. Reconcile with the
  adapter's independent journal and live release before taking action.

The owner-local API requires an exact same-origin browser request context.
The trigger accepts only `{"action":"deploy","target":"local","sha":"<full prepared SHA>"}`
plus the session-bound CSRF header. No path, target, flags, command, deploy
provider credential, Git user input or webhook is accepted.

The tab always distinguishes the prepared GitHub SHA from each target's
last verified deployment. For already deployed environments, the trusted
operator may place private `observed.json` (0600). There is intentionally
no HTTP write/import for it:

    {"schemaVersion":"ops.ngs-observed-deployments.v1",
     "local":{"sha":"<full GitHub SHA>","deployedAt":"2026-11-22T00:00:00Z"},
     "space":{"sha":"<full GitHub SHA>","deployedAt":"2026-11-22T00:00:00Z",
              "deploymentId":"<native deployment ID>","providerSha":"<Sites Git SHA>"}}

Use `null` for an environment without verified evidence. A successful
local job records its actual verified completion time. The UI highlights
mismatch only when both canonical GitHub SHAs were observed, and never
compares the Sites Git SHA to the canonical GitHub SHA. Unknown or uncertain
deployment progress suppresses mismatch. The `all` and `space` targets
stay disabled and return unavailable until a supported native publisher
is connected; clicking `all` never silently starts a local-only run.
