# Failure Injection Report

## Scope and safety boundary

This is the Lane B failure-injection review requested for the read-only safety phase.

- No external Provider API was called.
- Modal was not imported for a live operation, contacted, or executed.
- No workflow was dispatched.
- No deploy, publish, merge, push, production-routing change, or paid operation was performed.
- No secret value was read or printed. Test-only environment values were not real credentials and were not written to this report.
- Lane B changed only this report. Other untracked workspace paths were preserved and not edited by this review.

The supplied baseline is PR #40, base main `6c65d35f45539459c428b96769ef240aee8ec2f1`, and GitHub head `a72155b08302ee25ba592dbe474b50c70ee90c8c`. The inspected local checkout was branch `ai-army/provider-v3` at `fb789a12e79994b23608d8c02579dd06a6423a51`. The supplied GitHub head was not present as a local Git object; no network fetch was made. Therefore, this report is evidence for the inspected workspace and does not claim independent verification of the remote PR head.

The worktree was not clean: `artifacts/`, `docs/LIVE_PROBE_PLAN.md`, `docs/PROVIDER_READINESS_A.md`, `schemas/live_probe_plan.schema.json`, `scripts/live_probe_plan.py`, and `tests/test_live_probe_plan.py` were untracked at final inspection. They were treated as pre-existing/unrelated Lane work and were not modified or removed.

## Test execution record

| Run | Result | Evidence |
|---|---:|---|
| Targeted provider, quota, Modal, runtime, validation, routing, and secret tests | PASS: 88/88 | `python -m unittest -v tests.test_provider_controls tests.test_provider_adapters tests.test_free_quota tests.test_modal_cost_guard tests.test_modal_adapter tests.test_agent_runtime tests.test_direct_api_validation tests.test_probe_providers tests.test_commander_routing tests.test_secret_safety` |
| Full local test suite | PASS: 133/133 | `python -m unittest discover -s tests -p 'test_*.py' -q` |
| HTTP 5xx guarded-adapter mock matrix | PASS for 500, 502, 503, 504 | Ephemeral local mock `HTTP_<status>_GUARDED`; no repository test file added |
| Empty/malformed response mock | FAIL for empty OpenAI-compatible choices; PASS for malformed JSON classification | Ephemeral local mock `EMPTY_CHOICES_PROBE`, `MALFORMED_JSON_PROBE` |
| Ledger and idempotency mock checks | PASS for the exercised paths | Ephemeral local mocks `DUPLICATE_SETTLE`, `LEDGER_WRITE_FAILURE`, `WRITE_FAILURE_RESTART_REPLAY`, `IDEMPOTENCY_DUPLICATE_COMPLETE` |
| Missing rate metadata mock | PASS: readiness remained blocked | Ephemeral local mock `RATE_METADATA_MISSING` |

`PYTHONDONTWRITEBYTECODE=1` was used. All test doubles were local; no network transport was enabled.

## Status meaning

- `PASS`: the stated behavior was exercised by an existing deterministic test or an explicitly recorded local mock check.
- `FAIL`: the requested injection was exercised and the observed behavior violates the safety expectation.
- `BLOCKED`: the case is applicable, but this phase has no safe deterministic coverage sufficient to claim PASS.
- `NOT_APPLICABLE`: the relevant execution path is intentionally disabled by policy in this phase.

## Failure-injection matrix

| # | Requested case | Status | Exact evidence | Finding / gap |
|---:|---|---|---|---|
| 1 | 429 | PASS | `tests/test_provider_controls.py::ProviderControlsTests::test_429_opens_only_one_provider_circuit_and_no_retry_is_performed`; `tests/test_provider_adapters.py::ProviderAdapterTests::test_guarded_adapter_opens_only_failed_provider_circuit_and_never_retries`; `tests/test_free_quota.py::FreeQuotaTests::test_hard_stop_and_429_open_the_breaker_without_retry`; `scripts/provider_adapters.py:94-105, 791-807` | The failing provider is blocked and the sibling provider remains closed in the exercised paths. `Retry-After` is preserved; no automatic retry is performed. |
| 2 | 500 | PASS | Ephemeral local mock `HTTP_500_GUARDED`; `scripts/provider_adapters.py:104-105, 791-807`; related quota-counting coverage `tests/test_free_quota.py::FreeQuotaTests::test_reservation_and_pre_send_ledger_are_idempotent` | Normalized to `TEMPORARY_PROVIDER_ERROR`, marked retryable for an upper-layer bounded policy, counted once, and not retried by the adapter. No dedicated committed per-status test exists. |
| 3 | 502 | PASS | Ephemeral local mock `HTTP_502_GUARDED`; `scripts/provider_adapters.py:104-105, 791-807` | Same bounded one-call behavior as 500. The mock did not exercise a real provider or an upper-layer backoff controller. |
| 4 | 503 | PASS | Ephemeral local mock `HTTP_503_GUARDED`; `scripts/provider_adapters.py:104-105, 791-807` | Same bounded one-call behavior as 500. The mock did not exercise provider recovery or half-open probing. |
| 5 | 504 | PASS | Ephemeral local mock `HTTP_504_GUARDED`; `scripts/provider_adapters.py:104-105, 791-807` | Same bounded one-call behavior as 500. The mock did not exercise a real timeout/retry coordinator. |
| 6 | timeout | PASS | `tests/test_agent_runtime.py::IdempotencyTests::test_timeout_retry_cannot_double_write`; `tests/test_modal_adapter.py::ModalAdapterTests::test_timeout_keeps_one_unsettled_record_and_replay_does_not_execute_again`; `scripts/agent_runtime.py:279-313`; `scripts/modal_adapter.py:326-343` | Ambiguous timeout remains non-retryable for the same identity and Modal usage remains unsettled until reconciliation. |
| 7 | connection reset | PASS | Ephemeral local mock `CONNECTION_RESET_REQUEST`; `scripts/provider_adapters.py:240-251` | The request path converts `ConnectionResetError`/`OSError` to bounded `NETWORK_ERROR` handling and does not retry itself. Direct provider-adapter normalization of a raw `ConnectionResetError` is not separately covered. |
| 8 | malformed JSON | PASS | Ephemeral local mock `MALFORMED_JSON_PROBE`; `scripts/provider_adapters.py:51-54, 111-112, 242-249, 343-358` | Classified as `MODEL_OUTPUT_INVALID`; no raw response body is retained. |
| 9 | empty response | FAIL | Ephemeral local mock `EMPTY_CHOICES_PROBE` returned `PROBE_OK` for an OpenAI-compatible payload with `choices: []`; `scripts/provider_adapters.py:280-283, 343-368` | `_chat()` checks only that `choices` is a list, and `probe()` does not require a non-empty choice/message. An empty successful-looking response can therefore pass the minimal probe. This is a readiness-integrity defect. The Gemini-native probe separately checks candidate presence, but that does not protect OpenAI-compatible providers. |
| 10 | NaN cost | PASS | `tests/test_modal_cost_guard.py::ModalCostGuardTests::test_nan_null_negative_and_zero_are_rejected`; `scripts/modal_cost_guard.py:76-93, 145-163` | Non-finite money values are rejected fail-closed. |
| 11 | negative cost | PASS | `tests/test_modal_cost_guard.py::ModalCostGuardTests::test_nan_null_negative_and_zero_are_rejected`; `scripts/modal_cost_guard.py:76-93` | Negative billing and estimate values are rejected. |
| 12 | unknown billing | PASS | `tests/test_modal_cost_guard.py::ModalCostGuardTests::test_unknown_billing_blocks_new_jobs_and_gpu_requires_known_credit`; `tests/test_provider_controls.py::ProviderControlsTests::test_unknown_quota_is_a_stop_condition`; `scripts/modal_cost_guard.py:166-182, 563-620` | Unknown billing/quota prevents new work; GPU additionally requires known credit. |
| 13 | rate metadata missing | PASS | Ephemeral local mock `RATE_METADATA_MISSING` produced `QUOTA_UNVERIFIED`, `FREE_ACCESS_UNVERIFIED`, `GROQ_READY=false`, and `paid_fallback=false`; `scripts/direct_api_validation.py:245-272, 486-505` | The staged readiness path fails closed when no header/account evidence is available. Gap: `scripts/provider_controls.py:215-224` models daily/RPM limits only; TPM/TPD and provider-specific header reservation are not independently enforced here. |
| 14 | duplicate request | PASS | `tests/test_provider_adapters.py::ProviderAdapterTests::test_guarded_adapter_reserves_before_send_and_blocks_duplicate_request`; `tests/test_free_quota.py::FreeQuotaTests::test_reservation_and_pre_send_ledger_are_idempotent`; `scripts/provider_controls.py:226-270` | Duplicate request identity is blocked before the underlying provider call. |
| 15 | duplicate completion | PASS | Ephemeral local mocks `DUPLICATE_SETTLE` and `IDEMPOTENCY_DUPLICATE_COMPLETE`; `scripts/modal_cost_guard.py:751-770`; `scripts/agent_runtime.py:269-277` | Repeated settlement/completion returns the existing result and does not create a second completion. No committed test directly names duplicate settlement. |
| 16 | concurrent reservation | PASS | `tests/test_modal_cost_guard.py::ModalCostGuardTests::test_concurrent_reservations_are_bounded`; `scripts/modal_cost_guard.py:415-433, 680-700` | Two concurrent local guards produce one reservation and one hard-stop result under the shared file lock. |
| 17 | reservation race | PASS | Same deterministic race evidence: `tests/test_modal_cost_guard.py::ModalCostGuardTests::test_concurrent_reservations_are_bounded`; `scripts/modal_cost_guard.py:680-700` | The read/check/write reservation sequence is protected by the local POSIX file lock in the exercised environment. Cross-runner storage semantics are not live-tested in this phase. |
| 18 | ledger write failure | PASS | Ephemeral local mocks `LEDGER_WRITE_FAILURE` and `WRITE_FAILURE_RESTART_REPLAY`; `scripts/modal_cost_guard.py:505-526, 746-763` | Injected write failure is surfaced as `LEDGER_WRITE_FAILED`; the persisted pre-failure reservation remains replayable after restart, preventing a second execution. Actual filesystem failure modes were not induced. |
| 19 | provider途中停止 / provider stops mid-operation | BLOCKED | Existing isolation only covers circuit/error recording: `tests/test_provider_controls.py::ProviderControlsTests::test_429_opens_only_one_provider_circuit_and_no_retry_is_performed`; `tests/test_modal_cost_guard.py::ModalCostGuardTests::test_provider_error_opens_only_modal_circuit` | No deterministic test simulates a provider becoming unavailable during a multi-step mission and verifies checkpoint/resume plus sibling-provider continuity. No PASS claim. |
| 20 | fallback途中停止 / fallback stops mid-operation | NOT_APPLICABLE | Automatic fallback is disabled by policy: `config/provider_registry.json:7-14`; `tests/test_provider_adapters.py::ProviderAdapterTests::test_probe_rejects_model_mismatch_without_fallback`; `tests/test_commander_routing.py::CommanderRoutingTests::test_routes_one_mission_to_one_suitable_commander` | There is no enabled fallback path to inject. A future opt-in fallback must have its own stop/replay tests; it must not be enabled as part of this phase. |
| 21 | Commander cancellation | PASS | `tests/test_agent_runtime.py::HierarchicalRuntimeTests::test_cancel_mission_does_not_affect_other_mission`; `test_cancel_parent_propagates_to_child`; `test_cancelled_sibling_isolated`; `test_completed_result_is_preserved_after_cancel`; `test_double_cancel_is_idempotent`; `scripts/agent_runtime.py:1149-1204` | Mission and descendant scoping, sibling isolation, completed-result preservation, and double-cancel behavior pass locally. |
| 22 | Worker cancellation | PASS | `tests/test_agent_runtime.py::HierarchicalRuntimeTests::test_background_cancel_stops_only_relevant_mission`; `tests/test_modal_cost_guard.py::ModalCostGuardTests::test_cancellation_is_job_and_mission_scoped_and_idempotent`; `scripts/agent_runtime.py:1295-1335`; `scripts/modal_cost_guard.py:772-797` | Cooperative runtime/ledger cancellation is scoped. Actual external Worker process termination is not tested because live execution is prohibited. |
| 23 | stale response | BLOCKED | No deterministic stale-response test or explicit response sequence/monotonic-version guard was located in the inspected runtime/provider paths; only general timestamp/source-version fields were found | The repository prevents some same-identity replays, but this review did not find evidence that an older response cannot overwrite a newer result across distinct arrivals. No PASS claim. |
| 24 | idempotency replay | PASS | `tests/test_agent_runtime.py::IdempotencyTests::test_same_key_same_payload_replays_same_result`; `test_restart_preserves_command_result`; `tests/test_modal_cost_guard.py::ModalCostGuardTests::test_reservation_is_atomic_and_replayable`; `scripts/agent_runtime.py:220-313`; `scripts/modal_cost_guard.py:641-707` | Same identity replays the stored result; changed payload conflicts; timeout/restart paths avoid a second side effect. |

## Findings requiring follow-up

1. **FAIL — empty OpenAI-compatible response acceptance.** Require at least one choice and a valid message/content or tool result before returning `PROBE_OK`/successful generation. Add a deterministic regression test for `choices: []` and empty message content.
2. **BLOCKED — mid-mission provider stop.** Add a local state-machine test for provider interruption after reservation and before/after checkpoint, including sibling-provider isolation and restart from the last normal checkpoint.
3. **BLOCKED — stale response protection.** Define and test a monotonic response/version or command-result acceptance rule so an older arrival cannot overwrite a newer result.
4. **Coverage gap — rate dimensions.** Add provider-specific RPM/RPD/TPM/TPD reservation tests, especially for Groq header absence and `Retry-After` handling. Keep unknown limits fail-closed.
5. **Coverage gap — cross-runner durability.** The Modal test exercises local processes/threads with POSIX locking; it does not prove that the selected shared storage is durable across separate runners.

## Lane B conclusion

The local safety suite is green at 133/133, and most requested failure paths are either covered or fail closed. However, the empty OpenAI-compatible response defect is a real `FAIL`, and the stale-response and mid-mission provider-stop cases remain `BLOCKED`. This report does not authorize readiness promotion, live probing, production routing, or activation.

`GOOGLE_READY=false`

`NVIDIA_READY=false`

`GROQ_READY=false`

`OPENROUTER_WORKERS_READY=false`

`MODAL_READY=false`

`LIVE_PROBE_GOOGLE=false`

`LIVE_PROBE_NVIDIA=false`

`LIVE_PROBE_GROQ=false`

`LIVE_PROBE_OPENROUTER=false`

`LIVE_PROBE_MODAL=false`

`PRODUCTION_ROUTING_CHANGED=false`

`PAID_EXECUTION_COUNT=0`

`DEPLOY_COUNT=0`

`PUBLISH_COUNT=0`

`SECRET_NEW_LEAKS=0` (no secret values were accessed; existing local secret-safety tests passed)

`AWAITING_USER_FINAL_APPROVAL=true`

## Commander follow-up after the Lane B snapshot

The Lane B matrix above was captured before the following local, no-network
follow-up changes. The original `FAIL`/`BLOCKED` labels remain as the
historical observation; current follow-up evidence is recorded here rather
than silently rewriting that run:

| Finding | Follow-up status | Evidence |
|---|---|---|
| Empty OpenAI-compatible response | PASS | `tests.test_provider_adapters`: empty `choices` and empty message tests; Adapter returns `MODEL_OUTPUT_INVALID` |
| Stale response | PASS | `tests.test_agent_runtime`: monotonic `ResponseFreshnessGuard` and Runtime rejection of a stale report |
| Process reservation race | PASS | `tests.test_provider_controls`: two ledger instances share a locked read/check/write path |
| Cross-runner durable backend | BLOCKED | The filesystem backend explicitly reports `cross_runner_durable=false`; a reviewed shared backend is still required |

No Provider was activated by these follow-up changes, and the Live Probe
flags remain false.

## Phase 5 revalidation addendum

The current PR #40 HEAD is `cdc436c8c9efcd01d3281c29f994d064ca226ddb`.
The Phase 5 additions were verified without Provider network access:

- Full local deterministic suite: `157/157 PASS`, `0 FAIL`, `0 ERROR`.
- Redacted catalog snapshot/drift tests: `6/6 PASS`.
- Live Probe plan tests: `6/6 PASS`.
- Secret audit: `SECRET_NEW_LEAKS=0`; the scan reports no values.
- Generated Live Probe plan: 14 bounded requests, cost `UNKNOWN`, retry `0`,
  `auto_execution_allowed=false`, and all live flags false.
- Catalog snapshots are non-mutating, hash-validated, and do not activate a
  newly discovered model. Removed/deprecated/pricing changes fail closed.

No live Provider failure injection was started in this phase. Therefore the
existing `BLOCKED` status for provider interruption/resume and cross-runner
durability remains valid; deterministic local tests do not prove remote
Runner durability or a real Provider outage.

## Phase 6 follow-up

The Phase 6 follow-up was executed as deterministic local/fixture validation;
it did not call a Provider, start Modal, or alter production routing.

| Area | Current result | Boundary |
|---|---|---|
| Empty OpenAI-compatible response | PASS | Empty choices/messages are rejected as `MODEL_OUTPUT_INVALID` |
| Stale response | PASS | Monotonic response version/freshness checks reject stale reports |
| Reservation race | PASS | Local file-backed process locking protects the exercised read/check/write path |
| DAG, ownership, single writer, depth bound | PASS | Bounded staging scheduler; no production writes |
| Checkpoint/resume | PASS | Known local state resumes without replaying settled tasks |
| Provider crash with unknown usage | PASS_SAFE_STOP | Reservation is retained as unsettled; automatic retry/fallback is not performed |
| Mid-mission real Provider outage | BLOCKED | No live Provider call was permitted |
| Cross-runner durable ledger | BLOCKED | Local backend explicitly reports `cross_runner_durable=false` |

The full local deterministic suite is `181/181 PASS`; GitHub Actions for the
final PR HEAD also completed successfully. These results do not satisfy live
catalog, free-tier, quota, or endpoint verification, so no model or Provider
was promoted to READY or ACTIVE.
