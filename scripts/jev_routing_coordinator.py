#!/usr/bin/env python3
"""Fast Jev control-plane overlay for exact-free specialist routing."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from scripts.jev_decision_engine import decide, decide_many, quota_pressure_from_remaining
from scripts.openrouter_free_efficiency_router import load_policy as load_free_policy
from scripts.openrouter_free_efficiency_router import ordered_candidates, plan_task


def _candidate_profiles(
    entries: Sequence[Mapping[str, Any]],
    candidates: Sequence[str],
    lane: str,
) -> dict[str, str]:
    by_id = {str(x.get("id") or ""): x for x in entries if isinstance(x, Mapping)}
    profiles: dict[str, str] = {}
    for rank, model in enumerate(candidates, 1):
        entry = by_id.get(model) or {}
        context = entry.get("context_length")
        arch = entry.get("architecture") if isinstance(entry.get("architecture"), Mapping) else {}
        modalities = arch.get("input_modalities") if isinstance(arch.get("input_modalities"), list) else []
        params = entry.get("supported_parameters") if isinstance(entry.get("supported_parameters"), list) else []
        profiles[model] = (
            f"prevalidated_lane={lane}; deterministic_rank={rank}; "
            f"context={context or 'unknown'}; modalities={modalities}; "
            f"tools={'yes' if ('tools' in params or 'tool_choice' in params) else 'no'}"
        )
    return profiles


def _baseline(
    task: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    *,
    account_ten_dollar_eligibility_verified: bool,
    free_requests_today: int,
) -> dict[str, Any]:
    return plan_task(
        task,
        entries,
        account_ten_dollar_eligibility_verified=account_ten_dollar_eligibility_verified,
        free_requests_today=free_requests_today,
    )


def coordinate(
    task: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    *,
    account_ten_dollar_eligibility_verified: bool = False,
    free_requests_today: int = 0,
    use_jev: bool = True,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Return one safe route. Jev can refine only prevalidated exact-free candidates."""
    baseline = _baseline(
        task,
        entries,
        account_ten_dollar_eligibility_verified=account_ten_dollar_eligibility_verified,
        free_requests_today=free_requests_today,
    )
    if baseline.get("status") != "READY":
        return {
            "schema_version": "jev-routing-coordinator-v2",
            "status": baseline.get("status"),
            "route_source": "DETERMINISTIC_BASELINE",
            "baseline": baseline,
            "jev": None,
            "final_plan": baseline,
        }

    if not use_jev or bool(task.get("deterministic")):
        return {
            "schema_version": "jev-routing-coordinator-v2",
            "status": "READY",
            "route_source": "DETERMINISTIC_BASELINE",
            "baseline": baseline,
            "jev": None,
            "final_plan": baseline,
        }

    free_policy = load_free_policy()
    candidates = ordered_candidates(free_policy, entries, task)[:12]
    remaining = int(baseline.get("remaining_quota_before_plan", 0))
    lane = str(baseline.get("lane") or "GENERAL_REASONING")
    summary = str(task.get("objective") or task.get("task_summary") or task.get("description") or task)
    profiles = _candidate_profiles(entries, candidates, lane)
    jev = decide(
        task_summary=summary,
        candidate_models=candidates,
        remaining_free_quota=remaining,
        candidate_profiles=profiles,
        api_key=api_key,
    )
    if jev.get("status") != "JEV_DECISION_OK":
        return {
            "schema_version": "jev-routing-coordinator-v2",
            "status": "READY",
            "route_source": "DETERMINISTIC_FALLBACK_AFTER_JEV_UNAVAILABLE",
            "baseline": baseline,
            "jev": jev,
            "final_plan": baseline,
        }

    decision = jev.get("decision") or {}
    if decision.get("action") != "EXECUTE" or decision.get("low_confidence") is True:
        return {
            "schema_version": "jev-routing-coordinator-v2",
            "status": "READY",
            "route_source": "CHATGPT_ADJUDICATION_AFTER_JEV",
            "baseline": baseline,
            "jev": jev,
            "final_plan": {
                **baseline,
                "status": "REQUIRES_CHATGPT_ADJUDICATION",
                "jev_action": decision.get("action"),
                "jev_confidence": decision.get("confidence"),
            },
        }

    selected = list(decision.get("workers") or [])
    if not selected or any(model not in candidates for model in selected):
        return {
            "schema_version": "jev-routing-coordinator-v2",
            "status": "READY",
            "route_source": "DETERMINISTIC_FALLBACK_AFTER_JEV_CONTRACT_FAILURE",
            "baseline": baseline,
            "jev": jev,
            "final_plan": baseline,
        }
    final = {
        **baseline,
        "selected_models": selected,
        "primary_model": selected[0],
        "active_model_count": len(selected),
        "parallel_model_calls": len(selected) if decision.get("parallel") else 1,
        "execution_mode": decision.get("execution_mode"),
        "lane": decision.get("lane") or lane,
        "independent_verification": bool(decision.get("independent_verification")),
        "fanout_reason": ["JEV_TYPED_SYSTEM_ONE_DECISION", *list(baseline.get("fanout_reason") or [])],
        "jev_confidence": decision.get("confidence"),
        "fanout_decision_owner": "chatgpt-top-commander-with-jev-fast-decision-plane",
    }
    return {
        "schema_version": "jev-routing-coordinator-v2",
        "status": "READY",
        "route_source": "JEV_FAST_DECISION_PLANE",
        "baseline": baseline,
        "jev": jev,
        "final_plan": final,
    }


def coordinate_many(
    tasks: Sequence[Mapping[str, Any]],
    entries: Sequence[Mapping[str, Any]],
    *,
    account_ten_dollar_eligibility_verified: bool = False,
    free_requests_today: int = 0,
    use_jev: bool = True,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Batch independent routing tasks through Jev with deterministic per-task fallback."""
    baselines: dict[str, dict[str, Any]] = {}
    records: list[dict[str, Any]] = []
    free_policy = load_free_policy()

    for index, task in enumerate(tasks, 1):
        task_id = str(task.get("task_id") or task.get("id") or f"task_{index:04d}")
        base = _baseline(
            task,
            entries,
            account_ten_dollar_eligibility_verified=account_ten_dollar_eligibility_verified,
            free_requests_today=free_requests_today,
        )
        baselines[task_id] = base
        if base.get("status") != "READY" or bool(task.get("deterministic")) or not use_jev:
            continue
        candidates = ordered_candidates(free_policy, entries, task)[:12]
        if not candidates:
            continue
        lane = str(base.get("lane") or "GENERAL_REASONING")
        remaining = int(base.get("remaining_quota_before_plan", 0))
        records.append({
            "id": task_id,
            "task_summary": str(task.get("objective") or task.get("task_summary") or task.get("description") or task),
            "candidate_models": candidates,
            "candidate_profiles": _candidate_profiles(entries, candidates, lane),
            "quota_pressure": quota_pressure_from_remaining(remaining).value,
        })

    jev = decide_many(records=records, api_key=api_key) if records else {
        "status": "JEV_MANY_OK",
        "record_count": 0,
        "batch_count": 0,
        "decisions": {},
    }
    decisions = jev.get("decisions") if isinstance(jev.get("decisions"), Mapping) else {}
    plans: dict[str, Any] = {}

    # Reserve planned free-worker request capacity across the batch so a fast
    # Jev plan does not create work that the free ledger will immediately block.
    remaining_budget = 0
    for base in baselines.values():
        if base.get("status") == "READY":
            remaining_budget = max(remaining_budget, int(base.get("remaining_quota_before_plan", 0) or 0))

    for index, task in enumerate(tasks, 1):
        task_id = str(task.get("task_id") or task.get("id") or f"task_{index:04d}")
        base = baselines[task_id]
        decision = decisions.get(task_id) if isinstance(decisions, Mapping) else None
        if not isinstance(decision, Mapping) or decision.get("action") != "EXECUTE" or decision.get("low_confidence"):
            plans[task_id] = base
            continue
        selected = list(decision.get("workers") or [])
        if not selected:
            plans[task_id] = base
            continue
        if remaining_budget <= 0:
            plans[task_id] = {**base, "status": "BLOCKED_FREE_QUOTA_PLANNED_EXHAUSTED", "selected_models": []}
            continue
        if len(selected) > remaining_budget:
            selected = selected[:remaining_budget]
        remaining_budget -= len(selected)
        parallel = bool(decision.get("parallel")) and len(selected) > 1
        plans[task_id] = {
            **base,
            "selected_models": selected,
            "primary_model": selected[0],
            "active_model_count": len(selected),
            "parallel_model_calls": len(selected) if parallel else 1,
            "execution_mode": "PARALLEL" if parallel else ("SINGLE" if len(selected) == 1 else "SEQUENTIAL"),
            "lane": decision.get("lane") or base.get("lane"),
            "independent_verification": bool(decision.get("independent_verification")),
            "jev_confidence": decision.get("confidence"),
            "planned_free_requests_reserved": len(selected),
            "remaining_batch_free_request_budget": remaining_budget,
            "fanout_reason": ["JEV_BATCH_TYPED_DECISION", *list(base.get("fanout_reason") or [])],
        }

    return {
        "schema_version": "jev-routing-coordinator-batch-v1",
        "status": "READY",
        "task_count": len(tasks),
        "jev": jev,
        "plans": plans,
    }


__all__ = ["coordinate", "coordinate_many"]
