#!/usr/bin/env python3
"""Fail-closed preflight gate for an explicitly approved minimal probe.

The gate only evaluates redacted evidence supplied by a caller.  It never
reads secret values, performs network I/O, chooses a fallback model, or
starts a probe.  ``allowed`` means "eligible for a separately invoked probe",
not "probe was executed" and not "model is READY/ACTIVE".
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping


PROVIDERS = frozenset({"google", "nvidia", "groq", "openrouter"})
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")


def evaluate_probe_gate(
    *,
    provider: str,
    expected_model_id: str,
    selected_model_id: str | None,
    evidence: Mapping[str, Any],
    explicit_approval: bool = False,
) -> dict[str, Any]:
    """Return a redacted, non-executing decision for a minimal probe."""
    blockers: list[str] = []
    if provider not in PROVIDERS:
        blockers.append("UNKNOWN_PROVIDER")
    if not isinstance(expected_model_id, str) or not MODEL_ID_RE.fullmatch(expected_model_id):
        blockers.append("INVALID_EXPECTED_MODEL_ID")
    if selected_model_id != expected_model_id:
        blockers.append("EXACT_MODEL_ID_NOT_VERIFIED")
    if evidence.get("secret_present") is not True:
        blockers.append("SECRET_NOT_PRESENT")
    if evidence.get("model_verified") is not True:
        blockers.append("MODEL_NOT_VERIFIED")
    if evidence.get("free_verified") is not True:
        blockers.append("FREE_NOT_VERIFIED")
    if evidence.get("quota_verified") is not True:
        blockers.append("QUOTA_NOT_VERIFIED")
    if evidence.get("endpoint_verified") is not True:
        blockers.append("ENDPOINT_NOT_VERIFIED")
    if evidence.get("capabilities_verified") is not True:
        blockers.append("CAPABILITY_NOT_VERIFIED")
    if evidence.get("paid_fallback") is not False:
        blockers.append("PAID_FALLBACK_NOT_DISABLED")
    if evidence.get("auto_top_up") is not False:
        blockers.append("AUTO_TOP_UP_NOT_DISABLED")
    if evidence.get("max_retries") != 0:
        blockers.append("RETRY_MUST_BE_ZERO")
    cost = evidence.get("estimated_cost")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(float(cost)):
        blockers.append("ESTIMATED_COST_UNKNOWN")
    elif float(cost) != 0:
        blockers.append("ESTIMATED_COST_NONZERO")
    if explicit_approval is not True:
        blockers.append("EXPLICIT_PROBE_APPROVAL_REQUIRED")
    return {
        "provider": provider,
        "expected_model_id": expected_model_id,
        "selected_model_id": selected_model_id,
        "allowed": not blockers,
        "status": "READY_FOR_EXPLICIT_PROBE" if not blockers else "BLOCKED",
        "blockers": sorted(set(blockers)),
        "live_probe_executed": False,
        "live_probe_count": 0,
        "paid_execution_count": 0,
        "paid_fallback": False,
        "secret_values_emitted": False,
        "production_routing_changed": False,
    }


__all__ = ["PROVIDERS", "evaluate_probe_gate"]
