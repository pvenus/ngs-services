# OPS-only Mac self-update (not installed by this source)

This staged coordinator is independent of the OPS web PID. A reviewed user
LaunchAgent can invoke the pinned copy of `run_once.py` every five minutes.
The `launchagent.template.plist` has an intentionally non-runnable path.
An eventual owner-authorized “즉시 배포” button should only kickstart that
same fixed detached job. There is no alternate command, selected SHA, forced
execution or alternate eligibility policy. The button integration is not
included here; existing OPS prepared NGS deployment is a separate path.

GitHub-hosted `prepare-ops-release.yml` runs only on push to the canonical
main branch. Only OPS tests and the `ops-service/` source artifact are
built. The local coordinator requires the most recent matching run's
successful completion and exact push/main/canonical repository/commit/workflow
identity. It rebuilds the OPS-only source archive directly from that Git
commit, checks all extracted paths and local SHA-256, verifies the GitHub
main ref again, and never downloads from a URL in the release data. The
server-side artifact is audit evidence, not an unauthenticated binary
download endpoint. It uses only standard public metadata reads and Git
HTTPS, no new token or workflow secret. If GitHub access is rate-limited,
unavailable, the installed commit is not an ancestor within fetched history,
or the canonical proof doesn't match, nothing is applied.

Before enabling separately, an operator needs to verify the existing
LaunchAgent/OPS code and create the owner-private path
`~/Workspaces/NGS/runtime/ops-self-update` (0700). Its `installed.json` (0600)
contains:

    {"sha":"<actual canonical ngs-services commit>",
     "tree":"<actual Git object for SHA:ops-service>",
     "plistSha256":"<SHA256 of actual approved OPS launch plist bytes>",
     "directory":"<absolute fixed code/releases/ngs-services-github-xxxxxxx/ops-service>"}

No bootstrap value is guessed or written by the updater. Later installs use
`code/releases/ops-only-github-<full SHA>/ops-service`. A different repository
commit with the same OPS tree never restarts OPS. Only the fixed
`com.vinus.npcgen.ops` plist launch script and working directory may change.
Other launch jobs, NGS/Space, Request/Workflow/PDS/RS/workers, and data are
never restart/copy/provision targets. One private lock prevents overlap.

The journal keeps exact previous/candidate plist bytes and release IDs. If
a health check fails, the prior OPS plist is restored and its existing
requests/services/alarms routes must be healthy again. An interrupted
switch is reconciled before another cycle; unexpected filesystem or process
ownership fails closed. Nothing autodeploys the same failed SHA again.
The terminal private `status.json` exposes only:

    {"schemaVersion":"ops.self-update-status.v1","target":"ops",
     "state":"current|pending|unchanged|busy|deployed|rolled_back|previous_attempt_rolled_back|failed",
     "sha":"<known complete installed SHA or null>","checkedAt":"<UTC Z>"}

No service is installed, no access policy changes and no credentials are
provisioned by this branch.
