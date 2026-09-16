# Cross-Source Second-Pass Adjudication — 2026-09-13

**Status:** Permanent companion standard  
**Machine policy:** `config/cross_source_second_pass_policy.json`

This is a second adversarial pass over the existing AI Army, video, clipping and TikTok Shop standards. It intentionally avoids repeating the first-pass playbooks. New rules are admitted only when they close a material gap, are measurable, and do not create a contradictory second source of truth.

## Newly adopted gaps

### 1. Untrusted external content is data, not authority

Web pages, search results, emails/messages, tool outputs, external files and model-generated artifacts are `UNTRUSTED_DATA` by default. They may provide evidence, but they may not silently become instructions that override system policy, explicit user intent, canonical repository policy, tool permissions, cost controls or publish/deploy gates.

Before a side-effecting tool call, the controller must check that the planned action still matches the authorized plan. High-impact sinks must be checked for authority and provenance. Security testing must include indirect prompt injection, instruction smuggling through tool results, attempts to manipulate downstream reviewers/graders, context or memory poisoning, data-exfiltration requests and plan drift.

This follows the converging direction in current OpenAI, Microsoft, Google and NVIDIA agent-security guidance: assume some attacks evade detectors and constrain the consequences of a successful injection instead of relying on filtering alone.

### 2. Evaluation artifacts stay provider-independent

Permanent evaluation data must be stored in repository-controlled schemas/artifacts rather than relying on one vendor's hosted evaluation product. Vendor evaluators remain optional adapters.

Every serious architecture/routing/model comparison records the dataset/fixture version, Git SHA, effective config hash, provider/model, reasoning/effort setting when available, tool access and harness version, guardrails/permissions, turn/token/retry/time/cost budgets and randomness controls when available.

Outcome evaluation and process evaluation are separate. Task success can be high while tool selection, tool arguments, policy adherence or plan drift are poor. Conversely, an evaluator that does not support a tool path must return `UNKNOWN`, not `PASS`.

### 3. Claim-level provenance for factual media and commerce

Asset rights/provenance and factual-claim verification are separate ledgers. News/current-events videos, product capability claims, volatile offers, date/numeric facts and third-party explainers require stable claim IDs.

A claim record keeps the normalized claim, claim type, source references, source tier/independence group, source/retrieval timestamps, freshness TTL/expiry, verification/contradiction status and the script/scene/caption spans where that claim appears. A blocking claim that is expired or contradicted blocks publish handoff until refreshed or removed.

C2PA / Content Credentials may strengthen evidence about where an asset came from or how it was transformed. They do **not** prove that the factual statement depicted by that asset is true.

### 4. Experiment validity before declaring a winner

Creative optimization is now governed by experiment-validity gates, not only KPI deltas.

- Pre-register the main hypothesis, primary metric and guardrails.
- Change one primary creative variable when practical.
- Record randomization unit and attribution window.
- Check Sample Ratio Mismatch when randomized allocation exists.
- Do not claim a causal winner while SRM is unresolved.
- Do not repeatedly peek at a fixed-horizon p-value and stop whenever it becomes significant. Early stopping needs a predeclared sequential / always-valid method.
- Prefer native randomized/holdout experiments where available.
- Treat “top-performing videos” and creator leaderboards as hypothesis generation unless a causal experiment supports the conclusion.
- Account for delayed returns/refunds/complaints before promoting a shopping pattern permanently.

YouTube's current native title/thumbnail experiment chooses a winner using watch-time share rather than CTR alone. TikTok's current testing guidance similarly emphasizes predeclared objectives/KPIs, changing one variable at a time, statistical meaning, split testing and conversion-lift experiments.

### 5. Caption semantics and perceptual QA

Full narration coverage remains mandatory, but some videos need more than a transcript. When required for understanding, captions also identify speakers and important non-speech sounds and remain synchronized to the relevant event.

No universal characters-per-line or reading-speed number is added without current platform/accessibility evidence. FFmpeg-based deterministic checks such as black/freeze-frame, silence, loudness/true-peak and safe-zone collision remain preferred where they can be machine-oracled.

VMAF/PSNR/SSIM are **experiment-only reference-based regression metrics**. They can help compare an encode/reframe to a meaningful reference clip, but cannot become a universal social-video quality score. The metric/model/viewing condition must be recorded and no universal publish threshold is defined.

## Confirmed rather than duplicated

The existing DeepSeek supervisor policy already measures accepted-recommendation rate, cost per accepted decision, contradiction detection, duplicate research avoided, latency and coordination overhead, and already instructs the supervisor to stop when additional calls have low expected information gain. The second pass therefore does not create another overlapping supervisor policy; it only adds `marginal_new_evidence_per_additional_call` as an explicit measurement concept in the second-pass companion.

## Rejected shortcuts

The following remain blocked:

- allowing retrieved content to increase its own authority or permissions;
- treating unsupported evaluator coverage as a pass;
- tying the permanent evaluation contract to one hosted vendor product;
- publishing expired/contradicted blocking claims;
- treating C2PA as factual truth proof;
- causal winner claims with unresolved SRM;
- naive repeated-peeking early stopping;
- universal VMAF thresholds;
- declaring an observed high-performing creative pattern causal without an experiment.

## Relationship to existing standards

This file supplements, and does not replace:

- `config/cross_source_knowhow_evidence_matrix.json`
- `config/cross_domain_measurement_registry.json`
- `config/multi_agent_operating_policy.json`
- `config/media_command_read_gate.json`
- `config/longform_video_reliability_policy.json`
- `config/tiktok_shop_influence_policy.json`

If a future platform policy changes, current official evidence wins and this companion must be rescored or demoted rather than silently preserved as folklore.
