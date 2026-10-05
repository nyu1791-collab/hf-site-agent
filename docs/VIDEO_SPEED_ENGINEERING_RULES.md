# Video Speed Engineering Rules

Effective: 2026-10-05  
Authority: `config/media_speed_quality_policy.json`

This document is the human-readable implementation standard for the five-minute video-production target. Speed comes from shortening the critical path, reusing verified work, and avoiding redundant setup. It does **not** come from launching many workers against the same CPU, disk, cache, VOICEVOX engine, or FFmpeg encoder.

## Critical-path rule

Default to **one sequential critical path**. Open a second lane only when both tasks are genuinely independent and mostly I/O/preparation bound. The hard ceiling is two independent lanes for routine video work.

Never parallelize:
- multiple FFmpeg final encoders for the same video;
- multiple jobs against one local VOICEVOX engine;
- writers touching the same cache, timing file, presentation manifest, or output path;
- duplicate agents reviewing the same finished video during the delivery path.

Parallelism must have an expected wall-clock benefit of at least 10 seconds. Otherwise run sequentially.

## Fail fast before expensive setup

Before apt, Docker, VOICEVOX startup, asset downloads, or render:
1. `python -m py_compile` every changed renderer/helper;
2. parse mission JSON;
3. parse template/policy JSON;
4. confirm required paths and output contract;
5. reject invalid subtitle geometry or obviously impossible duration settings.

A syntax or manifest bug must fail in seconds, not after a Docker image pull.

## Reuse order

Use this order:
1. already-running GCP/local runtime with persistent caches;
2. installed local VOICEVOX engine;
3. cached VOICEVOX image/assets;
4. GitHub-hosted fallback only when persistent runtime is unavailable.

Do not upgrade pip on the video critical path. Install only missing packages. Reuse verified character shell, fonts, voice WAVs, visual assets, character variants, and measured timing.

VOICEVOX WAV cache keys must include speaker/style, exact text, pronunciation settings, speed and intonation. Unchanged dialogue must not be synthesized again.

## Rendering

Routine fast path:
- one final H.264 encode;
- no per-scene H.264 encode followed by concat;
- per-scene encoded checkpoints are recovery artifacts only;
- one FFmpeg encoder at a time;
- use the shared landscape renderer instead of topic-specific renderers whenever possible;
- render at 1280x720 for the speed baseline unless the user explicitly asks for a higher master.

Cache repeated frame states. Mouth state only needs to be recomputed around 10 Hz; the final canvas may still render at the configured frame rate. Do not recomposite identical visual/speaker/expression/caption states every frame.

## Measured Gemini 4 one-minute review

The 2026-10-05 one-minute Gemini 4 test had good script/dialogue and the correct horizontal structure, but exposed concrete production defects:
- subtitle lines approached/clipped the lower-third boundary;
- measured audio was about -24.47 LUFS integrated, too quiet for normal web playback;
- explanatory cards were useful but too dominant; current-news videos need at least one primary-source screenshot when available;
- repeated static compositions reduced perceived pace.

Permanent fixes:
- maximum three subtitle lines, bottom safe margin >=34 px, shrink font before clipping;
- target approximately -18 to -16 LUFS for VOICEVOX with a fixed gain + true-peak limiter inside the single final encode; do not add a second loudness pass;
- use official/primary evidence plus original diagrams, not diagrams alone;
- change crop/zoom/evidence on semantic beats without adding cosmetic rerender loops;
- keep both hosts large enough for expression readability while never covering the evidence stage.

## Retry discipline

If a late-stage manifest/concat/upload step fails, reuse completed voice, visuals, timing, and rendered checkpoints. Retry only the failed stage and true dependents. Never restart unrelated successful work.

## Gemini research lane

Gemini 3.8 Flash is part of the routine production pipeline when YouTube/multimodal source understanding materially helps. It is not an extra review pass. Treat its work as the combined **research + editorial/script input stage**.

Speed rules:
- default one Gemini research lane;
- one YouTube URL per request;
- multiple URLs run sequentially by default and merge deterministically;
- reuse cached results keyed by model + URL + topic + prompt version;
- never rerun a successfully cached YouTube analysis merely because a later render/upload stage failed;
- do not add a second model solely to review Gemini output;
- cross-check only material current claims against primary docs;
- if Gemini is unavailable but verified source material exists, continue instead of blocking the video;
- Gemini output must feed mission/script and visual-beat planning before VOICEVOX and the one-pass landscape render.

Runtime: `scripts/gemini_video_director.py`.
Policy: `config/gemini_video_director_policy.json`.

## Gemini production lane

Gemini 3.8 Flash is the default judgmental **research + editorial direction** stage for YouTube-relevant video tasks when its authorized Google Cloud runtime is available. Use its native video understanding to avoid manual transcript/screenshot inspection where possible. Ask it for timestamps, demos, visual beats, 3–5 takeaways, script notes and material limits in one compact structured output.

This does not increase routine parallelism. Default remains one execution lane. Multiple YouTube videos are processed one URL per request and merged deterministically; only use a second independent preparation lane when the normal >=10 second expected wall-clock saving rule is met. Cache Gemini analysis by model + URL + topic + prompt version and reuse it before another paid/cloud call.

Gemini is part of production, not an optional review pass. Do not invoke it again merely to critique a completed video.

## Agent use

Code agents/Codex are for static optimization, tests, profiling, and simplifying the critical path. They must not add:
- extra review passes;
- extra providers;
- extra rendering services;
- more parallel agents by default;
- frame-perfect polish loops.

The success metric is **time to a correct, watchable MP4**, not worker count.


### Multi-source Gemini budget

Gemini's broader production role must not recreate the old over-parallelized pipeline. For each major item, target 3 distinct YouTube videos, cap at 5, send one URL per request, and reuse cached source analyses before any new cloud call. After the per-source analyses, perform one compact Gemini item synthesis that produces dialogue/scene/visual decisions.

Default execution remains one lane. Do not fan out five Gemini calls concurrently merely because five URLs exist. The existing second-lane rule still applies: only use a second independent lane when measured/expected wall-clock gain is at least 10 seconds and there is no shared-state contention.
