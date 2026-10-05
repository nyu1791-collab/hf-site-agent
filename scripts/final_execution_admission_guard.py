#!/usr/bin/env python3
"""Last deterministic safety gate before an AI Army execution plan is released.

Routing surfaces may differ (deterministic, Jev, fallback, hedge, challenger or
batch).  None may bypass this guard.  It does not call providers or mutate
state: it preserves approval stops, validates the already-eligible model set,
applies the final quota ceiling, and makes execution and verification roles
explicit.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def reservation_models(plan: Mapping[str, Any]) -> list[str]:
    """Return all Worker calls a plan may release, including delayed work.

    A delayed challenger is not dispatched with the primary, but its quota and
    eligibility must be reserved before the plan is released.  This prevents a
    later timeout path from silently exceeding the free-request budget.
    """
    reserved: list[str] = []
    for raw in [
        *list(plan.get("selected_models") or []),
        *list(plan.get("execution_reservation_models") or []),
    ]:
        model = str(raw)
        if model and model not in reserved:
            reserved.append(model)
    return reserved


def apply_final_execution_admission_guard(
    task: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    eligible_models: Sequence[str],
    remaining_quota: int | None = None,
) -> dict[str, Any]:
    """Return a fail-closed plan with deterministic execution invariants.

    ``eligible_models`` is the prevalidated pool.  Health and Jev may rank or
    choose within it, but this guard never allows either to expand it.
    """
    guarded = dict(plan)
    allowed = {str(model) for model in eligible_models if str(model)}
    selected: list[str] = []
    for raw in list(guarded.get("selected_models") or []):
        model = str(raw)
        if model and model not in selected:
            selected.append(model)

    if guarded.get("status") != "READY":
        guarded["final_execution_admission"] = {
            "status": "NOT_RELEASED",
            "reason": "PLAN_ALREADY_BLOCKED_OR_REQUIRES_ADJUDICATION",
        }
        return guarded

    reserved = reservation_models(guarded)
    if allowed and any(model not in allowed for model in reserved):
        guarded.update(
            status="BLOCKED_INELIGIBLE_MODEL_AFTER_ROUTING",
            selected_models=[],
            active_model_count=0,
            parallel_model_calls=0,
            execution_mode="BLOCKED",
            worker_roles=[],
            final_execution_admission={
                "status": "BLOCKED",
                "reason": "ROUTING_MAY_NOT_EXPAND_PREVALIDATED_ELIGIBILITY",
            },
        )
        return guarded

    if remaining_quota is not None and len(reserved) > max(0, int(remaining_quota)):
        guarded.update(
            status="BLOCKED_FREE_QUOTA_PLANNED_EXHAUSTED",
            selected_models=[],
            active_model_count=0,
            parallel_model_calls=0,
            execution_mode="BLOCKED",
            worker_roles=[],
            final_execution_admission={
                "status": "BLOCKED",
                "reason": "FINAL_SELECTED_WORKERS_EXCEED_RESERVED_FREE_QUOTA",
            },
        )
        return guarded

    shared_state = bool(
        task.get("shared_mutable_state")
        or task.get("strictly_sequential")
        or task.get("single_writer_only")
    )
    if shared_state and len(selected) > 1:
        guarded["execution_mode"] = "SEQUENTIAL"
        guarded["parallel_model_calls"] = 1
    elif len(selected) <= 1:
        guarded["execution_mode"] = "SINGLE"
        guarded["parallel_model_calls"] = 1 if selected else 0

    verification_requested = bool(guarded.get("independent_verification"))
    if verification_requested and len(selected) < 2:
        guarded.update(
            status="REQUIRES_CHATGPT_ADJUDICATION",
            selected_models=[],
            active_model_count=0,
            parallel_model_calls=0,
            execution_mode="BLOCKED",
            worker_roles=[],
            final_execution_admission={
                "status": "NOT_RELEASED",
                "reason": "INDEPENDENT_VERIFICATION_HAS_NO_INDEPENDENT_VERIFIER",
            },
        )
        return guarded

    roles = []
    for index, model in enumerate(selected):
        role = "PRIMARY_EXECUTOR" if index == 0 else "PARALLEL_SPECIALIST"
        if verification_requested and index == 1:
            role = "INDEPENDENT_VERIFIER"
        roles.append({"model": model, "role": role})

    guarded.update(
        selected_models=selected,
        active_model_count=len(selected),
        primary_model=selected[0] if selected else None,
        worker_roles=roles,
        final_execution_admission={
            "status": "PASS",
            "eligibility_checked": True,
            "quota_checked": remaining_quota is not None,
            "reserved_worker_calls": len(reserved),
            "shared_state_serialized": shared_state and len(selected) > 1,
            "verification_role_explicit": verification_requested,
        },
    )
    return guarded


__all__ = ["apply_final_execution_admission_guard", "reservation_models"]
