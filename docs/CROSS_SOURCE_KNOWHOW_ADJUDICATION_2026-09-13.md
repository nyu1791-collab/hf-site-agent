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

`>=80` is eligible for canonical adoption only when there is no unresolved hard conflict. `65–79` remains an experiment. A higher-scoring item may also remain experimental when transferability or local calibration is unresolved. Lower scores, duplicated rules, unsafe suggestions, unmeasurable folklore and contradicted rules are held or rejected.

The same numeric heuristic must not be copied across YouTube, TikTok, Meta or other platforms without current evidence for that platform. Missing analytics are `UNKNOWN`, never fabricated as zero or synthetically estimated. Upper-agent output is candidate-generation/red-team evidence, not source authority.

## Evidence base reviewed

The audit cross-checked the existing repository standards against current or primary material from:

- **YouTube Help + YouTube Analytics API** — audience retention, intro, top moments, spikes, dips, impressions/CTR, average view duration, watch-time and machine-readable retention metrics.
- **TikTok For Business / Creative Center** — TikTok-first creative, 9:16 production, UI-safe space, hook/body/close, motion, text, sound and landing-page consistency.
- **TikTok Shop Seller University** — Japan GMV funnel, shoppable-video quality, product demonstration and current-market guidance.
- **Meta for Business** — Reels 9:16/audio/safe-zone creative and A/B testing evidence.
- **OpenAI** — single-agent-first architecture, manager orchestration, composable tools and explicit exit conditions.
- **Anthropic Engineering** — parallel multi-agent research value, large speedups on decomposable research, and substantial token/coordination cost.
- **Google Cloud / Agent Development Kit** — repeatable evaluation datasets, offline/online evaluation, observability and experiment comparison.
- **Microsoft Agent Framework** — provider-agnostic agent/workflow evaluation, task completion, tool selection/input accuracy and tool-call success.
- **NVIDIA NeMo Agent Toolkit** — reproducible workflow evaluation, trajectory evaluation, latency/token/error traces and percentile profiling.
- **Hugging Face smolagents** — minimal abstractions, explicit tool contracts and composable agent/tool patterns.
- **LangGraph** — checkpoint/durability modes and human-interrupt persistence, used as evidence for durability experiments rather than mandatory framework adoption.
- **CrewAI Flows** — structured event-driven control and state around agent crews, supporting the current policy that precise flow control should surround autonomous specialists.
- **Microsoft AutoGen** — multi-agent orchestration patterns as implementation/historical evidence, not a mandatory dependency.
- **ITU-R BS.1770** — loudness and true-peak measurement basis.
- **ACL 2025 MasRouter** — adaptive multi-agent collaboration/model routing as an experiment candidate rather than a production mandate.
- **WhisperX** — VAD plus forced alignment for accurate word timestamps when unknown/third-party speech requires it.
- **Journal of Business Research / Scientific Reports short-video commerce research** — trust, expertise, usefulness, entertainment and purchase behavior, with population/generalizability limits retained.
- **Mature OSS** including LangGraph, CrewAI, AutoGen, Hugging Face smolagents, PySceneDetect and WhisperX.
- **Paid DeepSeek Executive Supervisor run `34735988305`** — six lanes covering AI Army efficiency, video retention/editing, TikTok Shop commerce, HF/GitHub OSS, contradiction red-team and integration/pruning. Used for delta discovery and red-team only.
- **Independent NVIDIA/Google review run `34736405775`** — technically successful, but its mission context was primarily provider/Google staging; non-topical media/commerce conclusions were not promoted.

## Canonical deltas adopted

### 1. Evidence registry and measurable promotion

Permanent know-how keeps source tier, applicability scope and decision status. Advice can be promoted, demoted or rejected as sources change. A model vote cannot outrank current platform policy, standards bodies, machine evidence or a reproducible benchmark.

### 2. AI Army: reproducible system-level evaluation

Any routing/topology improvement must be evaluated against the appropriate single-agent or deterministic baseline on the same fixture and acceptance criteria. Persist effective config, topology, role assignments, provider/model route, tool allowlists, budgets and per-request telemetry. At minimum measure task/acceptance success, verifier pass rate, P50/P95 latency, tokens, requests, tool calls, errors, retries, handoffs, coordination overhead, estimated cost and recovery success.

**New:** architecture/model/tool-routing promotion now requires repeatable **golden fixtures plus redacted real failure cases**. The baseline and variant use the same versioned fixture set. This aligns with Google, Microsoft and NVIDIA evaluation practices and turns production failures into regression coverage instead of anecdotal memory.

**New:** when a deterministic or labeled oracle exists, measure **tool-selection accuracy, tool-input accuracy, tool-call success and unnecessary-tool-call rate**. This strengthens the existing minimal-tool/clear-tool-schema policy without adding another orchestration layer.

Adaptive topology/model routing remains `EXPERIMENT`: research such as MasRouter is promising, but its benchmark gains are not assumed to transfer to this repository. Promotion requires a local ablation showing higher total system value.

Checkpoint durability-mode tuning also remains `EXPERIMENT`: stronger synchronous persistence may be useful around irreversible/approval boundaries, while lower-overhead modes may suit safe regenerable steps, but current checkpoint guarantees cannot be weakened without failure-injection evidence.

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

Prefer clear product identity, real-use/function demonstration, multiple angles or detail shots, synchronized understandable audio/video and a product anchor that matches what is shown. Static product-page image splicing is not the default quality route.

Trustworthiness, expertise and evidence are explicit objectives. “Looks more authentic” is not treated as monotonically better, and fake UGC, fake testimonials, fake scarcity or synthetic customer claims remain blocked.

## Experiment-only deltas

- Adaptive single-agent vs bounded-multi-agent topology/model routing.
- Role-specific quantitative context budgets and compaction triggers.
- Durability-mode tuning by side-effect/recovery risk.
- WhisperX forced alignment as an optional third-party-speech fallback.
- PySceneDetect/content-aware cut detection as a candidate generator before semantic/audio refinement.
- TikTok Shop >30-second duration and 5+ posts/week account/category benchmarks.
- Any platform-specific loudness normalization target until verified or locally calibrated.

## Rejected shortcuts

- Universal scene-change cadence.
- CTR-only optimization.
- Copying TikTok's first-three-second rule to every platform.
- Treating attribution as copyright permission.
- Treating EBU/broadcast loudness as a universal social-platform upload law.
- Generating fake retention data when analytics are unavailable.
- Promoting multi-agent architecture without a single-agent/deterministic baseline.
- Treating an upper-agent answer as evidence authority without primary-source or local measurement support.
- Using a connected paid/freemium video SaaS merely because it is convenient.

## Canonical machine-readable files

- `config/cross_source_knowhow_evidence_matrix.json`
- `config/cross_domain_measurement_registry.json`
- `config/media_command_read_gate.json`
- `config/permanent_standards_manifest.json`
- `config/multi_agent_operating_policy.json`
- `config/longform_video_reliability_policy.json`
- `config/tiktok_shop_influence_policy.json`

This document records the adjudication. Domain policies continue to own operational details; this file must not create a second contradictory copy of those details. New external findings must enter through the evidence scorecard and measurement loop rather than by appending folklore to every playbook.
