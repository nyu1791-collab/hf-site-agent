# Jev Decision Plane Guide

Effective: 2026-09-22

## Purpose

Jev is the AI Army's fast System-One decision plane. It is used for routing,
classification, execution-shape choice, bounded hedging, and lightweight result
triage. It is not a long-form worker and never gains final authority.

## Source guidance incorporated

Primary references:
- https://docs.typesafe.ai/concepts/system-one
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
