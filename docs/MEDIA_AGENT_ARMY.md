# AI Army Media & Monetization Corps

This subsystem extends Provider-v3 without changing the existing seven core engineering roles. Media roles are stable identities; model bodies and external creative providers remain replaceable.

## Goal

Build a monetization-oriented media factory that can research public signals, plan content, transcribe and edit authorized media, create missing assets, package platform-specific variants, publish only after explicit approval, read measured analytics, and feed results into the next experiment.

The control loop is:

`Research -> Strategy -> Script -> Optional Generative Assets -> Transcription -> Edit -> Captions/Localization -> Thumbnail -> Rights/Safety -> Approval -> Publish -> Analytics -> Monetization Optimization`

## Why this is separated from the core engineering army

The engineering army owns repository correctness. The media corps owns content-production tasks. External media tools never receive repository write credentials. A broken transcription or editing provider must not stop repository work, and a social-platform outage must not trigger generic paid fallback.

Reasoning agents and service connectors are intentionally different things. `TRANSCRIPTION_AGENT` or `CLIP_EDITOR_AGENT` is a durable role identity; Descript, Fal and Runway are replaceable execution surfaces. Installing a plugin never grants paid-execution permission by itself.

## Social research and publishing

### Post Bridge

Post Bridge is the preferred action connector for YouTube, X/Twitter and Instagram publishing. It also exposes analytics for supported owned posts. Connection state is checked at execution time. A platform is not considered ready just because another platform is connected.

Publishing is a hard boundary. The PUBLISHING_AGENT may prepare a complete publish package, but it may not execute the connector unless explicit human approval exists for that publication.

### X

Public X information can be gathered through normal public-web research. Direct X API use is optional because the API is pay-per-use. If later enabled, it must have a credit/cost ceiling and cannot become a generic paid fallback. Recent-search evidence should be cached and deduplicated to avoid repeated paid reads.

### YouTube

YouTube publishing should prefer the connected Post Bridge account when available. YouTube Data API access can be added for search/metadata and quota-aware workflows, but quota state must be measured rather than assumed.

### Instagram

Direct Instagram API use is limited to supported professional-account surfaces. Do not build consumer-account scraping into the organization. Post Bridge remains the preferred publishing action surface when the Instagram account is connected.

## Descript, Fal and Runway

### Descript

Descript is the preferred semantic editing/transcription connector after free transcription routes. It covers transcription, filler removal, audio cleanup, captions, highlight clips, translation and conversational video editing. It is selected before billable generative video tools when it can solve the task.

### Fal

Fal is the default approved generative-media connector for missing image/video/audio assets because it can route across multiple media models. Connection alone is not sufficient: any potentially billable generation/edit operation remains blocked until the mission has explicit media-cost approval.

### Runway

Runway is the advanced-video specialist for higher-value generation and transformation such as background removal, reframing/aspect expansion, localization, upscaling and multi-shot video. It is not the bulk default and remains cost-gated even when installed and connected.

## Audio and transcription

Preferred routing is intentionally cost-aware:

1. `FREE_GPU_WHISPER` using `openai/whisper-large-v3-turbo` on an available free GPU environment such as Hugging Face Space, Colab or Kaggle.
2. Groq Whisper only when free quota is verified.
3. Connected Descript for transcription/editing workflows.
4. Groq Whisper low-cost route only after explicit budget approval.

The transcript must preserve timestamps and source provenance so downstream clip selection and subtitles remain auditable.

## Video editing

The deterministic executor is FFmpeg. AI agents should generate an edit decision/specification; FFmpeg performs trims, concat, crop, resize, caption burn-in, normalization and frame extraction. This keeps repetitive rendering cheap and reproducible.

For higher-level semantic editing, the preferred route is Descript first. Fal and Runway can be selected only when their plugin connection is present and explicit media-cost approval exists. A plugin being installed is evidence of capability, not authorization to spend.

## Creative generation

`GENERATIVE_MEDIA_AGENT` creates only assets missing from an approved brief. Owned footage and deterministic edits are reused first. For video generation, Fal is the normal approved specialist and Runway is the advanced-video specialist. For image-only work, native ChatGPT image generation can remain the low-friction first route where appropriate. Generated assets must retain provenance for the later rights/disclosure review.

## Upper-agent roles

NVIDIA remains an architecture/review commander for high-value media-system decisions and conflict resolution. DeepSeek remains the explicitly approved paid engineering specialist for hard pipeline design and debugging. Neither should be spent on routine caption cleanup, metadata variants or repetitive classification; those tasks belong to replaceable free workers.

## Monetization loop

The MONETIZATION_AGENT optimizes measured outcomes, not vanity metrics. Core metrics include first-three-second retention, average percentage viewed, completion rate, thumbnail CTR, views per impression, returning viewers, conversion, revenue per thousand views when available, and production cost per published asset.

A recommendation should identify the measured evidence, the hypothesis, the next experiment, the expected signal, and the maximum allowed spend. It must not invent revenue or causal claims.

## Rights and disclosure

Before publishing, RIGHTS_SAFETY_AGENT checks media provenance, music/voice/image rights, privacy, branded-content requirements and synthetic-media disclosures. Unknown rights block publication until resolved. This is intentionally a hard safety boundary because automated scale makes rights mistakes multiply quickly.

## Current connection state

Connection state is runtime data and is not committed to the repository. ChatGPT/Work queries plugin and social-account state immediately before an action. This prevents stale account IDs, usernames or tokens from becoming source code and prevents an installed connector from silently becoming an authorized paid route.
