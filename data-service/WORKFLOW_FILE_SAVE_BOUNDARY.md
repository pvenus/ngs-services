# Workflow file save boundary

1. Open a read-only Workflow SQLite snapshot and validate the exact running
   claim and its dependencies. Copy the proof and close the snapshot.
2. For a mutation, reserve the same task and attempt with a short SQLite CAS.
   The persistent marker is shown as saving. Do not hold SQLite across
   validation of Filex data, storage reads, writing, or reply verification.
3. Filex performs the authorized storage operation. It does not own the
   Workflow state. The worker clears the marker in the same transaction as
   completion or ordinary failure. A lost save response or an abandoned
   reservation must become a normal failure, not proof of success. Expiry is
   claim-bound, skips live PDS handoffs, and performs no file replay or recovery.
4. The two Filex transports reject calls made inside a Workflow snapshot.
   Serial frame uploads may continue a single claim; the in-process guard
   excludes overlapping calls for that claim.
5. Deploy worker versions that clear the additive marker before enabling the
   common PDS save boundary. Verify with an independent write during Filex I/O,
   a failed save response, and a real run from saving to terminal state.
