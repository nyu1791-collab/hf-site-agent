# Phase 6 Mission Runtime

This phase adds a provider-call-agnostic execution layer behind the existing
provider adapters. It is enabled only by an explicit caller; it does not
change production routing or activate a model.

The OpenAI-compatible SDK is an optional dependency of the legacy
OpenRouter-only delegation and design-council command lines. Deterministic
tests, provider adapters, ledgers, and the mission scheduler do not require it.
When that optional package is absent, those network-only entry points fail
closed before constructing a client or sending a request; the repository does
not rely on an accidentally preinstalled SDK.

## Safety bounds

- direct corps: Google, Groq, NVIDIA
- maximum direct-corps parallelism: 3
- OpenRouter subordinate workers: 1
- concurrent requests per provider: 1
- maximum delegation depth: 2
- task side effects: `read_only`, `read_only_draft`, or `dry_run`
- paid execution, fallback, top-up, publish, deploy: disabled

`MissionTask` requires an owner corps, provider, capability set, bounded
request/token budgets, a response version, and a nonblank idempotency key.
The `MissionPlan` validator rejects missing dependencies, cycles, invalid
costs, mutation tasks, and depth or fan-out over the initial bounds.

## Reservation and recovery

`MissionReservationLedger` performs an atomic request-plus-token reservation
before a task handler is invoked. A file-backed instance uses a POSIX lock for
separate processes on the same filesystem and atomically replaces its JSON
state. It records `reserved`, `settled`, `released`, and `unsettled` states,
retains unknown provider usage after interruption, and never releases an
uncertain reservation merely because a TTL elapsed.

This is not a distributed cross-runner proof. Until an independently verified
shared-store adapter is supplied, `cross_runner_durable=false` and
`production_parallel_routing_allowed=false` remain mandatory.

`MissionCheckpointStore` records task status, report references, response
versions, reservation references, and provider cancellation state after state
transitions. Completed tasks are not rerun on resume. A dispatched task with
unknown outcome is blocked until explicit reconciliation; it is never silently
replayed.

## DAG and ownership

Only dependency-free tasks are forked. A join task is submitted after all
dependencies complete successfully. A failed, blocked, or cancelled
dependency blocks descendants while preserving successful sibling results.
The scheduler shares provider semaphores across `run_many` missions, so a
second mission cannot bypass the per-provider concurrency limit. Worker output
returns to its owner corps; the scheduler has no peer-to-peer delegation path.
Dispatch also requires an explicitly supplied `HEALTHY`/`CLOSED` provider
state. Missing, degraded, open, or half-open state fails closed and does not
consume a reservation.

## Live Probe gate

`live_probe_gate.py` only evaluates redacted preflight evidence. It requires
exact model identity, endpoint/capability/free/quota evidence, zero estimated
cost, retry zero, paid fallback disabled, top-up disabled, secret presence,
and explicit probe approval. It does not make a network call. Unknown or
missing evidence returns `BLOCKED`.

The Phase 6 expected model IDs are stored in `config/model_registry.json` as
`EXPECTED_UNVERIFIED` records. They are not role candidates, primary models,
fallbacks, ACTIVE models, or readiness evidence. The normal promotion order
remains:

`DISCOVERED → CAPABILITY_CHECKED → COST_CHECKED → PROBED → BENCHMARKED → CANDIDATE → EXPLICIT_APPROVAL → ACTIVE`

`provider_readiness.py` evaluates the model evidence needed for a temporary
`READY` decision without mutating the registry. `READY` never implies
`ACTIVE`; in this phase the active flag and production route remain false.

## Staging

`staging_mission.py` runs a small deterministic fixture graph:

`Google requirements || NVIDIA risk → Groq patch plan → NVIDIA review → Commander integration`

The handlers are local fixture functions. A successful staging report proves
the scheduler, reservation, join, checkpoint, ownership, and safety contracts
only. It is not a provider probe and cannot set any `*_READY` or `*_ACTIVE`
flag.

## Bounded autonomous loops

`autonomous_mission.py` adds the bounded runtime above the scheduler. Each task
executes `EXECUTE -> VALIDATE -> REVIEW`; a failed review may invoke a bounded
revision callback and then re-review the result. Repeated failure signatures
invoke an optional replan callback instead of resending the same attempt
indefinitely. The mission runtime automatically dispatches newly unblocked DAG
tasks and joins independent branches without asking the caller to continue.

The runtime records iteration, revision, replan, request, token, elapsed-time,
lease, heartbeat, generation, and stop-reason state. It checkpoints that state
with the scheduler. A provider interruption retains unknown usage as
`unsettled`; it does not automatically replay the task or launch a speculative
fallback. A mission-level replan is allowed only when no unsettled reservation
is present and only when the Integrator callback returns a validated,
free-only `MissionPlan`; completed tasks are removed from the new generation.

Callback output is untrusted input. The runtime accepts only bounded structured
data, compacts it to a summary/digest, and exposes no repository-write,
deploy, publish, payment, or credential-value permission. The report marks
`user_continue_required=false` and `raw_result_compaction=true`; the offload
ratio is explicitly a runtime proxy, not a ChatGPT product usage measurement.
