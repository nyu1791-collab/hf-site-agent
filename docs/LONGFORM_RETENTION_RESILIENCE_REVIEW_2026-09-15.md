# Long-form Retention + Resilience Review — 2026-09-15

## Purpose

This is the reviewed evidence note behind `config/longform_retention_resilience_policy.json`. It is not a second machine authority. The machine policy and existing long-form reliability contracts remain authoritative.

The user asked for two things together: stronger know-how for roughly 8–12 minute Japanese news/topic explainers, and stronger safeguards so long-running AI/media work does not silently stop or restart from zero. The review intentionally avoids excessive agent fragmentation.

## Review method

1. ChatGPT reviewed current repository policy first so existing rules would not be re-invented.
2. Primary/official sources were checked across YouTube, LangGraph, GitHub Actions, FFmpeg/ffprobe and Anthropic Engineering.
3. A bounded DeepSeek Executive Supervisor second pass ran as workflow run `34956391997` with three compact lanes: editorial/retention, durable execution/recovery, and red-team reconciliation.
4. DeepSeek output was treated as advisory. ChatGPT compared it back to the primary sources and current repository rules before promotion.
5. Only low-complexity, reversible changes that add a measurable contract, recovery action or learning metric were promoted.

## Source findings

### YouTube

- Audience-retention guidance focuses on the intro, top moments, spikes and dips and recommends comparison against videos of similar length. It does not establish one universal retention percentage that every long-form video should pass.
- YouTube Analytics exposes `averageViewDuration` and `averageViewPercentage`; these should be treated as observed channel/video metrics, not universal laws.
- YouTube's native thumbnail Test & Compare is available for public long-form videos and evaluates variants using watch-time share. Controlled comparison is preferable to assuming that a single thumbnail change caused a result.

Primary sources:
- https://support.google.com/youtube/answer/9314415
- https://support.google.com/youtube/answer/141805
- https://support.google.com/youtube/answer/13861714
- https://developers.google.com/youtube/analytics/metrics

### Long-running agent execution

LangGraph's persistence model supports checkpointed state, resume from the last successful boundary, pending writes from successful sibling work, and explicit thread IDs. Its functional API also stresses serializable task outputs and idempotent external operations because interrupted work can be replayed.

Anthropic Engineering's long-running-agent work independently reinforces incremental progress, structured progress artifacts, clean handoffs across context resets, explicit acceptance criteria, and verification before declaring completion. The important lesson for this project is not to add more agents; it is to leave durable state that a fresh context can read without guessing.

Sources:
- https://docs.langchain.com/oss/python/langgraph/persistence
- https://docs.langchain.com/oss/python/langgraph/fault-tolerance
- https://docs.langchain.com/oss/python/langgraph/functional-api
- https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- https://www.anthropic.com/engineering/harness-design-long-running-apps

### Workflow/artifact durability

GitHub Actions distinguishes caches from artifacts. Cache is an accelerator for regenerable data; artifacts preserve produced outputs across jobs/runs. A cache hit must never be treated as proof that a scene checkpoint is valid. GitHub also supports controlled reruns and concurrency management, reinforcing bounded recovery rather than uncontrolled duplicate execution.

Sources:
- https://docs.github.com/en/actions/concepts/workflows-and-actions/dependency-caching
- https://docs.github.com/en/actions/concepts/workflows-and-actions/workflow-artifacts
- https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs
- https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency

### FFmpeg / ffprobe

FFmpeg's concat and segment primitives support scene/chapter-first production, and ffprobe provides machine-readable format/stream inspection. This supports keeping the existing `Scene -> Validate -> Checkpoint -> Join` architecture rather than returning to a monolithic long-form render.

Sources:
- https://ffmpeg.org/ffmpeg-formats.html
- https://ffmpeg.org/ffprobe.html

## ChatGPT adjudication of the DeepSeek review

### Adopt now

1. **8–12 minutes remains a soft envelope, never a padding quota.** Each major beat must add mechanism, stakeholder impact, new evidence, material uncertainty, a credible competing interpretation, necessary directly relevant context, or a next watchpoint.
2. **Retention is learned from comparable videos.** Track average view duration/percentage and map dips/spikes to beats where possible. Do not hard-code a universal retention percentage or fixed cut interval.
3. **Add a heartbeat/progress layer for long-running work.** It exists only for observability/stall detection. It must never become a second correctness authority over content-addressed manifests and validators.
4. **Use stable idempotency keys at replayable/external boundaries.** Where a provider accepts a token, pass it. Where it does not, check verified content-addressed output/receipt before reinvocation.
5. **Make the single-writer invariant explicit.** One mutable scene target per `thread_id` has one writer; stale concurrent promotion is rejected.
6. **Name failure classes.** Transient network/rate-limit/5xx can receive bounded backoff; deterministic/content-contract/fact/rights failures must not receive blind identical retries.
7. **Adopt the circuit-breaker concept but not an arbitrary number.** A threshold must be calibrated. When triggered it stops new scene starts, preserves verified scenes and emits BLOCKED; it does not delete healthy work or switch to paid fallback.
8. **Cross-session handoff is derived from manifests.** A fresh ChatGPT/DeepSeek context should be able to reconstruct run ID, thread ID, last good checkpoint, completed scene hashes, open failure class and next action without trusting conversation memory.
9. **Completion is evidence-based.** Elapsed time is not completion. All required scenes, matching manifests, join, ffprobe/media contract, decode, subtitle coverage and final visual rereview must pass.
10. **Final visual rereview includes the latest user preferences.** White caption text, green frame/accent for Zundamon, pink/magenta frame/accent for Shikoku Metan, and no unintended whole-diagram vertical drift.

### Keep from the existing system

- Scene -> Validate -> Checkpoint -> Join.
- Atomic scene promotion.
- Content-addressed manifests.
- Bounded retries and smallest-failed-unit recovery.
- Cache as accelerator only; artifact/manifest as durable evidence.
- Deterministic rendering and ffprobe/decode machine QA.
- ChatGPT + DeepSeek as the compact research/script judgment pair.
- No generic paid fallback, no auto top-up, no public publish/deploy/merge from this research mission.

### Reject or demote

- Fixed retention percentages such as a universal `60%` pass line.
- Mandatory visual changes/cuts every N seconds.
- Per-shot/per-frame checkpoints without measured need.
- Cache hit as proof of checkpoint correctness.
- Infinite retries/retry storms.
- Monolithic long-form rendering.
- Extra routine reviewer/writer agents.
- New database/lock service before the current single-writer + atomic promotion design proves insufficient.
- Timer-based completion.
- Claims that the current caption colors or stationary diagrams are proven causal retention drivers; they are current format preferences and QA contracts.

## Required validation experiments before harder promotion

The following remain experiments, not hard thresholds: value-gate effect on padding/retention; beat-to-retention mapping; heartbeat stall latency and false positives; forced-retry idempotency; cache-cleared resume; circuit-breaker calibration; cross-session resume; and concurrent single-writer promotion.

Any experiment that does not improve a measured failure mode or adds more coordination cost than value should be removed or demoted rather than accumulated permanently.

## DeepSeek second-pass record

- Workflow run: `34956391997`
- Conclusion: `success`
- Artifact: `10390299884`
- Artifact digest: `sha256:33db07ec844275755fae52fcb0e99a259af8913f4bab301524bed006f961208a`
- Mission: `missions/deepseek-supervisor/20260915-longform-retention-resilience-review.json`

The report explicitly rejected advisory bloat and recommended narrow additions instead of redesigning the existing reliability architecture.
