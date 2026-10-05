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
- The builder persists `STATE.json` and hashes successful audio/timing/visual
  preparation. Resume after downstream admission failure skips synthesis and
  downloads when those hashes match. Different job/mission input is rejected.
- Output and shared-worker flock locks prevent duplicate writers and competing
  heavy jobs. Shared cache defaults to `~/.cache/hf-site-agent`, configurable with
  `HF_VIDEO_CACHE_ROOT` or `--cache-root`; WAVs remain job-scoped and character
  composition cache is shared across worktrees.
- PCM WAV duration is measured from its header instead of launching ffprobe for
  every spoken turn.
- The cloud workflow now supplies the required job ID, rejects invalid mission
  paths before runtime bootstrap, uses the neutral output name, has no external
  `/usr/bin/time` dependency and never cancels an active build to start another.
  Finished MP4 upload uses zero compression and 30-day retention.
- Voice/audio has its own immutable timing checkpoint, so visual failure can
  resume without contacting the engine. An encode-success hash checkpoint is
  committed before the delivery gate; delivery failure resumes only ffprobe and
  completion saving, never the encoder. MP4s without either encode-success or
  completion proof still stop safely and remain untouched.
- Official image caches verify source URL and PNG SHA before reuse. Shared
  image entries are reused for up to 24 hours; the same-job saved image remains
  available during recovery. Timing and image receipts use atomic writes.
- Cloud job checkpoints are keyed by job ID plus mission SHA and saved after
  success/failure. A completed recovered job bypasses tool installation, engine
  startup, character preparation and build. The workflow collects the builder's
  existing completion result instead of running a second ffprobe.
- The legacy fixed-job VM workflow is manual recovery only. Code pushes no
  longer automatically restart that old production job on the small VM.

The latest recovery changes pass 49 related runtime tests and four workflow
contract tests, including embedded shell/Python syntax. No real video has been
rendered again for this maintenance task. VM Git push authentication remains the
user's task; no credential or SSH-key changes are made here.

## Deployment limits

Offline regression tests use saved JSON, mock clients and mock engine processes;
they do not consume Gemini calls, synthesize voice or render a video. Local
related tests: 46 PASS. On 2026-10-05 at 10:49 UTC, independent VM-control
Run [37299011898](https://github.com/nyu1791-collab/-hf-vm-control/actions/runs/37299011898)
installed pinned runtime `daae0491ffd3dd2b3e1a9ff785e3367baaf59475` in
`/home/n_yu1791/hf-site-agent-runs/speed-runtime-daae0491ffd3dd2b3e1a9ff785e3367baaf59475/code`.
It passed 24 relevant offline tests on the actual VM and confirmed resident
VOICEVOX enabled/active, Restart=always/3s. Existing working directories, jobs and
completed media were preserved. No render was started on the small VM.

Shared cache: `/home/n_yu1791/.cache/hf-site-agent`.
Runtime pointer: `~/.local/state/hf-site-agent/video-runtime.json`.
For new production explicitly select that pinned code; installing a worktree
does not change existing running processes or the canonical branch's workflow.
GitHub account `nyu1791-collab` has verified primary email `n.yu1791@gmail.com`;
new commits use it. The VM OS user is `n_yu1791`, home `/home/n_yu1791`, shell
`/bin/bash`. Runner operation is verified; direct SSH login remains unverified.

The existing research worker still stops at `RESEARCH_READY`; automatic editorial
mission construction and dispatch to final render are not completed by this patch.

No IAM/ADC changes, main changes, PR #40 merge, public upload, renderer rewrite,
completed-source reanalysis or completed-video re-encode are part of this change.
