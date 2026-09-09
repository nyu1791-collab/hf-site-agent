#!/usr/bin/env python3
"""Pure, fail-closed readiness evaluation for model/provider evidence.

This module never changes a registry and never turns a provider on.  It
distinguishes ``READY`` (all evidence exists for a bounded staging candidate)
from ``ACTIVE`` (always false here and controlled elsewhere by explicit
approval and production policy).
"""

from __future__ import annotations

from math import isfinite
from typing import Any, Mapping, Sequence


PRIMARY_LIFECYCLES = frozenset({"STABLE", "GA"})
PROVIDERS = frozenset({"google", "nvidia", "groq", "openrouter"})
REQUIRED_MODEL_EVIDENCE = (
    "model_verified", "free_verified", "auth_ok", "probe_pass", "capability_pass",
    "cost_safe", "quota_safe", "circuit_ok", "secret_guard_ok",
)


def evaluate_model_readiness(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate redacted model evidence without promoting or routing it."""
    blockers: list[str] = []
    provider = evidence.get("provider")
    model_id = evidence.get("model_id")
    lifecycle = str(evidence.get("lifecycle") or "UNKNOWN").upper()
    if provider not in PROVIDERS:
        blockers.append("UNKNOWN_PROVIDER")
    if not isinstance(model_id, str) or not model_id.strip():
        blockers.append("MODEL_ID_NOT_VERIFIED")
    if lifecycle not in PRIMARY_LIFECYCLES:
        blockers.append("LIFECYCLE_NOT_PROMOTABLE")
    for field in REQUIRED_MODEL_EVIDENCE:
        if evidence.get(field) is not True:
            blockers.append(f"{field.upper()}_REQUIRED")
    # New evidence bundles carry richer route/account facts.  These checks are
    # conditional for backward compatibility with the existing v1 fixtures;
    # when present, they are never inferred from a generic ``free`` boolean.
    if "current" in evidence and evidence.get("current") is not True:
        blockers.append("CURRENT_EVIDENCE_REQUIRED")
    if "zero_cost_verified" in evidence and evidence.get("zero_cost_verified") is not True:
        blockers.append("ZERO_COST_EVIDENCE_REQUIRED")
    if "current_account_eligible" in evidence and evidence.get("current_account_eligible") is not True:
        blockers.append("CURRENT_ACCOUNT_ELIGIBILITY_REQUIRED")
    if "selected_route" in evidence and not str(evidence.get("selected_route") or "").startswith("FREE_"):
        blockers.append("FREE_ROUTE_REQUIRED")
    cost = evidence.get("estimated_cost")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not isfinite(float(cost)) or float(cost) != 0:
        blockers.append("ZERO_COST_REQUIRED")
    if evidence.get("active") is True:
        blockers.append("ACTIVE_FLAG_MUST_REMAIN_FALSE_IN_PHASE6")
    return {
        "provider": provider,
        "model_id": model_id,
        "lifecycle": lifecycle,
        "ready": not blockers,
        "active": False,
        "blockers": sorted(set(blockers)),
        "production_routing_changed": False,
        "paid_execution_count": 0,
    }


def evaluate_provider_readiness(provider: str, models: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return true only when at least one same-provider model is READY."""
    if provider not in PROVIDERS:
        return {
            "provider": provider, "ready": False, "active": False,
            "models_considered": 0, "ready_models": [], "reason": "UNKNOWN_PROVIDER",
        }
    considered: list[dict[str, Any]] = []
    for evidence in models:
        if not isinstance(evidence, Mapping) or evidence.get("provider") != provider:
            continue
        result = evaluate_model_readiness(evidence)
        considered.append(result)
    ready_models = [item["model_id"] for item in considered if item["ready"]]
    return {
        "provider": provider,
        "ready": bool(ready_models),
        "active": False,
        "models_considered": len(considered),
        "ready_models": ready_models,
        "reason": "MODEL_EVIDENCE_COMPLETE" if ready_models else "NO_READY_MODEL",
        "production_routing_changed": False,
    }


__all__ = ["PRIMARY_LIFECYCLES", "REQUIRED_MODEL_EVIDENCE", "evaluate_model_readiness", "evaluate_provider_readiness"]
