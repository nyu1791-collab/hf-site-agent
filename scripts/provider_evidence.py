#!/usr/bin/env python3
"""Normalize current provider evidence and evaluate the zero-cost gate.

Evidence is data, not activation.  Historical records are retained for audit
but cannot satisfy a current READY/STAGING decision.  Trial credits are kept
distinct from a genuinely free route and are blocked by the project's
FREE_ONLY policy unless a future policy explicitly permits them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


ZERO_PRICES = frozenset({"0", "0.0", "0.00"})
FREE_EVIDENCE_TYPES = frozenset({
    "OFFICIAL_FREE_TIER",
    "ACCOUNT_FREE_PLAN",
    "FREE_ENDPOINT",
    "PRICE_ZERO",
    "ZERO_COST_MODEL_ROUTE",
    "TRIAL_CREDIT_WITH_HARD_CAP",
})
FREE_ONLY_ALLOWED_EVIDENCE = frozenset({
    "OFFICIAL_FREE_TIER",
    "ACCOUNT_FREE_PLAN",
    "FREE_ENDPOINT",
    "PRICE_ZERO",
    "ZERO_COST_MODEL_ROUTE",
})
RISK_VALUES = frozenset({"NONE", "POSSIBLE", "KNOWN", "UNKNOWN"})


class ProviderEvidenceError(ValueError):
    """Raised when evidence is malformed or unsafe to evaluate."""


def _text(value: Any, limit: int = 240) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > limit or any(ord(char) < 32 for char in value):
        return ""
    return value


def _price(value: Any) -> str | None:
    if value is None:
        return None
    text = _text(str(value), 40)
    return text or None


def normalize_evidence(
    raw: Mapping[str, Any],
    *,
    provider_id: str,
    model_id: str,
    evidence_generation: int | None = None,
) -> dict[str, Any]:
    """Return a bounded, secret-free evidence bundle."""
    if not isinstance(raw, Mapping):
        raise ProviderEvidenceError("EVIDENCE_NOT_OBJECT")
    evidence_type = _text(raw.get("free_evidence_type") or raw.get("selected_route") or raw.get("free_access_type"), 64).upper() or "UNKNOWN"
    route_aliases = {
        "FREE_TIER": "OFFICIAL_FREE_TIER",
        "FREE_PLAN": "ACCOUNT_FREE_PLAN",
        "FREE_ENDPOINT": "FREE_ENDPOINT",
        "FREE_MODEL_ENDPOINT": "FREE_ENDPOINT",
    }
    evidence_type = route_aliases.get(evidence_type, evidence_type)
    if evidence_type not in FREE_EVIDENCE_TYPES and evidence_type != "UNKNOWN":
        raise ProviderEvidenceError("FREE_EVIDENCE_TYPE_INVALID")
    risk = _text(raw.get("billing_transition_risk"), 32).upper() or "UNKNOWN"
    if risk not in RISK_VALUES:
        raise ProviderEvidenceError("BILLING_TRANSITION_RISK_INVALID")
    timestamp = _text(raw.get("evidence_timestamp") or raw.get("verified_at"), 80)
    source = _text(raw.get("free_evidence_source") or raw.get("evidence_source"), 400)
    bundle = {
        "provider_id": _text(provider_id, 80),
        "model_id": _text(model_id, 200),
        "model_family": _text(raw.get("model_family"), 80),
        "evidence_generation": evidence_generation if isinstance(evidence_generation, int) and evidence_generation >= 1 else 1,
        "evidence_timestamp": timestamp,
        "evidence_source": source,
        "current": raw.get("current", raw.get("is_current")) is True,
        "exact_model_verified": raw.get("exact_model_verified", raw.get("model_verified")) is True,
        "catalog_verified": raw.get("catalog_verified") is True,
        "endpoint_verified": raw.get("endpoint_verified") is True,
        "auth_verified": raw.get("auth_verified", raw.get("auth_ok")) is True,
        "capability_verified": raw.get("capability_verified", raw.get("capability_pass")) is True,
        "free_evidence_type": evidence_type,
        "pricing_input": _price(raw.get("pricing_input", raw.get("input_price"))),
        "pricing_output": _price(raw.get("pricing_output", raw.get("output_price"))),
        "account_tier_class_if_available": _text(raw.get("account_tier_class_if_available") or raw.get("current_account_tier"), 80) or None,
        "current_account_eligible": raw.get("current_account_eligible") if isinstance(raw.get("current_account_eligible"), bool) else None,
        "quota": raw.get("quota") if isinstance(raw.get("quota"), Mapping) else (raw.get("quota_limits") if isinstance(raw.get("quota_limits"), Mapping) else {}),
        "quota_safe": raw.get("quota_safe", raw.get("quota_verified")) is True,
        "billing_transition_risk": risk,
        "automatic_paid_transition_possible": raw.get("automatic_paid_transition_possible") if isinstance(raw.get("automatic_paid_transition_possible"), bool) else None,
        "fallback_to_paid_possible": raw.get("fallback_to_paid_possible") if isinstance(raw.get("fallback_to_paid_possible"), bool) else None,
        "estimated_cost": _price(raw.get("estimated_cost", "0" if raw.get("zero_cost_verified") is True else None)),
        "free_verified": raw.get("free_verified", raw.get("zero_cost_verified")) is True,
        "cost_safe": raw.get("cost_safe", raw.get("zero_cost_verified")) is True,
        "production_active": raw.get("production_active") is True,
        "paid_fallback": raw.get("paid_fallback") is True,
        "auto_top_up": raw.get("auto_top_up") is True,
        "max_retries": raw.get("max_retries", 0),
    }
    if not bundle["provider_id"] or not bundle["model_id"]:
        raise ProviderEvidenceError("EVIDENCE_ID_REQUIRED")
    if bundle["max_retries"] != 0:
        raise ProviderEvidenceError("EVIDENCE_RETRY_MUST_BE_ZERO")
    if bundle["paid_fallback"] or bundle["auto_top_up"]:
        raise ProviderEvidenceError("EVIDENCE_SAFETY_POLICY_INVALID")
    return bundle


def evaluate_zero_cost(evidence: Mapping[str, Any], *, free_only: bool = True) -> dict[str, Any]:
    """Evaluate zero-cost eligibility without making a request."""
    blockers: list[str] = []
    if evidence.get("current") is not True:
        blockers.append("CURRENT_EVIDENCE_REQUIRED")
    if evidence.get("catalog_verified") is not True:
        blockers.append("CATALOG_NOT_VERIFIED")
    if evidence.get("exact_model_verified") is not True:
        blockers.append("EXACT_MODEL_NOT_VERIFIED")
    if not evidence.get("evidence_timestamp"):
        blockers.append("EVIDENCE_TIMESTAMP_REQUIRED")
    if not evidence.get("evidence_source"):
        blockers.append("EVIDENCE_SOURCE_REQUIRED")
    evidence_type = str(evidence.get("free_evidence_type") or "UNKNOWN")
    if evidence_type not in FREE_EVIDENCE_TYPES or evidence_type == "UNKNOWN":
        blockers.append("FREE_EVIDENCE_REQUIRED")
    if free_only and evidence_type not in FREE_ONLY_ALLOWED_EVIDENCE:
        blockers.append("TRIAL_CREDIT_NOT_FREE_ONLY_SAFE")
    if evidence.get("pricing_input") not in ZERO_PRICES:
        blockers.append("INPUT_PRICE_NOT_ZERO")
    if evidence.get("pricing_output") not in ZERO_PRICES:
        blockers.append("OUTPUT_PRICE_NOT_ZERO")
    if evidence.get("estimated_cost") not in ZERO_PRICES:
        blockers.append("ESTIMATED_COST_NOT_ZERO")
    if evidence.get("billing_transition_risk") != "NONE":
        blockers.append("BILLING_TRANSITION_RISK_NOT_NONE")
    if evidence.get("quota_safe") is not True:
        blockers.append("QUOTA_NOT_SAFE")
    if evidence.get("current_account_eligible") is not True:
        blockers.append(
            "CURRENT_ACCOUNT_ELIGIBILITY_UNKNOWN"
            if evidence.get("current_account_eligible") is None
            else "CURRENT_ACCOUNT_NOT_ELIGIBLE"
        )
    if evidence.get("automatic_paid_transition_possible") is not None and evidence.get("automatic_paid_transition_possible") is not False:
        blockers.append("AUTOMATIC_PAID_TRANSITION_NOT_DISABLED")
    if evidence.get("fallback_to_paid_possible") is not None and evidence.get("fallback_to_paid_possible") is not False:
        blockers.append("PAID_ROUTE_FALLBACK_POSSIBLE")
    if evidence.get("production_active") is True:
        blockers.append("PRODUCTION_ACTIVE_FORBIDDEN")
    if evidence.get("paid_fallback") is not False:
        blockers.append("PAID_FALLBACK_FORBIDDEN")
    if evidence.get("auto_top_up") is not False:
        blockers.append("AUTO_TOP_UP_FORBIDDEN")
    verified = not blockers
    return {
        "provider_id": evidence.get("provider_id"),
        "model_id": evidence.get("model_id"),
        "free_evidence_type": evidence_type,
        "zero_cost_verified": verified,
        "free_verified": verified,
        "cost_safe": verified,
        "blockers": sorted(set(blockers)),
        "production_active": False,
        "paid_fallback": False,
        "auto_top_up": False,
    }


def staging_evidence(
    raw: Mapping[str, Any],
    *,
    provider_id: str,
    model_id: str,
    model_family: str,
    evidence_generation: int = 1,
) -> dict[str, Any]:
    """Normalize evidence and attach a pure readiness result for staging."""
    bundle = normalize_evidence(
        {**dict(raw), "model_family": model_family},
        provider_id=provider_id,
        model_id=model_id,
        evidence_generation=evidence_generation,
    )
    zero_cost = evaluate_zero_cost(bundle)
    capable = all(bundle.get(key) is True for key in ("endpoint_verified", "auth_verified", "capability_verified"))
    ready = zero_cost["zero_cost_verified"] and capable and bundle.get("current") is True
    return {
        **bundle,
        **zero_cost,
        "technically_ready": ready,
        "staging_active": False,
        "staging_approved": False,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


__all__ = [
    "FREE_EVIDENCE_TYPES",
    "FREE_ONLY_ALLOWED_EVIDENCE",
    "ProviderEvidenceError",
    "evaluate_zero_cost",
    "normalize_evidence",
    "staging_evidence",
]
