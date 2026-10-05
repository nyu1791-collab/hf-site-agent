# Work 6.1 Sol Review Brief — Video Production Architecture

Status date: 2026-10-05  
Branch: `ai-army/provider-v3`  
PR: keep #40 OPEN / DRAFT / UNMERGED  
Public publishing: disabled; terminal state is READY_TO_PUBLISH.

## User objective

Produce attractive Zundamon + Shikoku Metan explanatory/news videos with **quality:speed = 2:8**. Routine target is approximately **5 minutes wall-clock creation time**, including 8–12 minute longform when practical. Do not trade away the approved presentation, factuality, or two-speaker behavior.

The user explicitly rejected excessive parallelism. More agents/workers are not considered inherently faster.

## Current approved presentation

- 16:9 landscape for explanatory/news videos.
- 1280x720, routine 15 fps; 1080p/30 only as explicit quality master.
- Friendly modern AI briefing atmosphere inspired by the supplied reference's information hierarchy and pacing, but no copied background/trade dress and no morning-show framing.
- Large center evidence stage.
- Metan lower-left, Zundamon lower-right.
- Wide lower-third captions, max 3 lines and bottom safe margin.
- Official/primary source visual when useful/available, plus original explanatory diagrams.
- Two actual VOICEVOX speakers, native mouth states and authored expressions.

## Current speed architecture

Authoritative policy: `config/media_speed_quality_policy.json`

- sequential critical path by default;
- one execution lane by default;
- at most two independent preparation lanes;
- second lane requires >=10 seconds expected wall-clock savings and no shared CPU/disk/cache/VOICEVOX/FFmpeg contention;
- one FFmpeg final encoder;
- one shared VOICEVOX engine;
- no routine AI review loop;
- Python/JSON/template preflight before apt/Docker/VOICEVOX/download/render;
- persistent/cache-first runtime before GitHub-hosted fallback;
- one final H.264 encode;
- verified stage reuse and retry-only-failed-stage.

## Measured findings from Gemini 4 one-minute test

- finished output: about 61.65 seconds, 1280x720.
- render/build step reached 26 seconds in the improved GitHub workflow.
- cold GitHub VOICEVOX Docker pull was a major setup cost.
- earlier iterations lost time to Python syntax / concat-manifest bugs that should have been preflight failures.
- audio measured about -24.47 LUFS; policy now applies fixed gain + limiter in the single encode, aiming approximately -18 to -16 LUFS.
- subtitles needed stricter lower-third safe area.
- explanatory cards were readable but primary-source evidence should appear when available.
- too-static compositions should change evidence/crop/zoom on semantic beats without extra review loops.

## Architecture issues already repaired

1. Three-lane media default reduced to one lane / max two.
2. Longform authority moved from portrait `render_reusable_short.py` to `render_reusable_landscape.py`.
3. Routine landscape baseline reduced from 30 to 15 fps.
4. Shared renderer now uses record pointer, 10 Hz mouth-state updates, bounded composed-frame cache.
5. Audio gain/limiter moved into the single final FFmpeg encode.
6. Expensive topic-specific build workflow made manual-only.
7. CI/read-gate/handoff/manifest are being aligned to stop old three-lane and portrait settings from reappearing.

## Review questions for Work 6.1 Sol

Audit the entire video production architecture and identify every remaining source of wall-clock waste, duplicated authority, brittle handoff, cold-start overhead, or unnecessary agent/runtime hop.

Prioritize:
- one canonical routine video pipeline instead of topic-specific workflows;
- clean ownership of policy vs handoff vs validator;
- persistent-runtime/cache-first execution;
- offline-safe completion when chat/device disconnects;
- stage timing and cold/warm metrics;
- recovery without rebuilding successful stages;
- minimal CI needed before delivery vs heavy canonical CI;
- whether GitHub-hosted fallback should stay fallback-only;
- generic artifact handoff into ChatGPT;
- any state/config drift that could revert landscape/one-lane rules.

Do not propose extra agent review rounds, more routine workers, new paid media providers, auto top-up, public publish, or merge/deploy.
