# Separate an instrumental from a published BGM

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
- The audio worker applies the selected, pinned **becruily Mel-Band RoFormer
  Instrumental** checkpoint directly to the published WAV. This is
  instrumental inference, not music regeneration, and has no lyrics, creative
  prompt or user-entered request text. The production adapter uses the same
  CPU model, 11-second windows, 50% overlap and smooth overlap-add as the
  accepted listening candidate; it does not mix the unseparated original back.
- Corrections belong in the worker recipe, never in per-song cue timestamps.
  Validate the exact output duration and sufficient headroom. Publish only
  the one instrumental; never normalize or EQ an entire track to disguise
  an isolated separation artifact.
- Publish one new standard WAV and metadata through PDS and Filex under the new
  request ID. The result metadata preserves the concept linkage and is linked
  back to the exact source in
  `separation: {sourceOfficialRequestId, sourceSha256}`. Download and playback
  use the same API and authority checks as other tracks. Publish only after
  validation; never alter the source result or repurpose its digest.

## Verification and rollout

Validate unknown project, pending/missing source, hash mismatch, no separator
provider, retry exhaustion, exact audio length and independent
publication. A successful synthetic integration is not proof that a real
song's vocals are inaudible or that its arrangement is unchanged. For a real
acceptance test, compare the source and new take around the former vocal
entries, especially melody continuity, rhythm, percussion attacks, ambience,
and any audible voice residue. Preserve the originally published recording.
Enable the playlist action only after RS, Planner, PDS/Filex and the deployed
audio worker all support the route and its local model files are verified.
