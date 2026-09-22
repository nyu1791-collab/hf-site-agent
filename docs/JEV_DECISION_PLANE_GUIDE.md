# Jev Decision Plane Guide

Effective: 2026-09-22

## Purpose

Jev is the AI Army's fast System-One decision plane. It is used for routing,
classification, execution-shape choice, bounded hedging, and lightweight result
triage. It is not a long-form worker and never gains final authority.

## Source guidance incorporated

Primary references:
- https://typesafe.ai/blog/introducing-system-one-models-and-jev
- https://openrouter.ai/labs/jev/compile
- https://openrouter.ai/typesafe/jev-1.13/
- https://evals.typesafe.ai/

The implementation follows these principles:

1. Decompose automation into programmatic rules plus narrow intelligent judgments.
2. Use typed Choice / Noul / Score answers; never free-form routing prose.
3. Ask the fewest questions that capture the required judgments.
4. Keep one narrow coherent judgment per question.
5. Never ask Jev to count or perform arithmetic.
6. Put deterministic rules, quota math, fanout counting, duplicate removal,
   permission checks, and final JSON construction in Python.
7. Batch up to 20 independent records per Decisions request.
8. Consume Choice probabilities and confidence programmatically.
9. Use Jev Latest by default, with the pinned Jev 1.13 only as guarded fallback.
10. Keep Choice cardinality low when code can pre-rank candidates. TypeSafe notes
    that high-cardinality choices can require a two-stage score-then-choice path
    and can slow down.
11. Stage follow-up judgments only when the first-stage result requires them.
    This mirrors TypeSafe's workflow examples where follow-up questions run only
    after earlier decisions make them relevant.

## Routing surfaces

### Legacy compatibility route
Nine questions per record. Retained only for regression comparison.

### Fast route
Routine work uses three questions per record:
- primary worker
- route shape
- secondary worker

A fourth tertiary-worker question is added only when Python proves three
independent workstreams are possible.

### Portfolio route experiment
Python generates a bounded set of safe execution portfolios and Jev answers one
Choice per record. This route may become the default only if live A/B tests show
equal-or-better quality and lower latency than the three-question route.

## Safety boundaries

Jev cannot:
- expand the eligible worker set;
- authorize another paid model;
- mutate secrets or permissions;
- merge a PR;
- deploy or publish;
- override ChatGPT final authority.

The user has authorized paid Jev routing. Auto top-up and generic paid fallback
remain disabled.

## Measured production-routing optimizations

Jev-only live benchmarks on 2026-09-22 found:

- 4 eligible worker candidates: p50 282.710 ms, p95 358.687 ms, primary hit rate 100%.
- 6 candidates: p50 307.748 ms.
- 8 candidates: p50 327.221 ms.
- 12 candidates: p50 317.994 ms.
- Candidate-profile limit 240 chars: p50 271.273 ms, p95 324.185 ms, hit rate 100%.
- 120 chars: p50 274.778 ms.
- 360 chars: p50 285.830 ms.

Canonical routing therefore pre-ranks exact-free workers in Python and exposes
only the top four to Jev for routine routing, with candidate profiles capped at
240 characters.

A 9-vs-3-vs-1 benchmark also showed that fewer questions do not automatically
mean lower tail latency. The one-Choice portfolio route used fewer input tokens
but had worse p95 than the three-question route in one run. This is consistent
with TypeSafe's high-cardinality warning, so routing surfaces are promoted only
from repeated live A/B evidence rather than question-count aesthetics.

The current optimization order is:

1. Python performs deterministic eligibility, health ranking and quota math.
2. Jev sees a small typed decision surface.
3. Python composes the final route.
4. Downstream workers start immediately.
5. Slow hedges use short challenger timeouts.
6. Measured worker/domain health expires and is re-learned instead of becoming
   a permanent static ranking.
