# Speed-first code maintenance (2026-10-05)

User priority: speed first, quality second. Run the existing approved video path
once. Do not add AI reviews, manual previews, cosmetic corrections, repeated
verification passes, or re-encoding to adjust a small duration difference. Retain
the minimum mechanical delivery gate and repair only failed/unusable output or
material factual/rights errors.

This code change is based on canonical `ai-army/provider-v3` at `50b1046`.
That revision already has `sys.executable` child calls, resident VOICEVOX setup,
job-scoped research checkpoints, neutral output names and a pre-render duration
gate. Those implementations and the successful renderer are preserved.

## Targeted fixes

- Fully cached Gemini research resumes without constructing an SDK client or
  accessing ADC. If only synthesis is missing, only synthesis gets a client.
- Research JSON and final completion JSON use atomic replace and fsync, so an
  interrupted write cannot replace a prior successful checkpoint with partial JSON.
- A corrupt in-flight research checkpoint becomes `UNKNOWN_RESULT`, preserving
  the no-automatic-resend rule. Changing the job configuration topic cannot reuse
  the original source-plan identity.
- Completed MP4 reuse compares recorded bytes and SHA-256. Hashing reads bounded
  chunks rather than loading the entire MP4 into memory.
- An MP4 without a completion checkpoint is preserved and stops before voice or
  encoding. Its completion must be recovered explicitly; this change does not
  automatically label a possibly interrupted encode as complete.
- Builder paths are resolved before child processes change working directories.
- The final mechanical check requires a nonempty MP4, audio/video streams,
  positive duration and 1280×720. It adds no preview or duration-polish pass.
- A healthy VOICEVOX endpoint can be reused without a local Engine executable.
  Thread auto-selection remains capped at four; the resident small-VM service's
  existing explicit one-thread configuration is unchanged.

## Deployment limits

Offline regression tests use saved JSON, mock clients and mock engine processes;
they do not consume Gemini calls, synthesize voice or render a video. These tests
prove code behavior, not live VM deployment. The managed `/workspace` environment
is distinct from `instance-20261001-071545`; no VM service deployment is claimed.
The existing research worker still stops at `RESEARCH_READY`; automatic editorial
mission construction and dispatch to final render are not completed by this patch.

No IAM/ADC changes, main changes, PR #40 merge, public upload, renderer rewrite,
completed-source reanalysis or completed-video re-encode are part of this change.
