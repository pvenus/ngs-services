# Regenerate a published BGM as an instrumental

In the NGS playlist, **분리 요청** submits one formal request for the song on
that row. There is no prompt dialog. The request does not change the original
track, delete it, or use a third-party media URL.

## Registered selection and execution

- Request type: `audio.separate`; schema: `ngs.audio-separate.v1`.
- Exact fields: `schemaVersion`, `projectId`, `sourceOfficialRequestId`,
  `sourceSha256`. A valid source is a completed, published BGM generation in
  the same project. An instrumental made by this flow is not a new source.
- Workflow Planner selects one task with key `audio_separate` for the existing
  audio worker. The worker claims the exact task, uses PDS to read the published
  source and WAV, and verifies the pinned current digest, bounded size and type.
  Missing, inaccessible or changed sources are not substituted.
- A local source-separation model provides the accompaniment to ACE-Step
  Cover. The source WAV and temporary stems are not published again. Keep the
  generation prompt fixed and versioned in the worker; no extra request
  message, title prompt, or lyrics should override it. Guidance is to preserve
  the source melody, harmony, rhythm, instrumentation and form. Instrumental
  is passed as a model setting, not inferred from a list of negative terms.
- Publish one new standard WAV and metadata through PDS and Filex under the new
  request ID. The result metadata preserves the concept linkage and is linked
  back to the exact source in
  `separation: {sourceOfficialRequestId, sourceSha256}`. Download and playback
  use the same API and authority checks as other tracks. Publish only after
  validation; never alter the source result or repurpose its digest.

## Verification and rollout

Validate unknown project, pending/missing source, hash mismatch, no separator
or Cover provider, retry exhaustion, exact audio length and independent
publication. A successful synthetic integration is not proof that a real
song's vocals are inaudible or that its arrangement is unchanged. For a real
acceptance test, compare the source and new take around the former vocal
entries, especially melody continuity, rhythm, percussion attacks, ambience,
and any audible voice residue. Preserve the originally published recording.
Enable the playlist action only after RS, Planner, PDS/Filex and the deployed
audio worker all support the route and its local model files are verified.
