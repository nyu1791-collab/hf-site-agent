# Long-form Retention + Resilience Review — 2026-09-15

**Role:** Evidence note only. Not a second machine authority.  
**Machine authority:** `config/longform_video_reliability_policy.json`  
**Operational playbook:** `docs/LONGFORM_VIDEO_RELIABILITY_PLAYBOOK.md`

## Review method

The repository was inspected before adding rules so existing Scene -> Validate -> Checkpoint -> Join behavior would not be reinvented. ChatGPT then checked official/primary material across YouTube, LangGraph, Temporal, AWS, Google Cloud, GitHub Actions, FFmpeg/ffprobe, Adobe and Anthropic Engineering. Two bounded DeepSeek Executive Supervisor reviews were also used as advisory second passes; ChatGPT retained final source verification and policy adjudication.

DeepSeek evidence:
- run `34956083092`, artifact `10391441454`, digest `sha256:03024ae2bfce4553b5ee56a6a260d956ef37d338802c78e8cd4b6f2e36f4ecc5`
- run `34956391997`, artifact `10390299884`, digest `sha256:33db07ec844275755fae52fcb0e99a259af8913f4bab301524bed006f961208a`

The second review is retained because it independently highlighted heartbeat observability, replay-safe idempotency, cross-session handoff from structured artifacts, single-writer promotion and the need to simplify harnesses when extra components become overhead. It does not create another policy layer.

## Primary-source findings retained

- YouTube retention: use intro retention, top moments, spikes, dips, average view duration/percentage and comparisons with similar-length videos. Do not invent a universal retention pass percentage. https://support.google.com/youtube/answer/9314415
- YouTube controlled testing: use native/controlled comparison where available instead of attributing a result to one packaging change from a single observation. https://support.google.com/youtube/answer/13861714
- LangGraph persistence/durable execution: checkpoint state, resume from verified boundaries, isolate replayable tasks and make external side effects idempotent. https://docs.langchain.com/oss/python/langgraph/durable-execution
- Temporal: durable execution is based on persisted workflow state and recovery after process/infrastructure failure; this is architecture evidence, not a mandate to adopt Temporal. https://docs.temporal.io/
- AWS retry guidance: timeout + bounded retry + exponential backoff + jitter; avoid retry multiplication across layers. https://aws.amazon.com/builders-library/timeouts-retries-and-backoff-with-jitter/
- Google Cloud retry guidance: retry transient conditions with bounded backoff; permanent failures require correction, not repetition. https://cloud.google.com/storage/docs/retry-strategy
- GitHub Actions: Cache is a regenerable accelerator; Artifact preserves outputs. Concurrency and timeout should prevent uncontrolled overlap/stalls. https://docs.github.com/en/actions
- FFmpeg/ffprobe: Scene/Chapter outputs can be validated and compatible streams joined without unnecessary re-encoding. https://ffmpeg.org/ffmpeg-formats.html and https://ffmpeg.org/ffprobe.html
- Adobe proxy workflow: proxy can reduce editing pressure for heavy sources, but it is a selective accelerator rather than a mandatory step for every Scene. https://helpx.adobe.com/premiere-pro/using/proxy-workflow.html
- Anthropic long-running harness research: incremental progress plus structured artifacts lets a fresh context resume without guessing; testing is required before declaring completion. https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Anthropic 2026 harness design: orchestration components have cost/latency and can become stale as model capability improves; use the simplest harness that preserves measurable quality. https://www.anthropic.com/engineering/harness-design-long-running-apps

## Final adjudication

### Adopt now

- 8–12 minutes is a soft target, never a padding quota.
- Each major section must add verified evidence, mechanism, impact, material chronology, uncertainty/competing interpretation, next watchpoint or necessary context.
- Research + Script stays compact: ChatGPT + DeepSeek. Deterministic media stages do not get extra agents.
- Explicit state manifest, verified checkpoints, atomic Scene promotion and last-known-good preservation.
- Heartbeat/progress is observability only; it cannot override manifest/validator correctness.
- Replayable/external boundaries require stable idempotency behavior.
- One writer per mutable Scene target; stale writer promotion is rejected.
- Retry is owned by one orchestration layer; transient errors get bounded backoff + jitter, permanent errors do not get blind retry.
- No-progress detection and provider circuit breaker stop silent stalls while preserving healthy work.
- Cross-session resume is reconstructed from Repository + verified manifests/artifacts, not conversation memory.
- Completion requires verified Scenes, Join, ffprobe/media contract, full decode, subtitle coverage, Fast Start and final visual rereview.

### Keep experimental, not hard law

- fixed numeric Information Density interval
- universal retention percentage
- fixed chapter count or recap interval
- fixed cut/motion/expression cadence
- numeric circuit-breaker threshold without fixtures
- numeric lease-contention threshold without measurements
- mandatory proxy for all Scenes

### Reject

- padding to reach 8–12 minutes
- monolithic long-form render
- cache hit as checkpoint proof
- multi-layer retry storms
- timer-based completion
- extra routine writer/reviewer agents
- new database/lock service before current Single Writer + atomic promotion proves insufficient
- generic paid fallback or auto top-up

This evidence note is intentionally separate from the machine policy only so the research trail remains auditable. New operating rules belong in the existing long-form policy/playbook rather than creating additional active policy layers.
