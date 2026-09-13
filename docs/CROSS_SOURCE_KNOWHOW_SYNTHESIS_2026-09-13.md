# Cross-Source Know-How Synthesis — 2026-09-13

**Status:** Permanent research synthesis + evidence-governance companion  
**Decision owner:** ChatGPT Top Commander  
**Scope:** AI Army efficiency, video editing/retention/quality, clipping/repurposing, TikTok Shop commerce  
**Machine registries:** `config/cross_source_knowhow_evidence_matrix.json`, `config/cross_domain_measurement_registry.json`

## 1. Why this exists

The repository already contained strong permanent standards. The goal of this research pass was **not** to append every external tip. It was to compare current official platform guidance, academic evidence, mature OSS, production engineering reports, Hugging Face/GitHub implementations and upper-agent reviews against the existing canonical rules, then keep only deltas that are:

1. evidence-backed,
2. measurable,
3. materially useful,
4. non-duplicative,
5. compatible with rights/safety/cost constraints,
6. reversible if real measurements do not reproduce the expected benefit.

The result is a **knowledge → measurement → experiment → promotion/rollback loop**, not a larger pile of folklore.

## 2. Quantitative admission score

Every candidate is scored out of 100:

- Evidence strength: 30
- Measurability: 20
- Expected operational impact: 20
- Novelty vs current canonical rules: 10
- Implementation/operating cost: 10
- Safety/rights/policy fit: 10

Disposition:

- **80–100:** canonical adoption if no unresolved hard conflict
- **65–79:** experiment/shadow mode only
- **<65:** hold/reject

A platform-specific numeric recommendation is never copied to another platform without fresh evidence.

## 3. Source families checked

### Official platform / engineering guidance

- OpenAI — *A practical guide to building agents*  
  https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/
- Anthropic Engineering — *How we built our multi-agent research system*  
  https://www.anthropic.com/engineering/multi-agent-research-system
- NVIDIA NeMo Agent Toolkit — evaluation workflow  
  https://docs.nvidia.com/nemo/agent-toolkit/latest/workflows/evaluate.html
- Hugging Face smolagents — agents / multi-agent orchestration / telemetry-oriented tooling  
  https://huggingface.co/docs/smolagents/index  
  https://huggingface.co/docs/smolagents/en/examples/multiagents
- YouTube Help — audience-retention key moments and analytics guidance  
  https://support.google.com/youtube/answer/9314415
- TikTok For Business — Creative Codes  
  https://ads.tiktok.com/business/en/creative-codes
- TikTok Shop Japan Seller University — seller playbook / content performance  
  https://seller-jp.tiktok.com/university/essay?knowledge_id=5653863148177169  
  https://seller-jp.tiktok.com/university/essay?knowledge_id=4193088864388865
- Meta for Business — Reels ads, vertical/audio/safe-zone and A/B testing guidance  
  https://www.facebook.com/business/ads/facebook-instagram-reels-ads
- ITU-R BS.1770 — loudness and true-peak measurement basis  
  https://www.itu.int/rec/R-REC-BS.1770

### Academic / research

- MasRouter — ACL 2025, dynamic multi-agent collaboration/model routing  
  https://aclanthology.org/2025.acl-long.757/
- WhisperX — time-accurate long-form ASR / VAD / forced alignment  
  https://arxiv.org/abs/2303.00747
- Short-form/TikTok commerce research retained in the evidence matrix for trust, expertise, usefulness and purchase-intention hypotheses. These are used as **supporting evidence**, never as a substitute for platform-native measurements.

### Mature OSS / implementation references

- Microsoft AutoGen  
  https://github.com/microsoft/autogen  
  Note: repository status is maintenance mode; it is treated as historical/implementation evidence rather than a preferred new dependency.
- PySceneDetect  
  https://github.com/Breakthrough/PySceneDetect  
  Useful as a candidate generator for scene boundaries; final clipping still requires semantic/audio refinement.
- Hugging Face smolagents  
  Small agent abstractions, explicit tools, bounded steps and manager/managed-agent patterns corroborate the repository’s existing bounded manager architecture.
- Existing LangGraph/CrewAI/AutoGen adapter research remains governed by the native-runtime-first policy; framework count is not treated as a success metric.

## 4. AI Army findings

### Keep: central manager + bounded specialization

External evidence strongly supports the existing architecture rather than replacing it:

- OpenAI recommends maximizing a single agent first and splitting only when tool/instruction complexity warrants it.
- OpenAI’s manager pattern maps closely to the current `ChatGPT Top Commander → specialists` structure.
- Anthropic reports strong benefits for heavily parallelizable research, but also reports substantially higher token consumption and warns that shared-context/dependency-heavy tasks are poor multi-agent fits.
- Hugging Face’s manager/managed-agent examples corroborate clear role names, descriptions, bounded `max_steps`, and explicit tools.

**Canonical decision:** no unbounded swarm, no peer-to-peer free-for-all, no mandatory paid supervisor hop for trivial work.

### New canonical addition: reproducible evaluation artifacts

Every architecture/routing benchmark must persist:

- effective config hash,
- topology,
- agent roles,
- provider/model bindings,
- tool allowlists,
- budgets/timeouts,
- P50/P95 latency,
- token usage,
- external request count,
- tool-call count,
- retries/429/errors,
- verifier/acceptance outcome,
- checkpoint/recovery outcome.

This extends existing observability into a reproducible benchmark record instead of relying on one-off run impressions.

### Experiment only: adaptive routing

MasRouter supports the idea that collaboration mode, roles and models can be selected dynamically, but local benefit must be proven before changing the deterministic routing facade.

Shadow-test inputs should include:

- parallelizable fraction,
- uncertainty,
- tool intensity,
- shared mutable state risk,
- verification risk,
- provider health,
- expected cost/latency.

Promotion requires measured improvement versus the same fixed fixture set and same acceptance criteria.

### Experiment only: quantitative context compaction

Current context quarantine remains canonical. A future experiment may record role-level useful-context ratio and trigger structured compaction using fixture-calibrated thresholds. No universal token threshold is adopted.

## 5. Video editing / retention / quality findings

### New canonical addition: retention-to-edit feedback loop

When actual platform analytics exist, join analytics back to **scene IDs and edit features**.

For YouTube, record:

- intro retention,
- retention curve,
- top moments,
- dips,
- spikes,
- average view duration,
- average percentage viewed.

Scene feature records should include hook type, story module, visual source type, shot type, caption density, transition type and audio event.

A dip/spike interpretation is a **hypothesis** until replicated. Missing analytics are `UNKNOWN`, never fabricated.

### New canonical addition: CTR must be paired with retention

Packaging CTR is not optimized alone. A high-CTR variant cannot become a winner if average view duration/retention or promise-match worsens materially.

This prevents clickbait-like optimization and directly ties thumbnail/cover/title promises to the first section of the video.

### Keep + strengthen: platform-safe zones

TikTok and Meta both emphasize vertical composition, audio and keeping key messages inside safe areas. The repository already rejected universal fixed pixel margins.

**Canonical decision:** safe-zone QA remains mandatory, but exact insets are versioned per platform/current UI snapshot.

### New canonical addition: measured audio QA, not folklore

Record:

- integrated loudness,
- true peak,
- clipping events,
- silence ratio.

ITU-R BS.1770 is the measurement basis. It does **not** justify forcing a broadcast loudness number onto social platforms. Platform targets require current official evidence or local calibration.

### Experiment only: content-aware scene detection

PySceneDetect-style detectors may generate candidate boundaries. They do not define final edits by themselves. Final boundaries still use speech/semantic beats, visual continuity and story intent.

### Rejected: universal cut-every-N-seconds rule

No permanent “cut every 2/3/5 seconds” rule is adopted. Dynamic editing is useful when it supports attention and comprehension, but fixed cadence is not a substitute for measured retention.

## 6. TikTok Shop findings

### New canonical addition: funnel-stage experiment targeting

For Japan, TikTok Shop Seller University explicitly frames the commercial funnel around:

`GMV = Impressions × Product CTR × CVR × AOV`

Every creative experiment must declare which bottleneck it targets:

- discovery / impressions,
- interest / product CTR,
- consideration / add-to-cart where available,
- purchase / CVR,
- basket / AOV.

A GMV win cannot override return/refund/complaint/policy/claim-evidence guardrails.

### Keep + strengthen: real product proof

Prefer:

- real-use demonstration,
- multiple product angles/details,
- product visible and central,
- synchronized understandable audio/video,
- concrete use scenes.

Static product-page image splicing is not treated as a high-quality default creative.

### Keep + strengthen: trust and expertise over fake “authenticity”

The system should maximize verifiable trustworthiness, product knowledge and evidence. It must not manufacture testimonials, fake UGC, fake scarcity or synthetic customer identity to imitate authenticity.

### Experiment only: >30 seconds and 5+ posts/week

Official Japan material contains useful benchmarks around longer shoppable videos and frequent posting, but these are treated as account/category hypotheses—not mandatory quotas.

### Keep: first three seconds as TikTok priority, not universal law

The first three seconds remain a TikTok-specific attention checkpoint. It is not copied as an immutable rule for YouTube or Meta and is never the sole optimization metric.

## 7. Explicit non-adoptions

The following are rejected or prevented from becoming permanent truth without better evidence:

- universal scene-change interval,
- CTR-only optimization,
- revenue-only creative promotion,
- one platform’s numeric heuristic copied to another platform,
- fabricated/missing analytics filled in by AI,
- broadcast loudness target treated as a social-platform law,
- more agents = automatically better,
- framework stacking as an optimization metric,
- fake UGC/reviews/scarcity/testimonials,
- attribution treated as copyright permission.

## 8. Operational integration

The permanent control plane now uses two shared machine-readable registries:

- `config/cross_source_knowhow_evidence_matrix.json` — evidence, 100-point scoring and adoption/experiment/rejection status.
- `config/cross_domain_measurement_registry.json` — exact metrics, platform scope, baselines, guardrails, promotion and rollback rules.

Media commands must read these before planning/rendering. AI Army architecture changes must use the same evidence/measurement rules before promotion.

This synthesis supplements, rather than duplicates, the existing canonical documents:

- `config/multi_agent_operating_policy.json`
- `config/longform_video_reliability_policy.json`
- `config/tiktok_shop_influence_policy.json`
- `config/authorized_clipping_monetization_policy.json`
- `config/media_command_read_gate.json`

The existing stricter safety, rights, cost and publish gates always win if a new external tip conflicts with them.
