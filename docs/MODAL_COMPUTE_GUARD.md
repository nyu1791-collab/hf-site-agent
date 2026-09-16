# Modal Compute Guard

Modal is registered as a separate `COMPUTE_PROVIDER`. It is not an LLM
provider, Commander, Planner, Critic, or OpenRouter Worker. The current
registry state is `REGISTERED`, disabled, unapproved, and denies new jobs.

## Safety contract

- `FREE_ONLY_MODE` is true; paid execution, paid fallback, auto top-up,
  deploy, and publish are false.
- The repository never assumes a fixed free-credit amount. Billing cycle,
  official metered cost, usage limit, remaining credit, and rates must be
  known from the official Modal Workspace billing interfaces.
- New work is evaluated as:

  `official metered cost + local unsettled cost + active reservations + new reservation + safety buffer`

- Money is parsed and persisted as `Decimal` text. `null`, negative values,
  NaN, Infinity, binary floats, invalid ledgers, unknown billing, and unknown
  rates fail closed.
- A reservation is written atomically before an executor is allowed to run.
  The ledger records the mission, job, idempotency key, resource shape,
  maximum estimate, reservation, observed cost, billing status, and timestamps.
- A timeout or failed execution does not become zero cost. It remains
  `UNSETTLED` until official reconciliation. A mismatch remains
  `RECONCILIATION_PENDING` and denies new work.
- A reservation is idempotent by `mission_id + idempotency_key + operation +
  payload_hash`. The same request replays its existing record; a changed
  payload is rejected as `IDEMPOTENCY_CONFLICT`.
- CPU and GPU requests both require a finite maximum-cost estimate. GPU work
  additionally requires known billing and credit facts. There is no default
  GPU probe; the first validation path may use one tiny CPU probe only after
  all gates pass.

Cost bands are configurable in `config/compute_provider_registry.json`:

| State | Exposure band | Default action |
| --- | --- | --- |
| `GREEN` | below 70% | eligible for guarded work |
| `CAUTION` | 70% through below 80% | eligible with the same reservation guard |
| `SOFT_STOP` | 80% through below 90% | deny new compute and background fan-out |
| `HARD_STOP` | 90% or more | deny new compute and open the safety stop |
| `UNKNOWN` | any required fact is unknown or invalid | deny new compute |

The shared ledger requirement is intentional. A local file lock is useful for
tests and one host, but it is not proof of atomicity across independent
GitHub runners. `MODAL_LEDGER_SHARED=true` is only an informational input;
a separately reviewed adapter must provide an explicit durable-store proof
before `ledger_status.ready`, cross-runner locking, or activation can be
considered true. No current workflow supplies that proof.

## Official Modal interfaces used by the adapter

The adapter is lazy and inert by default. When explicitly confirmed in the
manual workflow it uses the documented `modal.Workspace.from_context()` and
the Workspace billing methods `billing.summary(cycle)`, `billing.rates()`,
and `billing.report(...)`. It keeps only bounded, normalized fields and never
stores or prints a raw provider response.

References:

- [Modal Workspace SDK](https://modal.com/docs/sdk/py/latest/Workspace)
- [Modal billing SDK](https://modal.com/docs/sdk/py/latest/billing)
- [Modal budgets](https://modal.com/docs/guide/budgets)
- [Modal Functions](https://modal.com/docs/guide/functions)

The installed SDK version is reported by the validation command. It is not
guessed or written into the registry before a real validation.

## Validation modes

`python scripts/modal_validation.py` is a dry run. It performs no network
request, no Modal SDK billing call, and no compute. It verifies the fail-closed
boundaries and writes a redacted report.

The workflow `.github/workflows/modal-validation.yml` is `workflow_dispatch`
only. A live auth/billing check requires the exact non-secret confirmation
`MODAL_VALIDATION`, an explicit billing cycle, and the two Modal credentials
from the GitHub Secret Store. This session does not dispatch it. The workflow
does not deploy, publish, start a GPU job, or change the registry.

Until the report proves authentication, SDK interface, official billing and
rates, shared ledger/atomic reservation, and failure-injection gates, the
result is `NOT_READY`. The only possible post-validation state is
`READY_FOR_ACTIVATION`; this implementation never writes `ACTIVE` and never
enables production routing.
