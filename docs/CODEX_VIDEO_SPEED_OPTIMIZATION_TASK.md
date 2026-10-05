# Codex 6.1 Sol Review Brief — Video Fast Path

Status date: 2026-10-05  
Branch: `ai-army/provider-v3`  
PR #40 remains OPEN / DRAFT / UNMERGED.

## Objective

Make routine video generation materially faster without changing the approved script quality, two VOICEVOX speakers, 16:9 AI-briefing presentation, source rules, or READY_TO_PUBLISH boundary.

## Current fast-path invariants

- default execution lanes = 1;
- max independent preparation lanes = 2;
- lane 2 requires explicit expected savings >=10 seconds;
- shared VOICEVOX concurrency = 1;
- FFmpeg final encoder concurrency = 1;
- routine landscape = 1280x720 @ 15 fps;
- one final H.264 encode;
- no routine preview/review/polish pass;
- fail-fast Python + JSON + template checks before heavy setup;
- reuse exact WAV/timing, characters, visuals, fonts and stage checkpoints;
- GitHub-hosted build is fallback/manual, not an automatic build on each code push.

## Code already optimized

`scripts/render_reusable_landscape.py`
- record pointer instead of scanning all dialogue records every frame;
- mouth state updated near 10 Hz;
- bounded composed-frame cache;
- fixed audio gain + limiter inside final encode;
- one H.264 encode.

`scripts/media_speed_orchestrator.py`
- landscape renderer/output contract;
- sequential by default;
- two-lane execution requires measured/declared benefit;
- PARALLEL_TRIPLE is rejected for routine video.

## Gemini 3.8 Flash production integration

Gemini is now a permanent Video Research & Editorial Director, not an optional reviewer. Codex must preserve and strengthen this route:

- policy: `config/gemini_video_director_policy.json`
- runtime: `scripts/gemini_video_director.py`
- guide: `docs/GEMINI_VIDEO_DIRECTOR.md`
- auth: Google Cloud ADC only; no repository API key
- model: `gemini-3.8-flash`
- native YouTube input: one YouTube URL per request
- multiple videos: sequential analysis + deterministic structured merge
- cache identity: model + URL + topic + prompt version
- output feeds mission/script + visual-source/presentation planning
- no second AI review pass
- no automatic paid-provider fallback
- no rerun of successful Gemini research after unrelated downstream failure

Codex should treat YouTube understanding as a production advantage: extract timestamps, demos, UI/screens, claims, visual beats, and Zundamon/Metan editorial cues before VOICEVOX/rendering. Preserve the existing one-lane default, max-two-independent-lanes rule and one final H.264 encode.

## Required Codex audit

Find remaining code paths that violate or bypass those invariants.

### P0
1. Search all configs, tests, validators and workflows for stale 3-lane assumptions.
2. Search for stale `render_reusable_short.py` / 720x1280 pointers used as general or longform authority. Keep them only for explicit portrait shortform.
3. Identify any routine path that encodes H.264 per scene and then concats it. Remove that path from normal execution.
4. Detect any automatic workflow that performs apt/Docker/VOICEVOX/render merely because code was pushed.
5. Compile changed Python and validate JSON before provider/runtime setup in every expensive media workflow.
6. Ensure failed upload/manifest/concat steps do not re-synthesize audio or reacquire assets.

### P1
7. Consolidate Gemini/topic-specific rendering into the shared mission -> voice/timing -> presentation -> `render_reusable_landscape.py` path.
8. Make VOICEVOX WAV caching content-addressed by speaker/style/text/pronunciation/speed/intonation.
9. Cache character-shell extraction and composed character variants.
10. Emit stage timings: preflight, engine readiness, voice synthesis, asset prep, render, probe, upload.
11. Distinguish cold-start and warm-cache timings.
12. Add regression tests for:
   - no parallelism without >=10s benefit;
   - max two prep lanes;
   - one final encode;
   - 15fps routine landscape;
   - no caption clipping;
   - no stale portrait longform authority;
   - syntax failure before heavy setup.

### Guardrails
Do not add multi-agent review, additional runtime services, paid media generation, public publishing, merge, auto top-up, or frame-perfect rerender loops. Optimize the critical path, not worker count.
