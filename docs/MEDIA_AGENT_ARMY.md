# AI Army Media & Monetization Corps

This subsystem extends Provider-v3 without changing the existing seven core engineering roles. Media roles are stable identities; model bodies remain replaceable, but the execution policy below is fixed unless the user explicitly changes it.

## Goal

Build a monetization-oriented media factory that can research public signals, plan content, create missing assets, generate narration, render long-form video, package platform-specific variants, publish only after explicit approval, read measured analytics, and feed results into the next experiment.

The default control loop is:

`Research -> Strategy -> Script -> Assets -> Zundamon Voice -> Captions -> Scene Render -> Scene Validation -> Concat -> Final Mechanical QA -> Approval -> Publish -> Analytics`

The primary success criterion is not a report. It is a playable finished MP4.

## Why this is separated from the core engineering army

The engineering army owns repository correctness. The media corps owns content-production tasks. Media failures must not stop repository work, and repository changes must not be mixed into rendering work unless explicitly required.

Reasoning agents and rendering executors are intentionally different things. AI agents plan, review and diagnose. Python/FFmpeg perform deterministic media processing.

## Fixed media execution policy

### Prohibited external freemium media SaaS

For video production, do not use Runway, Fal/fal.ai, Descript, VEED, HeyGen, Higgsfield, or similar services whose normal operating model is limited free credits followed by paid credits/subscription usage.

This prohibition applies even when:

- the connector is installed,
- the account is already connected,
- free credits remain,
- a single generation would currently be free.

Do not use these services for video generation, editing, TTS, transcription, subtitling, upscaling, reframing or scene generation.

A different external service may be used only when it is verified at execution time to be completely free for the intended operation, with no automatic billing, no paid fallback and no credit-purchase requirement. Unknown cost state is fail-closed.

### Allowed execution surfaces

Preferred media execution surfaces are:

- Python
- FFmpeg / ffprobe
- Pillow
- OpenCV
- MoviePy when useful as a layer/composition helper
- local/open-source tools
- Colab, Kaggle or GitHub Actions only while the selected path is verified to fit a free execution allowance
- owned assets, rights-cleared free assets, or native/self-generated still images

The renderer must not depend on a billable creative-media connector.

## Audio: Zundamon is the default voice

Japanese narration uses VOICEVOX Zundamon by default. Do not substitute an external paid TTS provider unless the user explicitly changes this rule.

Operational rules:

1. Generate narration by chapter or subtitle block.
2. Cache successful WAV files.
3. Measure the actual duration of each WAV.
4. Use measured audio duration as the timing authority for subtitles and visual duration.
5. Do not regenerate successful narration when only visuals or FFmpeg filters change.
6. Preserve the same voice configuration across scenes unless a deliberate style change is specified.

Zundamon on-screen art, when used, should stay in a stable screen position. Change expression/pose by chapter rather than moving the character around the frame continuously.

## Long-form rendering architecture

Never render a long video as one giant fragile FFmpeg process when it can be split safely.

The required pattern is:

`Scene 01 -> validate -> checkpoint`
`Scene 02 -> validate -> checkpoint`
`...`
`Scene N -> validate -> checkpoint`
`normalized concat -> final.mp4`

Each scene must be normalized to a shared delivery contract before concat:

- 1080x1920
- 30fps
- same video codec
- same audio codec
- same sample rate
- same pixel format

Prefer concat copy when stream parameters match. Re-encode only when necessary.

A failed scene must not invalidate already completed scenes. Retry only the failed scene, with a bounded retry count. Repeated failure with the same cause is escalated to a technical-review agent before the processing method is changed.

## Checkpoints and restart recovery

Persist at least the following state per scene/chapter:

- assets_ready
- voice_ready
- subtitles_ready
- scene_rendered
- scene_validated
- final_concat_done

Successful artifacts are immutable inputs to later retries unless they are themselves proven invalid. A render failure must not trigger new research, a rewritten approved script, new asset collection or regenerated VOICEVOX audio.

## Asset handling

Do not read external image URLs directly during FFmpeg rendering.

Before rendering, materialize required assets into the working area and validate:

- HTTP/download success when applicable
- non-zero and plausible file size
- successful image decode
- reasonable dimensions
- rights/provenance state

A single failed asset should switch to a prepared fallback asset or simplified scene layout. One image failure must not fail the entire long-form video.

## Captions

Captions must cover the full narration. Split long sentences into readable blocks and preserve semantic boundaries.

Use distinct visual hierarchy for:

- main title
- chapter title
- small heading
- body captions

Reserve a fixed safe area so captions do not collide with Zundamon, key visual evidence or mobile-app UI overlays.

## AI role assignment

### ChatGPT — Top Commander

Owns overall planning, task decomposition, source integration, assignment, final decision making, checkpoint policy and delivery of the final video.

### DeepSeek — technical specialist

Use DeepSeek for difficult technical analysis, long-form structure review, FFmpeg/render failure diagnosis and repair proposals. The user has explicitly allowed a paid DeepSeek path for these reasoning/review tasks, subject to the repository's existing budget and safety guards.

Paid DeepSeek permission does **not** authorize paid/freemium media-generation services.

### NVIDIA / Qwen / other specialists

Use for distinct, non-overlapping specialist work such as:

- code review
- error analysis
- subtitle/audio synchronization review
- asset validation strategy
- structure review
- deterministic test design

Do not waste multiple high-capability agents on the same routine task.

### Python / FFmpeg — execution layer

Own the actual repeatable machine work: probing, timing, resizing, normalization, caption burn-in, audio muxing, scene rendering, concat and mechanical final validation.

## DeepSeek paid exception boundary

The approved paid DeepSeek path is advisory/technical only and remains bounded by the currently configured safeguards unless separately changed:

- budget/call limits remain active
- no secret exposure
- STAGING_ONLY where configured
- repository_write=false where configured
- deploy=false
- publish=false
- no generic paid fallback
- no auto top-up

This exception must never be interpreted as permission to spend on creative-media SaaS.

## Social research and publishing

Public information can be researched through normal public-web research and rights-compliant sources. Publishing is a hard boundary: the publishing role may prepare a complete package but must not publish without explicit human approval.

## Monetization loop

The MONETIZATION_AGENT optimizes measured outcomes, not vanity metrics. Core metrics include first-three-second retention, average percentage viewed, completion rate, thumbnail CTR, views per impression, returning viewers, conversion, revenue per thousand views when available, and production cost per published asset.

A recommendation should identify the measured evidence, the hypothesis, the next experiment, the expected signal, and the maximum allowed spend. It must not invent revenue or causal claims.

## Rights and disclosure

Before publishing, RIGHTS_SAFETY_AGENT checks media provenance, music/voice/image rights, privacy, branded-content requirements and synthetic-media disclosures. Unknown rights block publication until resolved.

## Completion gate

Do not delay delivery with unnecessary heavy AI review after deterministic checks pass. At minimum verify:

- final MP4 exists
- file size > 0
- video stream exists
- audio stream exists
- frame size is 1080x1920
- duration is within expected tolerance
- ffprobe can parse the output

After this gate passes, surface the finished playable video first. Detailed internal-agent discussion is secondary.

## Current connection state

Connection state is runtime data and is not committed to the repository. Installed connectors are capability evidence only; they never override this media policy or become authorization to spend.
