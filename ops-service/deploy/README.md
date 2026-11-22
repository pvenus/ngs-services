# Verify a prepared NGS Local release

`prepare_ngs_release.py` is a private OPS-side library, not a deployment job.
`verified_main_candidate(github)` checks current GitHub main, the exact push
workflow/run/attempt, authenticated artifact metadata, outer ZIP checksum, and
the immutable inner Local TAR and source manifest. Its injected transport
supports offline fixtures; the optional `GitHubEvidence` adapter requires an
already-configured absolute gh executable. It does not set up credentials.

Once a separate preparer has created the owner-private release and execution
trees and installed the locked dependencies, `write_prepared` verifies both
before atomically writing the Mac-only ready receipt. It neither applies a
release nor publishes Space. No polling or live integration is installed.

Run offline tests:
```sh
PYTHONPATH=ops-service/deploy python3 -B -m unittest discover -s ops-service/deploy -p test_prepare_ngs_release.py
```

## Fixed Mac target updater

`mac_target_apply.py` is staged source for a private `targets/<id>/run-once`
(install as executable; its only sibling import is the trusted, private
`prepare_ngs_release.py`). It accepts only the cycle's two named inputs from
the environment, no argv. It does not fetch or prepare unverified source.
The upstream preparer must verify and install the exact archive, and supply
an owner-private `candidate.json` containing exactly:

- `schemaVersion`: `ops.mac-target-candidate.v1`
- `target`, `sha`, `archiveSha256`: exact verified dispatch values
- `treeSha256`: digest from `prepare_ngs_release.tree_sha(installed)`
- `expectedPriorPlistSha256`: prior selector hash acquired during preparation

Worker releases are `~/Workspaces/Workers/code/releases/workflows-github-<sha>`
and backend releases are
`~/Workspaces/BackendServices/code/releases/ngs-services-github-<sha>`.
Only fixed checked-in catalog entries are accepted. Local process restart
preserves private runtime configuration, databases, models, and other plist
values; only the known release-root arguments change. Unsupported release
layouts, unexpected prior release references, and unsupported dependency
symlinks fail closed. Fresh OPS heartbeat for the new process (all workers
and RS) or seven existing loopback health endpoints (backend stack) is required
before the runner emits a deployed receipt. On failed startup, it restores the
exact prior selector and verifies service recovery; it never emits a success
receipt for rollback. Planner is deliberately unsupported until it has an
independent readiness probe. This code does not install or run any live updater.

## Mac service and worker apply adapter

The detached OPS self-updater does not deploy any of these. OPS runs the fixed
`mac_target_apply.py` adapter for six workers, RS, and the seven-child backend
stack. It changes only the fixed LaunchAgent source pins; runtime configuration,
DB paths and model paths are preserved. It records success only after the new
process has a fresh heartbeat or, for the stack, all seven health endpoints
are healthy. Failure attempts to restore the old plist and verify it again.

Use the exact target's trusted candidate manifest and installed-tree digest.
The hosted `prepare-service-targets.yml` and `prepare-worker-targets.yml`
workflows report source readiness only. They do not provision private runtime
config, build native dependencies or automatically publish `ready.json`.
An OPS runner has to be prepared and pinned locally before a target is eligible.
Do not enable the timer or provision readiness until that target has a safe
cutover for active work, verified runtime dependencies, and a successful
isolated apply and rollback check. Planner remains listed but unconfigured
until its independent health check is available. Space has its own separate
publisher and is not handled by this Mac adapter.

### Services and worker static target proof

`prepare_service_targets.py` is a staging library, never a launcher or
network transport. The operator must read the exact canonical main ref,
matching workflow, successful main push run and artifact metadata from
GitHub, download the exact artifact ZIP, and confirm main did not advance
before publishing any state. `verify_target_artifact` checks the canonical
repo/run/attempt/ZIP digest and the single data-free `release.json`.
`tracked_targets` binds it to exact reviewed Git object IDs, fixed entrypoints
and source counts. `assemble_source` creates a new private source release from
those Git blobs only. It never starts code, copies a runtime tree or reads
private worker bindings. Existing releases are not overwritten.

For Request Service, `request_candidate` verifies every staged file, pins
the exact existing selector and required storage/startup module bytes, and
generates the existing private `candidate.json` DTO without publishing it.
Folder/data paths, the inbox, and all operators’ configuration stay in place.
The seven-service backend stack is verified as a single source target, not
seven deployment switches. Its actionable readiness still needs runtime and
dependency compatibility proof, particularly the existing private Filex/PDS
bindings and all seven listener/health checks.

`verify_equivalent` compares tracked non-documentation source bytes to the
present selected release and rejects unrelated visible files; it is neither
a health report nor an observed Git revision change. The trusted local
coordinator can record `equivalent_record` in its private target directory
bound to the observed prior source and exact selector. Do not write a ready
record or deployment timestamp for equivalent code. Service/worker target
publication is separate from source verification and stays in the supported
private OPS runtime, not a repository checkout.

## Automatic NGS Local preparation only

`prepare_ngs_local_once.py` is a fixed, no-arguments, noninteractive preparation
entrypoint. It verifies the current canonical `pvenus/ngs-platform` main and
its successful CI artifact, uses only the separately reviewed native Mac
assembler and existing owner-private offline cache, then registers its exact
SHA in the existing **NGS Local** button lane. It cannot invoke the apply
adapter, change a selector, start or stop a service, publish Space, or update
OPS itself. An already selected or already ready exact SHA returns without
downloading or assembling again.

Provision copies of the preparer and `prepare_ngs_release.py` (0600) in
`~/Workspaces/OPS/runtime/deploy-control/bin`. In the same directory install
(0600) the exact `tools/ops-deploy/assemble_mac_release.py` from reviewed
NGS commit `2395719a30c6ea83d2306992409ecd039a70b229`, SHA-256
`959547928ccafe7a27f6d05e0649ba6e24d770feaba3ea8a63b961d777765547`.
The preparer checks those assembler bytes before importing them. Supply the
already approved owner-private (0700) npm cache at
`deploy-control/ngs-local-npm-cache`; do not let the assembler install
online or run dependency lifecycle scripts.

The Mac acquires the artifact directly; no Dot automation, connector handoff,
`gh` session, external mirror, token file or fallback credential is used.
The owner must manually enroll a **fine-grained** GitHub PAT restricted to
`pvenus/ngs-platform` and **Actions: read** (GitHub's mandatory Metadata
permission is implicit). The public main-ref read is unauthenticated.
The fixed login Keychain generic-password identifiers are:

- Service: `com.vinus.ops.ngs-local.github-actions-read`
- Account: `pvenus/ngs-platform`

Put the value directly into the login Keychain using the secure password
prompt or Keychain Access; do not put the value in chat, an argv string,
shell history, or an environment variable. Grant access only to the fixed
`/usr/bin/security` application, not all applications. This code does not
create/modify/unlock a Keychain or provision credentials. Keychain reads and the
fixed Actions REST URLs are bound to this repo only. The verified API's sole
artifact redirect is limited to GitHub Actions' known HTTPS artifact hosts.
The cross-origin GET does **not** send Authorization, cookies, referrer, or
allow any further redirect. The existing exact digest/size and run/main checks
still apply.

Place the reviewed companion `github_actions_keychain.py` and optional
`check_ngs_local_github_auth.py` into the same private bin directory as the
preparer and verifier (0600). Install the separate, no-arguments 300-second
LaunchAgent from the source template only after review, with private working
directory and fixed entry path. It never wakes or changes the OPS self-update
agent or the existing NGS deploy button runner. No source publication or
checkout installs the timer. Do not run the former Dot timer for this lane.

Run prep with `/opt/homebrew/bin/python3.12 -B -u <fixed-bin>/prepare_ngs_local_once.py`.
The private status at `deploy-control/ngs-local-preparation-status.json` has a
bounded fixed schema: `ready` (with exact candidate SHA/outcome), `pending`,
`auth_missing`, `auth_locked_or_denied`, `auth_rejected`, or `blocked`.
It contains only safe error codes, never GitHub responses or signed URLs.
An already-selected, already-ready or already-attempted SHA does not acquire
the credential or download again. Past failures are not rearmed.
Use the fixed no-arguments `check_ngs_local_github_auth.py` to deliberately
test the exact authorized artifact read even for an unchanged main; the probe
does not assemble, register or write status. Only
`artifact_read_verified` proves full CI ZIP access.

Public main movement, changed plist/runner, active or uncertain deploy,
partial packages and npm-cache misses do not produce new readiness. Review
incomplete private staging manually; do not remove an apply attempt marker or
overwrite a staged tree. Polling never clears prior deployed observations.

Tests: `python3 -B -m unittest discover -s ops-service/deploy -p 'test_prepare_ngs_local_once.py'`.

## Local NGS deployment failure diagnostics

The fixed Local target adapter sends only whitelisted native stage/reason codes
and separately recorded recovery status; it does not expose or retain raw
stderr, service output, absolute paths or user data. OPS shows exact prior
legacy recovery separately as `원인 미기록` when a durable old `rolled-back`
record had no diagnostic. A missing or unconfirmed recovery is not shown as
verified. The updater and target still refuse replay of a failed SHA.

The diagnostic source change alone installs nothing. A native runner update
and fixed adapter update require the reviewed package and existing runtime
provisioning. Do not repin or manipulate an old failed candidate to retry it.
