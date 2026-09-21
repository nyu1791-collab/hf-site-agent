#!/usr/bin/env python3
"""Fast Jev control-plane overlay for OpenRouter free specialist routing."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from scripts.jev_decision_engine import decide
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
            f"Deterministic lane={lane}; pre-ranked fit={rank}; "
            f"context={context or 'unknown'}; input_modalities={modalities}; "
            f"supports_tools={'tools' in params or 'tool_choice' in params}."
        )
    return profiles


def coordinate(
    task: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    *,
    account_ten_dollar_eligibility_verified: bool = False,
    free_requests_today: int = 0,
    use_jev: bool = True,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Return a safe plan; Jev may refine only the eligible exact-free candidate set."""
    baseline = plan_task(
        task,
        entries,
        account_ten_dollar_eligibility_verified=account_ten_dollar_eligibility_verified,
        free_requests_today=free_requests_today,
    )
    if baseline.get("status") != "READY":
        return {
            "schema_version": "jev-routing-coordinator-v1",
            "status": baseline.get("status"),
            "route_source": "DETERMINISTIC_BASELINE",
            "baseline": baseline,
            "jev": None,
            "final_plan": baseline,
        }

    if not use_jev or bool(task.get("deterministic")):
        return {
            "schema_version": "jev-routing-coordinator-v1",
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
            "schema_version": "jev-routing-coordinator-v1",
            "status": "READY",
            "route_source": "DETERMINISTIC_FALLBACK_AFTER_JEV_UNAVAILABLE",
            "baseline": baseline,
            "jev": jev,
            "final_plan": baseline,
        }

    decision = jev.get("decision") or {}
    if decision.get("action") != "EXECUTE" or decision.get("low_confidence") is True:
        return {
            "schema_version": "jev-routing-coordinator-v1",
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

    selected = list(decision.get("selected_models") or [])
    if not selected or any(model not in candidates for model in selected):
        return {
            "schema_version": "jev-routing-coordinator-v1",
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
        "parallel_model_calls": len(selected) if decision.get("execution_mode") == "PARALLEL" else 1,
        "execution_mode": decision.get("execution_mode"),
        "lane": decision.get("lane") or lane,
        "independent_verification": bool(decision.get("independent_verification")),
        "fanout_reason": ["JEV_SYSTEM_ONE_TYPED_DECISION", *list(baseline.get("fanout_reason") or [])],
        "jev_confidence": decision.get("confidence"),
        "fanout_decision_owner": "chatgpt-top-commander-with-jev-fast-decision-plane",
    }
    return {
        "schema_version": "jev-routing-coordinator-v1",
        "status": "READY",
        "route_source": "JEV_FAST_DECISION_PLANE",
        "baseline": baseline,
        "jev": jev,
        "final_plan": final,
    }


__all__ = ["coordinate"]
