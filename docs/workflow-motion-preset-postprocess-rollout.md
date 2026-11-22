# Roll out split 2D motion-preset postprocessing

The Local/Space request remains `motion-preset.convert-2d`; its envelope
and the existing image producer are unchanged. A new plan has one image,
eight independently runnable animation-frame lanes, a CPU-only encode task
after each lane, and a single publish task after all eight encoded outputs.
Encoded MP4s retain the source frame count and frame rate; no background
removal/pixelation is part of these tasks.

## Operator sequence

1. Let any already planned **inline** 2D preset requests reach a terminal
   state on the current release. Do not change, resubmit, or migrate their
   existing tasks. Avoid submitting a new 2D conversion during cutover.
2. Through OPS, apply the Backend/PDS update, then the Animation and
   Postprocess worker updates. Confirm the frame handoff capability is
   available; the ordinary NPC routes remain separate.
3. Apply the Workflow Planner last so its first new 2D request uses the
   split graph only after both worker roles and storage support it.
4. Submit one new preset request to verify a direction's encode can follow its
   own frame task without waiting for the other directions, and that the new
   preset appears only after the eight MP4s are verified.

The old inline animation execution is not retained in the new worker.
Historical status and completion verification of existing records are retained.
No Filex storage/writer, NGS, RS, or Image Worker deployment is required by
this change.
