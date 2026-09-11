# AI Army Media & Monetization Corps

This subsystem extends Provider-v3 without changing the existing seven core engineering roles. Media roles are stable identities; model bodies and external creative providers remain replaceable.

## Goal

Build a monetization-oriented media factory that can research public signals, plan content, transcribe and edit authorized media, package platform-specific variants, publish only after explicit approval, read measured analytics, and feed results into the next experiment.

The control loop is:

`Research -> Strategy -> Script -> Transcription -> Edit -> Captions/Localization -> Thumbnail -> Rights/Safety -> Approval -> Publish -> Analytics -> Monetization Optimization`

## Why this is separated from the core engineering army

The engineering army owns repository correctness. The media corps owns content-production tasks. External media tools never receive repository write credentials. A broken transcription or editing provider must not stop repository work, and a social-platform outage must not trigger generic paid fallback.

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

## Audio and transcription

Preferred routing is intentionally cost-aware:

1. `FREE_GPU_WHISPER` using `openai/whisper-large-v3-turbo` on an available free GPU environment such as Hugging Face Space, Colab or Kaggle.
2. Groq Whisper only when free quota is verified.
3. Connected Descript for transcription/editing workflows.
4. Groq Whisper low-cost route only after explicit budget approval.

The transcript must preserve timestamps and source provenance so downstream clip selection and subtitles remain auditable.

## Video editing

The deterministic executor is FFmpeg. AI agents should generate an edit decision/specification; FFmpeg performs trims, concat, crop, resize, caption burn-in, normalization and frame extraction. This keeps repetitive rendering cheap and reproducible.

For higher-level semantic editing, the preferred optional connector order is Descript, Fal, then explicitly approved Runway. These connectors are never auto-paid and are not required for basic operation.

## Creative generation

Thumbnail and visual generation can use ChatGPT image generation or optional Fal/Runway connectors. Paid video generation is a premium specialist route, not a bulk default. A normal mission should first reuse owned footage, deterministic editing and low-cost/free model labor.

## Upper-agent roles

NVIDIA remains an architecture/review commander for high-value media-system decisions and conflict resolution. DeepSeek remains the explicitly approved paid engineering specialist for hard pipeline design and debugging. Neither should be spent on routine caption cleanup, metadata variants or repetitive classification; those tasks belong to replaceable free workers.

## Monetization loop

The MONETIZATION_AGENT optimizes measured outcomes, not vanity metrics. Core metrics include first-three-second retention, average percentage viewed, completion rate, thumbnail CTR, views per impression, returning viewers, conversion, revenue per thousand views when available, and production cost per published asset.

A recommendation should identify the measured evidence, the hypothesis, the next experiment, the expected signal, and the maximum allowed spend. It must not invent revenue or causal claims.

## Rights and disclosure

Before publishing, RIGHTS_SAFETY_AGENT checks media provenance, music/voice/image rights, privacy, branded-content requirements and synthetic-media disclosures. Unknown rights block publication until resolved. This is intentionally a hard safety boundary because automated scale makes rights mistakes multiply quickly.

## Current connection state

Connection state is runtime data and is not committed to the repository. ChatGPT/Work queries the connector immediately before an action. This prevents stale account IDs, usernames or tokens from becoming source code.
