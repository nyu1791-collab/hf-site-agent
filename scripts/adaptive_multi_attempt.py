#!/usr/bin/env python3
"""Best-of-N executor callbacks for important AI-army tasks.

Normal work remains single-shot. Important work gets two independent executor
attempts and critical work gets three when provider evidence supports that
redundancy. Attempts are serialized for one provider binding so this layer
cannot bypass the scheduler's same-provider concurrency invariant. Independent
provider corps may still run in parallel.

For Google's focused FREE_TIER route, when account-specific zero-cost evidence
is still unavailable, the first real commander task is deliberately one primary
attempt with bounded transport recovery instead of Best-of-N. This avoids using
optional quality redundancy to create a rate-limit storm on an unknown-quota
free route. Once stronger account evidence exists, normal adaptive redundancy is
restored automatically.

If a later optional attempt is interrupted after at least one valid executor
result already exists, the runtime keeps that completed result and stops. A
successful result that already required transport recovery also suppresses
optional Best-of-N calls for that task. Ambiguous interrupted calls are never
replayed.
"""

from __future__ import annotations

from typing import Any, Mapping

from scripts.adaptive_performance_policy import profile_for_task
from scripts.autonomous_mission import TaskLoopCallbacks
from scripts.mission_scheduler import ProviderInterrupted
from scripts.resilient_live_call import call_model_with_bounded_recovery
import scripts.live_staging_runner as live_runner


def _sequence_len(value: Any) -> int:
    return len(value) if isinstance(value, (list, tuple)) else 0


def _candidate_score(candidate: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
    """Prefer valid, actionable, tested proposals without model self-scoring."""
    if candidate.get("output_invalid") is True:
        return (-1, 0, 0, 0, 0)
    proposal = str(candidate.get("proposal") or "")
    summary = str(candidate.get("summary") or "")
    return (
        1,
        min(_sequence_len(candidate.get("tests")), 8),
        min(_sequence_len(candidate.get("risks")), 8),
        min(_sequence_len(candidate.get("files_affected")), 12),
        min(len(proposal) + len(summary), 20_000),
    )


def _google_free_route_single_primary(executor: Any) -> bool:
    """Return true only for the real focused Google route with unknown account cost."""
    if not isinstance(executor, live_runner.LiveAgentBinding):
        return False
    policy = executor.execution_policy
    return (
        executor.provider_id == "google"
        and policy.scope == "STAGING"
        and policy.staging_free_route_allowed is True
        and policy.account_zero_cost_verified is not True
        and policy.paid_fallback is False
    )


def _used_transport_recovery(result: Mapping[str, Any]) -> bool:
    attempts = result.get("provider_attempts")
    return (
        (isinstance(attempts, int) and not isinstance(attempts, bool) and attempts > 1)
        or int(result.get("conclusive_5xx_recovery_count", 0) or 0) > 0
        or int(result.get("post_5xx_rate_limit_recovery_count", 0) or 0) > 0
    )


def build_adaptive_executor_reviewer_callbacks(executor, reviewer, *, metrics=None) -> TaskLoopCallbacks:
    """Build callbacks with importance-aware, provider-safe executor redundancy."""
    executor.validate()
    reviewer.validate()
    if executor.role != "EXECUTOR" or reviewer.role != "REVIEWER":
        raise live_runner.LiveStagingError("EXECUTOR_REVIEWER_ROLE_MISMATCH")
    if executor.model_family == reviewer.model_family:
        raise live_runner.LiveStagingError("REVIEWER_MODEL_FAMILY_MUST_DIFFER")
    metrics = metrics or live_runner.LiveCallMetrics()

    def execute(task, context: Mapping[str, Any]) -> dict[str, Any]:
        profile = profile_for_task(
            role=task.role,
            risk_level=task.risk_level,
            complexity_level=task.complexity_level,
            metadata=task.metadata,
        )
        profile_attempts = max(1, min(3, profile.attempts))
        free_route_single_primary = _google_free_route_single_primary(executor)
        attempts = 1 if free_route_single_primary else profile_attempts

        def one(index: int) -> dict[str, Any]:
            attempt_context = dict(context)
            phase = str(context.get("phase") or "EXECUTE")
            attempt_context["phase"] = f"{phase}:independent-attempt-{index}"
            attempt_context["performance_attempt"] = index
            attempt_context["performance_attempts"] = attempts
            return call_model_with_bounded_recovery(
                executor,
                task,
                attempt_context,
                metrics=metrics,
                instruction=(
                    "You are the staging Executor. Produce an independent solution, not a paraphrase of "
                    "another attempt. Continue through the current project until its acceptance gates are "
                    "satisfied; do not stop merely because one substep finished. Return JSON only with "
                    "summary, proposal, files_affected, tests, risks, and next_action. Optimize for "
                    "correctness, implementation completeness, maintainability, performance and test "
                    "coverage. Do not claim repository writes or execution."
                ),
            )

        indexed_results: list[tuple[int, dict[str, Any]]] = []
        interrupted_after_success = False
        interrupted_signature = ""
        transport_recovery_suppressed_optional = False
        for index in range(1, attempts + 1):
            try:
                value = one(index)
                indexed_results.append((index, value))
                if _used_transport_recovery(value) and index < attempts:
                    # The provider already needed extra network attempts to
                    # produce one usable answer. Treat that as enough sampling
                    # for this task and preserve quota for later project phases.
                    transport_recovery_suppressed_optional = True
                    break
            except ProviderInterrupted as exc:
                valid_so_far = [
                    item for _, item in indexed_results
                    if item.get("output_invalid") is not True
                ]
                if not valid_so_far:
                    # No usable work exists. Preserve the original checkpoint
                    # semantics and never replay an uncertain call.
                    raise
                interrupted_after_success = True
                interrupted_signature = str(exc)[:240]
                break
            except Exception as exc:
                indexed_results.append((index, {
                    "output_invalid": True,
                    "summary": "independent attempt failed before a provider result was adopted",
                    "failure_signature": type(exc).__name__,
                    "requests_used": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }))

        valid = [(index, item) for index, item in indexed_results if item.get("output_invalid") is not True]
        chosen_index, chosen_value = max(valid or indexed_results, key=lambda pair: _candidate_score(pair[1]))
        results = [item for _, item in indexed_results]
        total_requests = sum(int(item.get("requests_used", 0) or 0) for item in results)
        total_input = sum(int(item.get("input_tokens", 0) or 0) for item in results)
        total_output = sum(int(item.get("output_tokens", 0) or 0) for item in results)
        chosen = dict(chosen_value)
        chosen["requests_used"] = total_requests
        chosen["input_tokens"] = total_input
        chosen["output_tokens"] = total_output
        chosen["performance_profile"] = profile.name
        chosen["independent_attempts_profile_requested"] = profile_attempts
        chosen["independent_attempts_requested"] = attempts
        chosen["independent_attempts_completed"] = len(valid)
        chosen["attempt_execution_mode"] = "SERIAL_SAME_PROVIDER"
        chosen["selection_method"] = "DETERMINISTIC_BEST_OF_N"
        chosen["google_free_route_single_primary"] = free_route_single_primary
        chosen["optional_attempt_interrupted_after_valid_result"] = interrupted_after_success
        chosen["optional_attempts_suppressed_after_transport_recovery"] = transport_recovery_suppressed_optional
        if interrupted_after_success:
            chosen["optional_attempt_interruption"] = interrupted_signature
            chosen["further_attempts_suppressed"] = True
        elif transport_recovery_suppressed_optional:
            chosen["further_attempts_suppressed"] = True
        chosen["alternative_attempt_summaries"] = [
            str(item.get("summary") or "")[:400]
            for result_index, item in indexed_results
            if result_index != chosen_index
        ][:2]
        return chosen

    def revise(task, context: Mapping[str, Any]) -> dict[str, Any]:
        return execute(task, context)

    def validate(task, context: Mapping[str, Any]) -> dict[str, Any]:
        candidate = context.get("candidate") if isinstance(context.get("candidate"), Mapping) else {}
        forbidden = any(bool(candidate.get(key)) for key in ("repository_write", "deploy", "publish", "payment", "credential_access"))
        passed = bool(candidate.get("response_digest")) and not forbidden and candidate.get("output_invalid") is not True
        return {
            "passed": passed,
            "summary": "deterministic structured-envelope validation passed" if passed else "structured-envelope validation failed",
            "failure_signature": "LOCAL_VALIDATION_FAILED" if not passed else "",
            "requests_used": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "provider": "local",
            "model": "deterministic-validator",
        }

    def review(task, context: Mapping[str, Any]) -> dict[str, Any]:
        result = call_model_with_bounded_recovery(
            reviewer,
            task,
            context,
            metrics=metrics,
            instruction=(
                "You are the independent staging Reviewer. Aggressively test the chosen proposal for "
                "correctness, races, stale-state bugs, performance regressions, missing tests and simpler "
                "alternatives. Review the whole current-project acceptance contract, not only the most "
                "recent substep. Return JSON only with decision PASS or FAIL, summary, findings, "
                "required_changes, risks, and failure_signature."
            ),
        )
        decision = str(result.get("decision") or result.get("verdict") or "").upper()
        if decision not in {"PASS", "FAIL"}:
            result["decision"] = "FAIL"
            result["failure_signature"] = "REVIEW_SCHEMA_INVALID"
            result["summary"] = "reviewer returned an invalid decision"
        else:
            result["decision"] = decision
        return result

    return TaskLoopCallbacks(executor=execute, validator=validate, reviewer=review, reviser=revise)


__all__ = ["build_adaptive_executor_reviewer_callbacks"]
