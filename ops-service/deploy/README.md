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
