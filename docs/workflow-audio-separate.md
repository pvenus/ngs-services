# BGM separation request

A user selects a completed official BGM. Local or verified Space registers
`audio.separate` (`ngs.audio-separate.v1`) in the existing Request Service.
The exact payload is:

```json
{
  "schemaVersion": "ngs.audio-separate.v1",
  "projectId": "binari",
  "sourceOfficialRequestId": "request.official.<64 lowercase hex digits>",
  "sourceSha256": "<64 lowercase hex digits>"
}
```

The digest pins the original WAV bytes, not the request body or its metadata.
Request Service verifies the original completed BGM ledger row without
changing it. PDS verifies the referenced published WAV and its exact digest;
the front end and Request Service do not grant storage authority. Standard
idempotency keys make retries of the same registration safe. Different keys
can create separate requests for the same source. No new request or work
queue is installed.

Planner creates one `audio_separate` task with type `audio.separate`,
worker `audio`, and no predecessor. Its input contains only
`sourceOfficialRequestId` and `sourceSha256`. The worker reads the input
through PDS and publishes both derivative outputs through PDS under the new
official request, without overwriting or deleting the original. OPS displays
the task as **음원 분리** and uses the existing status path.

Enable new submission only after Request Service, Planner, PDS, the
appropriate Filex writer, and the Audio Worker are deployed and verified as
one compatible workflow. Do not send the new request to older services.
