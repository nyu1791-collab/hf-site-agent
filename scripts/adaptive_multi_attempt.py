#!/usr/bin/env python3
"""Best-of-N executor callbacks for important AI-army tasks.

Normal work remains single-shot. Important work gets two independent executor
attempts and critical work gets three. Attempts are serialized for one provider
binding so this layer cannot bypass the scheduler's same-provider concurrency
invariant. Independent provider corps may still run in parallel.

If a later optional attempt is interrupted after at least one valid executor
result already exists, the runtime keeps that completed result, stops issuing
more attempts, and records the interruption as uncertainty. It never replays
the interrupted call. If no valid result exists yet, the interruption still
propagates and checkpoints normally.
"""

from __future__ import annotations

from typing import Any, Mapping

from scripts.adaptive_performance_policy import profile_for_task
from scripts.autonomous_mission import TaskLoopCallbacks
from scripts.mission_scheduler import ProviderInterrupted
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
        attempts = max(1, min(3, profile.attempts))

        def one(index: int) -> dict[str, Any]:
            attempt_context = dict(context)
            phase = str(context.get("phase") or "EXECUTE")
            attempt_context["phase"] = f"{phase}:independent-attempt-{index}"
            attempt_context["performance_attempt"] = index
            attempt_context["performance_attempts"] = attempts
            return live_runner._call_model(
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
        for index in range(1, attempts + 1):
            try:
                indexed_results.append((index, one(index)))
            except ProviderInterrupted as exc:
                valid_so_far = [
                    item for _, item in indexed_results
                    if item.get("output_invalid") is not True
                ]
                if not valid_so_far:
                    # No usable work exists. Preserve the original checkpoint
                    # semantics and never replay an uncertain call.
                    raise
                # A valid result already exists. Keep it, stop optional
                # redundancy, and do not send another provider request.
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
        chosen["independent_attempts_requested"] = attempts
        chosen["independent_attempts_completed"] = len(valid)
        chosen["attempt_execution_mode"] = "SERIAL_SAME_PROVIDER"
        chosen["selection_method"] = "DETERMINISTIC_BEST_OF_N"
        chosen["optional_attempt_interrupted_after_valid_result"] = interrupted_after_success
        if interrupted_after_success:
            chosen["optional_attempt_interruption"] = interrupted_signature
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
        result = live_runner._call_model(
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
