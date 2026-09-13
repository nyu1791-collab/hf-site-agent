# Cross-Source Know-How Adjudication

**Status:** Permanent evidence and measurement companion  
**Effective:** 2026-09-13 JST  
**Scope:** AI Army efficiency, video editing/retention/quality, clipping, and TikTok Shop commerce.

## Decision rule

External advice does not become a permanent rule merely because it appears in an official platform guide, academic paper, mature OSS repository, or an upper-tier AI review. Every candidate is scored in `config/cross_source_knowhow_evidence_matrix.json` on a 100-point scale:

- Evidence strength: 30
- Measurability: 20
- Expected operational impact: 20
- Novelty versus current canonical standards: 10
- Implementation/operating cost: 10
- Safety, rights and policy fit: 10

`>=80` is eligible for canonical adoption only when there is no unresolved hard conflict. `65–79` remains an experiment. Lower scores, duplicated rules, unsafe suggestions, unmeasurable folklore and contradicted rules are held or rejected.

The same numeric heuristic must not be copied across YouTube, TikTok, Meta or other platforms without current evidence for that platform. Missing analytics are `UNKNOWN`, never fabricated as zero or synthetically estimated.

## Evidence base reviewed

The audit cross-checked the existing repository standards against current or primary material from:

- YouTube Help — audience retention, intro, top moments, spikes, dips, impressions/CTR and watch-time interpretation.
- TikTok For Business — TikTok-first creative, 9:16 production, UI-safe space, hook/body/close, movement, text and sound.
- TikTok Shop Seller University — Japan GMV funnel, shoppable-video quality, product demonstration and current-market guidance.
- Meta for Business — Reels 9:16/audio/safe-zone creative and A/B testing evidence.
- OpenAI — single-agent-first architecture, manager orchestration, composable tools and explicit exit conditions.
- Anthropic Engineering — parallel multi-agent research value and its substantial token/coordination cost.
- NVIDIA NeMo Agent Toolkit — reproducible workflow evaluation, effective configuration snapshots, trajectory evaluation, latency/token/error traces and percentile profiling.
- ITU-R BS.1770 — loudness and true-peak measurement basis.
- ACL 2025 MasRouter — adaptive multi-agent collaboration/model routing as an experiment candidate rather than a production mandate.
- WhisperX — VAD plus forced alignment for accurate word timestamps when unknown/third-party speech requires it.
- Journal of Business Research and other empirical short-video commerce research — trust, expertise, product information and purchase behavior, with population/generalizability limits retained.
- Mature OSS including LangGraph, AutoGen, Hugging Face smolagents, PySceneDetect, WhisperX and pyannote.
- Paid DeepSeek Executive Supervisor run `34735988305`, used as delta discovery/red-team evidence rather than ground truth.
- Independent NVIDIA/Google review run `34736405775`; useful for proving bounded review execution, but its mission context was primarily Google staging and therefore it was not treated as substantive media/TikTok evidence.

## Canonical deltas adopted

### 1. Evidence registry and measurable promotion

Permanent know-how now keeps source tier, applicability scope and decision status. Advice can be promoted, demoted or rejected as sources change. A model vote cannot outrank current platform policy, standards bodies, machine evidence or a reproducible benchmark.

### 2. AI Army: reproducible system-level evaluation

Any routing/topology improvement must be evaluated against the appropriate single-agent or deterministic baseline on the same fixture and acceptance criteria. Persist effective config, topology, role assignments, provider/model route, tool allowlists, budgets and per-request telemetry. At minimum measure task/acceptance success, verifier pass rate, P50/P95 latency, tokens, requests, tool calls, errors, retries, handoffs, coordination overhead, estimated cost and recovery success.

Adaptive topology/model routing remains `EXPERIMENT`: research such as MasRouter is promising, but its benchmark gains are not assumed to transfer to this repository. Promotion requires a local ablation showing higher total system value.

### 3. Video: analytics-to-edit closed loop

When real platform analytics exist, export or record them as a retention feedback artifact and join time ranges to scene IDs and edit features. On YouTube, interpret intro retention, top moments, dips and spikes; a late top moment is evidence to test earlier payoff placement, while a spike is not automatically positive because it can also signal confusion.

Packaging metrics are paired: CTR cannot promote a title/thumbnail/cover variant without retention or average-view-duration guardrails. Clickbait is not a successful optimization.

No universal rule such as “cut every N seconds” is allowed. Shot changes, overlays, motion and pacing are hypotheses whose value is judged by content comprehension, retention and platform analytics.

### 4. Video: technical perceptual QA

In addition to decode/subtitle/media-contract checks, record integrated loudness, true peak, clipping events, silence ratio, black-frame events, freeze-frame events and UI-safe-zone collisions when tooling supports them. ITU loudness measurement is a measurement basis, not proof that an EBU broadcast target is the correct upload target for YouTube/TikTok/Meta.

Safe-zone logic is canonical, but exact insets are versioned platform data. Never hardcode one platform's UI geometry as a universal mobile-video safe zone.

### 5. TikTok Shop Japan: optimize the funnel, not vanity metrics

Use the Japan Seller University funnel:

`GMV = Impressions × Product CTR × CVR × AOV`

Every creative experiment declares which bottleneck it targets. A variant that raises views while hurting product CTR/CVR, returns, refunds, complaints or policy compliance is not automatically a winner.

Current official guidance around the first three seconds remains a TikTok priority and pre-publish checkpoint, not a cross-platform immutable law. Japan guidance that strong shopping videos tend to exceed 30 seconds and top sellers publish 5+ per week is retained as an experiment benchmark, not a mandatory quota.

### 6. TikTok Shop: product proof before simulated authenticity

Prefer clear product identity, real-use/function demonstration, multiple angles or detail shots, synchronized understandable audio/video and a product anchor that matches what is shown. Static/PDP-image splicing is not the default quality route.

Trustworthiness, expertise and evidence are explicit objectives. “Looks more authentic” is not treated as monotonically better, and fake UGC, fake testimonials, fake scarcity or synthetic customer claims remain blocked.

## Experiment-only deltas

- Adaptive single-agent vs bounded-multi-agent topology/model routing.
- Role-specific quantitative context budgets and compaction triggers.
- WhisperX forced alignment as an optional third-party-speech fallback.
- PySceneDetect/content-aware cut detection as a candidate generator before semantic/audio refinement.
- TikTok Shop >30-second duration and 5+ posts/week account/category benchmarks.
- Any platform-specific loudness normalization target until verified or locally calibrated.

## Rejected shortcuts

- Universal scene-change cadence.
- CTR-only optimization.
- Copying TikTok's first-three-second rule to every platform.
- Treating attribution as copyright permission.
- Treating EBU broadcast loudness as a universal social-platform upload law.
- Generating fake retention data when analytics are unavailable.
- Promoting multi-agent architecture without a single-agent/deterministic baseline.
- Using a connected paid/freemium video SaaS merely because it is convenient.

## Canonical machine-readable files

- `config/cross_source_knowhow_evidence_matrix.json`
- `config/cross_domain_measurement_registry.json`
- `config/media_command_read_gate.json`
- `config/multi_agent_operating_policy.json`
- `config/longform_video_reliability_policy.json`
- `config/tiktok_shop_influence_policy.json`

This document records the adjudication. Domain policies continue to own their operational details; this file must not create a second contradictory copy of those details.
