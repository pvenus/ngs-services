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
