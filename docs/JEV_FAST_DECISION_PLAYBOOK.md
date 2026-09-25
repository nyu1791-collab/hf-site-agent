# Jev Fast Decision Playbook

**Status:** Permanent operating standard  
**Effective:** 2026-09-22 JST  
**Machine authority:** `config/jev_decision_engine_policy.json`

## Purpose

Jev is the AI Army's fast System-One decision plane directly under ChatGPT. It is used for routing, classification, route-shape selection, bounded fanout and lightweight triage. ChatGPT remains final authority. Python owns deterministic rules, arithmetic, quota handling and final route construction.

Speed is valuable only after the route is sufficiently reliable. The permanent
priority is **verified route correctness → decision stability and evidence
sufficiency → time to verified completion**. A faster answer, a single recent
success, or a model-reported confidence score alone must not reduce the
decision surface.

## Official Jev operating rules

- Use typed Decisions only: `choice`, `noul`, `score`.
- Never ask Jev for free-text routing prose.
- Ask the fewest narrow questions that capture the judgment.
- Do not ask Jev to count or perform arithmetic.
- Keep the workflow graph and deterministic rules in Python.
- Consume confidence/probabilities in code.
- Batch independent records; current ceiling is 20 records/request and 5 concurrent batches.
- Default to `~typesafe/jev-latest`; keep `typesafe/jev-1.13` as last-known-good rollback.
- Jev may select only from the prevalidated candidate pool.

Official references were reverified on 2026-09-22 from OpenRouter Jev Decisions examples, OpenRouter Jev model pages and TypeSafe System One/Jev documentation.

## Production decision surfaces

Use the smallest decision surface that preserves correctness **and has enough
current domain evidence to be stable**:

1. **0 questions — deterministic health fast path**
   - at least three clean, current domain successes make the primary Worker
     clear, with a success margin over a clean runner-up;
   - route shape is also clear in code;
   - neither a single success nor a latency advantage alone qualifies;
   - Python composes the route directly.

2. **1 question — shape only**
   - the same stable-primary evidence threshold is met;
   - Jev decides only route shape.

3. **2 questions — lean routine default**
   - primary evidence is thin, tied, or otherwise not fully clear;
   - Jev decides primary + route shape;
   - Python chooses complements from health-ranked candidates.

4. **3–4 questions — rich complex route**
   - only for genuinely complex, high-impact, or accuracy-sensitive routing;
   - third-Worker logic is conditional, not routine.

Do not keep old route surfaces merely for compatibility if measured evidence shows a simpler surface is better.

## Media pipeline typed surface (permanent)

For bounded vertical video production, Jev also acts as a narrow **MEDIA_PIPELINE_PROFILE_AND_SHAPE** decision surface. It may choose only one of the four Python-prevalidated profiles in `config/media_speed_quality_policy.json`: `CACHE_INCREMENTAL`, `PARALLEL_PREP`, `FULL_REBUILD`, or `ESCALATE_TO_CHATGPT`. The routine surface is the accuracy-first lean two-question contract (profile plus route shape), and it uses the existing `VISION_AND_MEDIA_UNDERSTANDING` lane.

Jev does not hash files, decide cache invalidation, count lanes, calculate timings or encode passes, select rights, write captions, or compose the final plan JSON. Python (`scripts/media_speed_orchestrator.py`) owns the input manifest, true-dependent invalidation, the maximum-three preparation lanes, dependency joins, the one-pass shortform encode contract, and the final deterministic admission guard. If Jev is unavailable or its typed profile conflicts with a deterministic quality guard, Python keeps the safe deterministic profile; high-risk ambiguity returns to ChatGPT. This use of Jev preserves accuracy and decision stability while removing unnecessary coordination work.

## Candidate and state compaction

Current production defaults:
- shortlist about 4 eligible Workers for routine Jev routing;
- about 240 characters of candidate profile per Worker;
- compact task state, not full chat history;
- quota pressure sent as `AMPLE / LIMITED / CRITICAL`;
- Python derives fanout, duplicate removal, parallel flag and execution mode.

## Batch-first execution

Independent routing work is batch-first:
- up to 20 records per Jev request;
- up to 5 independent Jev batches concurrently;
- 100 independent routing jobs can therefore be represented as five 20-record decision requests.
- mixed route surfaces use two concurrent request groups: `shape` alone and `lean+rich` together; repeated A/B evidence showed better tail stability than three surface requests.

Batching is for independent work only. Shared-state or sequential work stays ordered.

Once an individual route has passed final admission, it may be dispatched from
the batch immediately. The executor prioritizes critical-path and user-visible
work, while tasks with declared dependencies wait for verified prerequisite
artifacts. An unrelated slow Jev record or Worker may not create a global
stage barrier.

## Worker health

Routing uses recent, expiring, domain-aware Worker evidence:
- successes;
- quality failures;
- 429s;
- transport failures;
- latency;
- domain-specific performance.

Recent evidence may reorder eligible Workers but never expand eligibility. Expired evidence is ignored.

Candidate cards include compact domain success count, quality-failure count,
rate-limit count, observed sample count and quality-pass rate. Latency ranks
already safe candidates; it cannot by itself prove that a Worker is correct.

Evidence with a missing or invalid expiry is also ignored for routing. Global
evidence may help rank an already eligible Worker, but only current evidence
for the task's actual domain may establish the clear-primary condition used by
the zero-question and shape-only tiers.

A fast recent success may outrank an unknown candidate. A 429, quality failure or severe latency causes temporary/domain-specific demotion.

## Low-confidence handling

Routine low-risk work should not overload ChatGPT merely because Jev confidence is modest. A bounded hedge of at most two Workers may be used when appropriate.

High-impact or authority-sensitive ambiguity returns to ChatGPT.

When a recent, reliable Primary is unusually slow, reserve one healthy
Challenger but do not launch it immediately. Launch it only after the
domain-calibrated delay if the Primary has not reached verified completion;
the first verified result wins and remaining work is cancelled. The reservation
itself must pass the final eligibility and quota guard before the Primary is
released.

## Final execution admission guard

Every route, including deterministic routing, Jev failure fallback, bounded
hedges, latency challengers and batch plans, passes one Python-only final guard
immediately before release. The guard confirms that selected Workers remain
inside the prevalidated eligible pool, that their count fits reserved free
quota, and that a shared mutable target is serialized even if a later hedge or
challenger was proposed.

Two selected Workers do not by themselves mean independent verification. A
verification route must name a separate verifier role and retain its
verification evidence. A high-risk low-confidence decision remains a ChatGPT
adjudication stop in both single and batch execution.

## Speed objective

Optimize **time to verified correct completion**, not agent count.

Priority:
1. verified route correctness;
2. decision stability and evidence sufficiency;
3. total wall-clock time to verified completion;
4. unnecessary Worker calls and coordination;
5. Jev cost;
6. raw tokens.

Jev is inexpensive enough that it may be used generously when it replaces slower deliberation. Do not call it when Python already knows the answer.

## Current measured routing lessons

Internal A/B evidence is stored in `config/jev_decision_engine_policy.json`. Current promoted lessons include:
- 3-question routing beat the older route;
- 2-question lean routing beat 3-question routing for routine tasks;
- 1-question shape routing beat 2-question routing when primary was already clear;
- primary-only 1-question routing showed a poor P95 tail and is not the routine default;
- 4 candidates outperformed larger candidate sets in the current benchmark;
- ~240-char candidate profiles are the current compact default.

The latest production tiers are therefore:
`ZERO_QUESTION_DETERMINISTIC_HEALTH_FAST_PATH`
→ `SHAPE_ONE_QUESTION`
→ `LEAN_TWO_QUESTION`
→ `FAST_THREE_TO_FOUR_QUESTION`.

## Measured end-to-end multi-agent improvement

The five-domain tuning cycle reached:
- 5/5 correct;
- one Jev batch for five jobs;
- 0 Worker 429s in the final measured cycle;
- no fallback calls;
- about 12.0 seconds total wall time in the final cycle;
- about 23.0 seconds in the prior sequential cycle;
- about 50.4 seconds in the earlier cycle.

Treat these as local evidence, not universal guarantees.

## Observability

Record:
- route surface and question count;
- evidence tier and reason a route could or could not use 0/1 questions;
- Jev latency P50/P95;
- batch size/count;
- confidence/probabilities used;
- selected Workers and route shape;
- Worker latency;
- 429/transport/quality failures;
- fallback reason;
- heavy-model calls avoided;
- unnecessary fanout avoided.
- time to first verified result, Worker TTFT, critical-path duration and
  verifier latency;
- delayed-Challenger reservations, triggers, winner and cancelled work.

## Continuous improvement

After a meaningful routing change:
1. retain a comparable baseline;
2. use the same fixtures;
3. measure verifier-pass rate, route-stability rate, success, P50/P95,
   requests, tokens and failures;
4. promote only with no verified quality or decision-stability regression;
5. write evidence back to policy;
6. preserve rollback;
7. prune inferior routing surfaces.

The permanent goal is: **the smallest evidence-sufficient decision surface plus
the smallest effective Worker set that reaches a verified correct result
quickly**.
