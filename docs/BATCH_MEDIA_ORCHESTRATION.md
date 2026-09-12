# Bounded Batch Media Orchestration

This standard adds job-level parallelism for independent media/clipping jobs without turning the AI Army into a swarm.

## Decision

The default remains one job. When independent work and resources justify it, the batch scheduler may run up to **three media jobs concurrently**. A fourth job waits for a later wave. Parallelism above three is blocked until a separate policy change is supported by shadow/soak evidence.

The pool is primarily deterministic. Python/FFmpeg/ffprobe, hashing, manifests, rights gates, leases and technical QA do not require an AI worker. Optional replaceable AI specialists may rank highlight candidates, review one hook/payoff decision, clean caption language, create metadata variants, or triage analytics. There is at most one judgmental verifier per clip and no peer debate.

## Safety contract

Each clip keeps its own rights gate. Every mutating job holds a job-scoped Single Writer lease. Manifest commits use compare-and-swap base hashes and atomic replacement. A stale writer is rejected instead of overwriting newer state. Failed jobs preserve verified checkpoints and resume the smallest failed unit; unrelated jobs continue.

Admission is governed by CPU, memory, disk and provider-slot tokens plus the backpressure state. `NORMAL` permits at most 3 jobs, `DEGRADED` at most 1, and `PAUSED` admits none. Transient provider failures have bounded retry; deterministic media, rights, resource, stale-write and invalid-job failures do not loop.

## Promotion target

Three-way execution starts as a measured capability, not a new default. Compare identical fixtures at parallelism 1 and 3. Promotion requires at least 1.5x throughput, no more than 2 percentage points technical-QA regression, no more than 25% cost-per-clip increase, zero rights-gate bypasses, and zero stale-write incidents.

## Runtime surface

- Policy: `config/batch_media_orchestration_policy.json`
- Worker pool: `config/bulk_media_worker_pool.json`
- Job schema: `schemas/batch_media_job.schema.json`
- Scheduler: `scripts/batch_media_scheduler.py`
- Tests: `tests/test_batch_media_scheduler.py`

The scheduler is intentionally media-operation agnostic. Existing or future four-stage clipping handlers plug into it; this keeps clipping semantics separate from concurrency control and lets a single-job pipeline remain unchanged.

## Rollback

Set effective parallelism to 1 or move backpressure to `PAUSED`. Do not delete verified per-job checkpoints. A failed worker must not trigger a destructive batch reset. If lease or CAS integrity fails, stop new admission and preserve all committed outputs for diagnosis.
