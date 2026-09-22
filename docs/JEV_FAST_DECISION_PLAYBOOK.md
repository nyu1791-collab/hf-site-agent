# Jev Fast Decision Playbook

**Status:** Permanent operating standard  
**Effective:** 2026-09-22 JST  
**Machine authority:** `config/jev_decision_engine_policy.json`

## Purpose

Jev is the AI Army's fast System-One decision plane directly under ChatGPT. It is used for routing, classification, route-shape selection, bounded fanout and lightweight triage. ChatGPT remains final authority. Python owns deterministic rules, arithmetic, quota handling and final route construction.

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

Use the smallest decision surface that preserves correctness:

1. **0 questions — deterministic health fast path**
   - recent domain evidence makes the primary Worker clear;
   - route shape is also clear in code;
   - Python composes the route directly.

2. **1 question — shape only**
   - primary Worker is clear;
   - Jev decides only route shape.

3. **2 questions — lean routine default**
   - primary is not fully clear;
   - Jev decides primary + route shape;
   - Python chooses complements from health-ranked candidates.

4. **3–4 questions — rich complex route**
   - only for genuinely complex/high-impact routing;
   - third-Worker logic is conditional, not routine.

Do not keep old route surfaces merely for compatibility if measured evidence shows a simpler surface is better.

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

Batching is for independent work only. Shared-state or sequential work stays ordered.

## Worker health

Routing uses recent, expiring, domain-aware Worker evidence:
- successes;
- quality failures;
- 429s;
- transport failures;
- latency;
- domain-specific performance.

Recent evidence may reorder eligible Workers but never expand eligibility. Expired evidence is ignored.

Evidence with a missing or invalid expiry is also ignored for routing. Global
evidence may help rank an already eligible Worker, but only current evidence
for the task's actual domain may establish the clear-primary condition used by
the zero-question and shape-only tiers.

A fast recent success may outrank an unknown candidate. A 429, quality failure or severe latency causes temporary/domain-specific demotion.

## Low-confidence handling

Routine low-risk work should not overload ChatGPT merely because Jev confidence is modest. A bounded hedge of at most two Workers may be used when appropriate.

High-impact or authority-sensitive ambiguity returns to ChatGPT.

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
1. verified correctness;
2. total wall-clock time;
3. unnecessary Worker calls and coordination;
4. Jev cost;
5. raw tokens.

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
- Jev latency P50/P95;
- batch size/count;
- confidence/probabilities used;
- selected Workers and route shape;
- Worker latency;
- 429/transport/quality failures;
- fallback reason;
- heavy-model calls avoided;
- unnecessary fanout avoided.

## Continuous improvement

After a meaningful routing change:
1. retain a comparable baseline;
2. use the same fixtures;
3. measure success, P50/P95, requests, tokens and failures;
4. promote only with no verified quality regression;
5. write evidence back to policy;
6. preserve rollback;
7. prune inferior routing surfaces.

The permanent goal is: **the smallest verified decision surface plus the smallest effective Worker set that finishes fastest and correctly**.
