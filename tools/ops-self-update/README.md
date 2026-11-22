# OPS-only Mac self-update (not installed by this source)

This staged coordinator is independent of the OPS web PID. A reviewed user
LaunchAgent can invoke the pinned copy of `run_once.py` every five minutes.
The `launchagent.template.plist` has an intentionally non-runnable path.
OPS is deliberately excluded from the OPS web deployment tab. The detached
job is responsible only for the OPS service. Every other deployment target
uses the separate OPS deployment cycle and its immediate trigger.

GitHub-hosted `prepare-ops-release.yml` runs only on push to the canonical
main branch. Only OPS tests and the `ops-service/` source artifact are
built. The local coordinator requires the most recent matching run's
successful completion and exact push/main/canonical repository/commit/workflow
identity. It rebuilds the OPS-only source archive directly from that Git
commit, checks all extracted paths and local SHA-256, verifies the GitHub
main ref again, and never downloads from a URL in the release data. The
server-side artifact is audit evidence, not an unauthenticated binary
download endpoint. The fixed GitHub Actions workflow-runs GET uses the
existing owner-enrolled fine-grained Actions-read item for `pvenus/ngs-services`
in the login Keychain (service `com.vinus.ops.ngs-local.github-actions-read`,
account `pvenus/ngs-platform`). It does not create or modify the Keychain.
The token remains in process memory and is not passed to Git, redirected,
printed, or written. Canonical head checks remain credential-free Git HTTPS.
Missing or rejected auth, rate limits, unavailable GitHub, changed history,
or mismatched proof leave the existing OPS deployment untouched. There is no
anonymous Actions fallback.

Source publication alone does not replace the detached installed script.
An approved operator must compare the installed selector/script digest,
ensure the existing owner-private roots and both updater/deploy locks are
idle with no switching journal, save the exact previous executable outside
the runnable path, and atomically replace only
`~/Workspaces/OPS/runtime/ops-self-update/run_once.py` with the reviewed
owner-only regular file (preserve the installed 0500 mode). Preserve
the LaunchAgent, its interval, installed/status/transaction records, and
the current web selector. A natural tick verifies the new code. Do not
manually run the updater as an auth test: an eligible tick can deploy OPS.

Before enabling separately, an operator needs to verify the existing
LaunchAgent/OPS code and create the owner-private path
`~/Workspaces/OPS/runtime/ops-self-update` (0700). Its `installed.json` (0600)
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
never restart/copy/provision targets. One private lock prevents overlapping OPS updates. A second lock shared
with the OPS deploy cycle defers an OPS restart while a target is applying.

The journal keeps exact previous/candidate plist bytes and release IDs. If
a health check fails, the prior OPS plist is restored and its existing
requests/services/alarms routes must be healthy again. An interrupted
switch is reconciled before another cycle; unexpected filesystem or process
ownership fails closed. Nothing autodeploys the same failed SHA again.
The terminal private `status.json` exposes only:

    {"schemaVersion":"ops.self-update-status.v1","target":"ops",
     "state":"current|pending|unchanged|busy|deployed|rolled_back|previous_attempt_rolled_back|failed",
     "sha":"<known complete installed SHA or null>","checkedAt":"<UTC Z>"}

During a failure the status can include only a sanitized machine `reason`,
such as `GITHUB_ACTIONS_READ_DENIED` or `GITHUB_ACTIONS_RATE_LIMITED`.
No service is installed, no access policy changes and no credentials are
provisioned by this branch.

## Existing Mac installation: guarded one-time runtime relocation

Private OPS state is isolated from NGS runtime. The only supported roots
are `~/Workspaces/OPS/runtime/ops-self-update` and its sibling
`~/Workspaces/OPS/runtime/deploy-control`. No fallback path, symlink, or
copy of an active owner key is accepted. The pinned script and its
WorkingDirectory and logs belong to the new self-update root. Other
services' native state remains where it is.

1. Verify the new canonical GitHub commit and OPS/app/adapters each use the
   new fixed control root. Let the existing updater apply the new OPS
   service normally before moving it. The new UI can report deployment
   controls unconfigured during this narrow transition; its ordinary
   requests/alarms remain available. Do not try to bootstrap both updaters.
2. Verify the current OPS label, native process and :8090 are healthy.
   Compare the actual selector bytes with the existing private
   `installed.json` receipt. Pin both old directory inodes/device/owner,
   both destination names absent, and source/destination parent on the
   same volume (do not merge with existing files or mount paths). Preserve
   both runtime directory modes 0700. Existing unrelated OPS runtime
   siblings are never moved.
3. Pause only the separate `com.vinus.ngs.ops-self-update` LaunchAgent and
   verify its old PID/label stopped. Do not pause OPS, NGS, PDS, RS,
   Planner, Space or workers. Obtain the existing nonblocking exclusive
   locks in the updater and deployment control roots; refuse migration
   if either is held, its file is not the same inode, the OPS updater
   journal is in `switching`, or a grouped `active.json` remains.
   Hold both lock descriptors through relocation and selector rotation.
4. Keep a separate owner-private exact rollback copy of the original
   updater LaunchAgent and pinned executable. With the same-volume
   private guards, rename the verified directories, not their contents;
   preserve receipts, journals, owner proof, adapter binaries and
   artifacts without editing their signed/verified identities. If one
   rename fails, restore the other verified inode before continuing.
   Install the new exact GitHub-backed updater pin in the new subtree.
   Re-render only that updater's script/WorkingDirectory/log paths, retain
   the existing label and interval, and relint the plist. Prior receipts
   may describe old paths as historical facts. Live runnable references
   must be reverified from their original publishers, not broadly replaced
   in records. Unverified/missing candidates remain disabled.
5. Release the two locks when both moves/rotations are complete, then
   bootstrap only the same updater label. Verify the normal timer
   interval, active new script/WorkingDirectory, source proof and file
   modes; verify :8090, same running OPS PID and its unchanged launch
   selector, authorized status from the new control root, and the next
   natural timer receipt. No deploy button or job is needed to test the
   move. The old two paths must stay absent; do not touch unrelated NGS
   runtime.
6. If activation fails, stop only the migrated updater, verify no new
   cycle and reacquire locks, reverse both guarded same-volume renames,
   restore the exact previous executable/selector there, and verify the
   old timer, ordinary OPS health and status. A newer OPS process that
   only knows the new root may keep deployment actions disabled after a
   rollback rather than re-enabling old credentials through a fallback;
   leave it healthy and report the control-plane mismatch for a reviewed
   update. If drift or a newly active journal makes recovery unsafe,
   leave paths intact and report exact phase. Do not delete old paths or
   attempt cross-volume copy as a substitute.

This plan is not an installer and this source change does not operate,
move, or bootstrap any live service.
