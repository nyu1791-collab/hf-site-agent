#!/usr/bin/env python3
"""Fast Jev control-plane overlay for exact-free specialist routing."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Mapping, Sequence

from scripts.jev_decision_engine import decide_lean, decide_many_lean, quota_pressure_from_remaining
from scripts.jev_lean_router import decide_lean, decide_many_lean
from scripts.openrouter_free_efficiency_router import load_policy as load_free_policy
from scripts.openrouter_free_efficiency_router import ordered_candidates, plan_task
from scripts.openrouter_worker_health import (
    domain_evidence,
    load_recent_evidence,
    merge_proven_into_candidates,
    profile_suffix,
)

LATENCY_CHALLENGER_TIMEOUT_SECONDS = 3.5


def _candidate_profiles(
    entries: Sequence[Mapping[str, Any]],
    candidates: Sequence[str],
    lane: str,
    evidence: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    domain: str | None = None,
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
            f"tools={'yes' if ('tools' in params or 'tool_choice' in params) else 'no'}; "
            f"{profile_suffix(model, evidence, domain=domain)}"
        )
    return profiles


def _health_ranked_candidates(
    policy: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    task: Mapping[str, Any],
    evidence: Mapping[str, Mapping[str, Any]],
    *,
    max_candidates: int = 12,
) -> list[str]:
    catalog_ids = {
        str(entry.get("id") or "")
        for entry in entries
        if isinstance(entry, Mapping)
    }
    raw = ordered_candidates(policy, entries, task)[:24]
    domain = str(task.get("domain") or task.get("task_class") or "").strip() or None
    return merge_proven_into_candidates(
        raw,
        catalog_model_ids=catalog_ids,
        evidence=evidence,
        domain=domain,
        max_candidates=max_candidates,
    )


def _needs_rich_jev_route(task: Mapping[str, Any]) -> bool:
    return bool(
        _is_high_risk(task)
        or int(task.get("independent_workstreams", 1) or 1) >= 2
        or task.get("complementary_specialization")
        or task.get("independent_verification")
        or task.get("requires_distinct_specialists")
    )


def _is_high_risk(task: Mapping[str, Any]) -> bool:
    return bool(
        task.get("high_impact")
        or task.get("requires_human_approval")
        or task.get("production_side_effect")
        or task.get("secret_access")
    )


def _bounded_low_confidence_hedge(
    task: Mapping[str, Any],
    baseline: Mapping[str, Any],
    candidates: Sequence[str],
    decision: Mapping[str, Any],
) -> dict[str, Any] | None:
    if _is_high_risk(task):
        return None
    chosen: list[str] = []
    for model in [*(candidates[:1]), *list(decision.get("workers") or [])]:
        model = str(model)
        if model and model in candidates and model not in chosen:
            chosen.append(model)
        if len(chosen) >= 2:
            break
    if not chosen:
        return None
    serial = bool(
        task.get("shared_mutable_state")
        or task.get("strictly_sequential")
        or task.get("single_writer_only")
    )
    mode = "SINGLE" if len(chosen) == 1 else ("SEQUENTIAL" if serial else "PARALLEL")
    return {
        **dict(baseline),
        "selected_models": chosen,
        "primary_model": chosen[0],
        "active_model_count": len(chosen),
        "parallel_model_calls": len(chosen) if mode == "PARALLEL" else 1,
        "execution_mode": mode,
        "independent_verification": len(chosen) > 1,
        "jev_confidence": decision.get("confidence"),
        "jev_action": decision.get("action"),
        "fanout_reason": [
            "JEV_LOW_CONFIDENCE_BOUNDED_HEDGE",
            "FAST_PROVEN_OR_DETERMINISTIC_PRIMARY_PLUS_JEV_CANDIDATE",
            *list(baseline.get("fanout_reason") or []),
        ],
    }


def _healthy_latency_challenger(
    selected: Sequence[str],
    candidates: Sequence[str],
    evidence: Mapping[str, Mapping[str, Any]],
    *,
    domain: str | None = None,
    latency_threshold_ms: float = 3000.0,
) -> str | None:
    if not selected:
        return None
    primary = str(selected[0])
    primary_ev = evidence.get(primary) if isinstance(evidence, Mapping) else None
    primary_scoped = domain_evidence(primary_ev, domain)
    try:
        primary_latency = float(primary_scoped.get("avg_latency_ms") or 0.0)
    except (TypeError, ValueError):
        primary_latency = 0.0
    if primary_latency <= latency_threshold_ms:
        return None
    for model in candidates:
        model = str(model)
        if not model or model in selected:
            continue
        raw = evidence.get(model) if isinstance(evidence, Mapping) else None
        scoped = domain_evidence(raw, domain)
        if isinstance(scoped, Mapping):
            if int(scoped.get("rate_limits", 0) or 0) > 0:
                continue
            if int(scoped.get("quality_failures", 0) or 0) > 0:
                continue
            try:
                latency = float(scoped.get("avg_latency_ms") or 0.0)
            except (TypeError, ValueError):
                latency = 0.0
            if latency > 0 and latency >= primary_latency:
                continue
        return model
    return None


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
            "schema_version": "jev-routing-coordinator-v4",
            "status": baseline.get("status"),
            "route_source": "DETERMINISTIC_BASELINE",
            "baseline": baseline,
            "jev": None,
            "final_plan": baseline,
        }

    if not use_jev or bool(task.get("deterministic")):
        return {
            "schema_version": "jev-routing-coordinator-v4",
            "status": "READY",
            "route_source": "DETERMINISTIC_BASELINE",
            "baseline": baseline,
            "jev": None,
            "final_plan": baseline,
        }

    free_policy = load_free_policy()
    evidence = load_recent_evidence()
    candidates = _health_ranked_candidates(free_policy, entries, task, evidence, max_candidates=4)
    remaining = int(baseline.get("remaining_quota_before_plan", 0))
    lane = str(baseline.get("lane") or "GENERAL_REASONING")
    summary = str(task.get("objective") or task.get("task_summary") or task.get("description") or task)
    domain = str(task.get("domain") or task.get("task_class") or lane).strip() or None
    profiles = _candidate_profiles(entries, candidates, lane, evidence, domain=domain)
    allow_third = (
        int(task.get("independent_workstreams", 1) or 1) >= 3
        and float(task.get("parallelizable_fraction", 0.0) or 0.0) >= 0.65
        and not bool(task.get("shared_mutable_state") or task.get("strictly_sequential") or task.get("single_writer_only"))
    )
    rich_route = _needs_rich_jev_route(task)
    route_fn = decide_fast if rich_route else decide_lean
    jev = route_fn(
        task_summary=summary,
        candidate_models=candidates,
        remaining_free_quota=remaining,
        candidate_profiles=profiles,
        lane=lane,
        allow_third=allow_third,
        shared_mutable_state=bool(task.get("shared_mutable_state") or task.get("strictly_sequential") or task.get("single_writer_only")),
        high_risk=_is_high_risk(task),
        api_key=api_key,
    )
    expected_status = "JEV_FAST_DECISION_OK" if rich_route else "JEV_LEAN_DECISION_OK"
    if jev.get("status") != expected_status:
        return {
            "schema_version": "jev-routing-coordinator-v4",
            "status": "READY",
            "route_source": "DETERMINISTIC_FALLBACK_AFTER_JEV_UNAVAILABLE",
            "baseline": baseline,
            "jev": jev,
            "final_plan": baseline,
        }

    decision = jev.get("decision") or {}
    if decision.get("action") != "EXECUTE" or decision.get("low_confidence") is True:
        hedge = _bounded_low_confidence_hedge(task, baseline, candidates, decision)
        if hedge is not None:
            return {
                "schema_version": "jev-routing-coordinator-v4",
                "status": "READY",
                "route_source": "JEV_LOW_CONFIDENCE_BOUNDED_HEDGE",
                "baseline": baseline,
                "jev": jev,
                "final_plan": hedge,
            }
        return {
            "schema_version": "jev-routing-coordinator-v4",
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
            "schema_version": "jev-routing-coordinator-v4",
            "status": "READY",
            "route_source": "DETERMINISTIC_FALLBACK_AFTER_JEV_CONTRACT_FAILURE",
            "baseline": baseline,
            "jev": jev,
            "final_plan": baseline,
        }
    latency_challenger = None
    if len(selected) == 1 and not _is_high_risk(task) and remaining >= 2:
        latency_challenger = _healthy_latency_challenger(selected, candidates, evidence, domain=domain)
        if latency_challenger:
            selected.append(latency_challenger)
    final = {
        **baseline,
        "selected_models": selected,
        "primary_model": selected[0],
        "active_model_count": len(selected),
        "parallel_model_calls": len(selected) if (decision.get("parallel") or latency_challenger) else 1,
        "execution_mode": "PARALLEL" if latency_challenger else decision.get("execution_mode"),
        "lane": decision.get("lane") or lane,
        "independent_verification": bool(decision.get("independent_verification")),
        "fanout_reason": [
            ("JEV_FAST_RICH_TYPED_DECISION" if rich_route else "JEV_LEAN_TWO_QUESTION_DECISION"),
            *(["RECENT_PRIMARY_SLOW_LATENCY_CHALLENGER_ADDED"] if latency_challenger else []),
            *list(baseline.get("fanout_reason") or []),
        ],
        "jev_confidence": decision.get("confidence"),
        "fanout_decision_owner": "chatgpt-top-commander-with-jev-fast-decision-plane",
        "latency_challenger_timeout_seconds": LATENCY_CHALLENGER_TIMEOUT_SECONDS if latency_challenger else None,
    }
    return {
        "schema_version": "jev-routing-coordinator-v4",
        "status": "READY",
        "route_source": "JEV_FAST_DECISION_PLANE" if rich_route else "JEV_LEAN_DECISION_PLANE",
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
    lean_records: list[dict[str, Any]] = []
    fast_records: list[dict[str, Any]] = []
    task_candidates: dict[str, list[str]] = {}
    free_policy = load_free_policy()
    evidence = load_recent_evidence()

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
        candidates = _health_ranked_candidates(free_policy, entries, task, evidence, max_candidates=4)
        task_candidates[task_id] = candidates
        if not candidates:
            continue
        lane = str(base.get("lane") or "GENERAL_REASONING")
        remaining = int(base.get("remaining_quota_before_plan", 0))
        domain = str(task.get("domain") or task.get("task_class") or lane).strip() or None
        record = {
            "id": task_id,
            "task_summary": str(task.get("objective") or task.get("task_summary") or task.get("description") or task),
            "candidate_models": candidates,
            "candidate_profiles": _candidate_profiles(entries, candidates, lane, evidence, domain=domain),
            "quota_pressure": quota_pressure_from_remaining(remaining).value,
            "lane": lane,
            "allow_third": (
                int(task.get("independent_workstreams", 1) or 1) >= 3
                and float(task.get("parallelizable_fraction", 0.0) or 0.0) >= 0.65
                and not bool(task.get("shared_mutable_state") or task.get("strictly_sequential") or task.get("single_writer_only"))
            ),
            "shared_mutable_state": bool(task.get("shared_mutable_state") or task.get("strictly_sequential") or task.get("single_writer_only")),
            "high_risk": _is_high_risk(task),
        }
        (fast_records if _needs_rich_jev_route(task) else lean_records).append(record)

    results: dict[str, Mapping[str, Any]] = {}
    jobs = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        if lean_records:
            jobs.append(("lean", pool.submit(decide_many_lean, records=lean_records, api_key=api_key)))
        if fast_records:
            jobs.append(("fast", pool.submit(decide_many_fast, records=fast_records, api_key=api_key)))
        for name, future in jobs:
            try:
                result = future.result()
            except Exception as exc:
                result = {"status": "JEV_UNAVAILABLE", "reason": type(exc).__name__, "decisions": {}}
            results[name] = result

    lean_result = results.get("lean") or {"status": "JEV_LEAN_MANY_OK", "record_count": 0, "batch_count": 0, "decisions": {}}
    fast_result = results.get("fast") or {"status": "JEV_LEAN_MANY_OK", "record_count": 0, "batch_count": 0, "decisions": {}}
    decisions: dict[str, Any] = {}
    for result in (lean_result, fast_result):
        if isinstance(result.get("decisions"), Mapping):
            decisions.update(result.get("decisions") or {})
    jev = {
        "status": "JEV_MIXED_MANY_OK",
        "lean": lean_result,
        "fast": fast_result,
        "record_count": len(lean_records) + len(fast_records),
        "batch_count": int(lean_result.get("batch_count", 0) or 0) + int(fast_result.get("batch_count", 0) or 0),
        "parallel_route_surfaces": bool(lean_records and fast_records),
        "decisions": decisions,
    }
    plans: dict[str, Any] = {}

    raw_plans: dict[str, Any] = {}
    for index, task in enumerate(tasks, 1):
        task_id = str(task.get("task_id") or task.get("id") or f"task_{index:04d}")
        base = baselines[task_id]
        decision = decisions.get(task_id) if isinstance(decisions, Mapping) else None
        candidates = task_candidates.get(task_id, [])
        if not isinstance(decision, Mapping):
            raw_plans[task_id] = dict(base)
            continue
        if decision.get("action") != "EXECUTE" or decision.get("low_confidence"):
            hedge = _bounded_low_confidence_hedge(task, base, candidates, decision)
            raw_plans[task_id] = hedge if hedge is not None else dict(base)
            continue
        selected = [str(model) for model in list(decision.get("workers") or []) if str(model) in candidates]
        if not selected:
            raw_plans[task_id] = dict(base)
            continue
        latency_challenger = None
        if len(selected) == 1 and not _is_high_risk(task):
            domain = str(task.get("domain") or task.get("task_class") or base.get("lane") or "").strip() or None
            latency_challenger = _healthy_latency_challenger(selected, candidates, evidence, domain=domain)
            if latency_challenger:
                selected.append(latency_challenger)
        parallel = (bool(decision.get("parallel")) or latency_challenger is not None) and len(selected) > 1
        raw_plans[task_id] = {
            **base,
            "selected_models": selected,
            "primary_model": selected[0],
            "active_model_count": len(selected),
            "parallel_model_calls": len(selected) if parallel else 1,
            "execution_mode": "PARALLEL" if parallel else ("SINGLE" if len(selected) == 1 else "SEQUENTIAL"),
            "lane": decision.get("lane") or base.get("lane"),
            "independent_verification": bool(decision.get("independent_verification")),
            "jev_confidence": decision.get("confidence"),
            "latency_challenger_timeout_seconds": LATENCY_CHALLENGER_TIMEOUT_SECONDS if latency_challenger else None,
            "fanout_reason": [
                ("JEV_FAST_RICH_BATCH_DECISION" if _needs_rich_jev_route(task) else "JEV_LEAN_TWO_QUESTION_BATCH_DECISION"),
                *(["RECENT_PRIMARY_SLOW_LATENCY_CHALLENGER_ADDED"] if latency_challenger else []),
                *list(base.get("fanout_reason") or []),
            ],
        }

    # One common reservation gate covers Jev plans and deterministic fallbacks.
    remaining_budget = 0
    for base in baselines.values():
        if base.get("status") == "READY":
            remaining_budget = max(remaining_budget, int(base.get("remaining_quota_before_plan", 0) or 0))

    for index, task in enumerate(tasks, 1):
        task_id = str(task.get("task_id") or task.get("id") or f"task_{index:04d}")
        plan = dict(raw_plans[task_id])
        selected = list(plan.get("selected_models") or [])
        if plan.get("status") == "READY" and selected:
            if remaining_budget <= 0:
                plan.update(
                    status="BLOCKED_FREE_QUOTA_PLANNED_EXHAUSTED",
                    selected_models=[],
                    active_model_count=0,
                    parallel_model_calls=0,
                )
            else:
                if len(selected) > remaining_budget:
                    selected = selected[:remaining_budget]
                remaining_budget -= len(selected)
                parallel = plan.get("execution_mode") == "PARALLEL" and len(selected) > 1
                plan.update(
                    selected_models=selected,
                    primary_model=selected[0],
                    active_model_count=len(selected),
                    parallel_model_calls=len(selected) if parallel else 1,
                    execution_mode="PARALLEL" if parallel else ("SINGLE" if len(selected) == 1 else "SEQUENTIAL"),
                    planned_free_requests_reserved=len(selected),
                    remaining_batch_free_request_budget=remaining_budget,
                )
        plans[task_id] = plan

    return {
        "schema_version": "jev-routing-coordinator-batch-v3",
        "status": "READY",
        "task_count": len(tasks),
        "jev": jev,
        "plans": plans,
    }


__all__ = ["coordinate", "coordinate_many"]
