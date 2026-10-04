# Codex Task — Video Fast Path Optimization

## Goal
Reduce routine video wall-clock production time toward 5 minutes without changing the approved script quality, two-character presentation, 16:9 landscape format, source rules, or public-publish boundary.

## Do not touch successful content
Preserve:
- Zundamon + Shikoku Metan VOICEVOX dialogue;
- approved landscape template;
- current editorial rules;
- existing factual/source gates;
- READY_TO_PUBLISH only; no auto-publication.

## Priority code work

### P0 — eliminate wasted retries
- Add syntax/JSON/template preflight before apt, Docker, VOICEVOX, downloads, or render.
- CI must fail in seconds on Python syntax or malformed mission/template.
- Add a regression test covering the previous unterminated-string / concat-manifest failure class.

### P0 — one encode
- Remove routine per-scene H.264 encode + concat from fast paths.
- Use `scripts/render_reusable_landscape.py` / shared one-pass renderer.
- One final FFmpeg encoder for a normal job.
- Encoded scene chunks are recovery-only, not the default.

### P0 — bounded parallelism
- Default 1 lane.
- Maximum 2 independent preparation lanes.
- VOICEVOX shared engine concurrency = 1.
- FFmpeg final encoder concurrency = 1.
- Only parallelize independent I/O work with expected >=10 s savings.
- Never run duplicate review agents on the critical path.

### P1 — persistent reuse
- Content-addressed VOICEVOX WAV cache.
- Reuse character variant cache, fonts, source visuals, measured timing.
- Prefer persistent GCP/local runtime; GitHub-hosted is fallback.
- Do not `pip install --upgrade pip` on the critical path.
- Install only missing system/python packages.
- Skip Docker pull when a healthy local VOICEVOX or cached image exists.

### P1 — renderer CPU
Optimize `scripts/render_reusable_landscape.py`:
- keep a record pointer instead of scanning every dialogue record for every frame;
- update mouth RMS/state around 10 Hz, not every output frame;
- cache recently composed frame states keyed by visual/speaker/expression/mouth/caption;
- bound raw-frame cache memory (about 24 entries);
- keep one final H.264 encode.

### P1 — quality defects without extra passes
- lower-third must fit max 3 lines with >=34 px bottom safe margin;
- shrink font before clipping;
- add fixed VOICEVOX gain + limiter in the single final encode targeting roughly -18 to -16 LUFS and <= -1.5 dBTP;
- no second loudness render pass.

## Acceptance
- `python3 scripts/validate_media_speed_quality.py` PASS.
- changed Python files compile before runtime bootstrap.
- final MP4 has video + audio streams.
- routine final encode count = 1.
- no per-scene H.264 fast-path encoding.
- measured stage timings are emitted.
- no new paid provider, auto-top-up, merge, deploy, or public publish.
