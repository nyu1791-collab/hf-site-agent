#!/usr/bin/env python3
"""Resolve provider-specific free-route evidence without activating a model.

The resolver deliberately keeps four questions separate:

* does a public free programme or route exist;
* is the current account eligible for that route;
* which exact route was selected; and
* can this bounded request be proven to remain zero-cost.

Provider model pages, paid price tables, account plans, response headers, and
provider fallbacks are not interchangeable evidence.  This module is pure
and never reads secrets, performs network I/O, changes a registry, or selects
a paid sibling model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Mapping, Sequence


KNOWN_PROVIDERS = frozenset({"google", "groq", "nvidia", "openrouter"})
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")
ZERO_WORDS = frozenset({"free", "free of charge", "no charge", "zero", "0"})
FREE_PLANS = frozenset({"FREE", "FREE_PLAN", "FREE_TIER", "AI_STUDIO_FREE", "DEVELOPER_FREE"})


class FreeEvidenceError(ValueError):
    """Raised when resolver input cannot be safely interpreted."""


def _text(value: Any, limit: int = 240) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > limit or any(ord(char) < 32 for char in value):
        return ""
    return value


def _upper(value: Any, limit: int = 80) -> str:
    return _text(value, limit).upper()


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError):
        return None


def _price(value: Any) -> str | None:
    if value is None:
        return None
    text = _text(str(value), 48).lower()
    if not text:
        return None
    if text in ZERO_WORDS:
        return "0"
    parsed = _decimal(text)
    if parsed is not None and parsed == 0:
        return "0"
    return text


def _is_zero(value: Any) -> bool:
    text = _price(value)
    if text == "0":
        return True
    parsed = _decimal(text)
    return parsed is not None and parsed == 0


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _canonical_model_id(provider: str, value: Any) -> str:
    model_id = _text(value, 200)
    if provider == "google" and model_id.startswith("models/"):
        # Google catalog responses commonly prefix model resource names.  The
        # prefix is transport syntax; the model ID itself is preserved.
        model_id = model_id[7:]
    return model_id if MODEL_ID_RE.fullmatch(model_id) else ""


def _catalog_entries(catalog: Any) -> list[Mapping[str, Any]]:
    if isinstance(catalog, Mapping):
        for key in ("data", "models", "entries", "items"):
            value = catalog.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                return [item for item in value if isinstance(item, Mapping)]
        return [catalog]
    if isinstance(catalog, Sequence) and not isinstance(catalog, (str, bytes, bytearray)):
        return [item for item in catalog if isinstance(item, Mapping)]
    return []


def _catalog_id(provider: str, entry: Mapping[str, Any]) -> str:
    return _canonical_model_id(provider, _first(entry, "id", "model_id", "name"))


def _safe_catalog_digest(provider: str, entries: Sequence[Mapping[str, Any]]) -> str:
    projected = []
    for entry in entries[:512]:
        model_id = _catalog_id(provider, entry)
        if not model_id:
            continue
        pricing = _mapping(entry.get("pricing"))
        projected.append({
            "id": model_id,
            "prompt": _price(_first(pricing, "prompt", "input", "input_price")),
            "completion": _price(_first(pricing, "completion", "output", "output_price")),
            "available": entry.get("available") if isinstance(entry.get("available"), bool) else None,
        })
    encoded = json.dumps({"provider": provider, "models": projected}, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _find_catalog_entry(provider: str, model_id: str, entries: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any] | None, bool]:
    matches = [entry for entry in entries if _catalog_id(provider, entry) == model_id]
    if not matches:
        return None, False
    # Duplicate exact IDs are accepted only when their safe public metadata is
    # identical.  Conflicting entries are an evidence conflict, not a reason
    # to choose one arbitrarily.
    fingerprints = set()
    for entry in matches:
        pricing = _mapping(entry.get("pricing"))
        fingerprints.add((
            _price(_first(pricing, "prompt", "input", "input_price")),
            _price(_first(pricing, "completion", "output", "output_price")),
            entry.get("available") if isinstance(entry.get("available"), bool) else None,
        ))
    return matches[0], len(fingerprints) > 1


def _route_prices(
    provider: str,
    entry: Mapping[str, Any],
    pricing: Mapping[str, Any],
    route: str,
) -> tuple[str | None, str | None, bool]:
    route_key = {
        "FREE_TIER": "free_tier",
        "FREE_PLAN": "free_plan",
        "FREE_ENDPOINT": "free_endpoint",
        "FREE_MODEL_ENDPOINT": "free_endpoint",
    }.get(route, "")
    route_data = _mapping(pricing.get(route_key))
    input_price = _first(route_data, "input", "prompt", "input_price")
    output_price = _first(route_data, "output", "completion", "output_price")
    if input_price is None:
        input_price = _first(pricing, "input_price", "input", "prompt")
    if output_price is None:
        output_price = _first(pricing, "output_price", "output", "completion")
    if input_price is None or output_price is None:
        catalog_pricing = _mapping(entry.get("pricing"))
        if input_price is None:
            input_price = _first(catalog_pricing, "input", "prompt", "input_price")
        if output_price is None:
            output_price = _first(catalog_pricing, "output", "completion", "output_price")
    paid = _mapping(pricing.get("paid_tier"))
    paid_price_exists = any(
        value is not None and not _is_zero(value)
        for value in (
            _first(paid, "input", "prompt", "input_price"),
            _first(paid, "output", "completion", "output_price"),
            _first(pricing, "paid_input_price", "paid_output_price"),
        )
    )
    if provider in {"google", "groq", "nvidia"} and route in {"FREE_TIER", "FREE_PLAN", "FREE_ENDPOINT"}:
        explicit_free_prices = any(value is not None for value in (input_price, output_price))
        if not explicit_free_prices and pricing.get("free_price_verified") is True:
            input_price, output_price = "0", "0"
    return _price(input_price), _price(output_price), paid_price_exists


def _account_tier(provider: str, account: Mapping[str, Any], raw: Mapping[str, Any]) -> str:
    value = _first(
        account,
        "current_account_tier", "current_org_plan", "account_tier", "organization_plan", "plan", "tier",
    )
    if value is None:
        value = _first(raw, "current_account_tier", "current_org_plan", "account_tier", "current_plan")
    tier = _upper(value, 80)
    if tier in {"FREE PLAN", "FREE-PLAN", "FREE TIER", "FREE-TIER"}:
        return "FREE"
    return tier or "UNKNOWN"


def _free_plan_contains(model_id: str, value: Any) -> bool:
    if isinstance(value, Mapping):
        if model_id in value:
            return value[model_id] is not False
        values = value.get("models")
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)):
            return model_id in {str(item) for item in values}
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return model_id in {str(item) for item in value}
    return False


def _quota_limits(quota: Mapping[str, Any], pricing: Mapping[str, Any]) -> dict[str, Any]:
    source = _mapping(_first(quota, "quota_limits", "limits", "free_plan_limits"))
    if not source:
        source = _mapping(_first(pricing, "quota_limits", "limits", "free_plan_limits"))
    result: dict[str, Any] = {}
    for key, value in list(source.items())[:32]:
        name = _text(key, 80)
        if not name:
            continue
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            result[name] = _text(str(value), 80) if isinstance(value, str) else value
    return result


def _current_account_eligible(provider: str, account: Mapping[str, Any], raw: Mapping[str, Any], tier: str) -> bool | None:
    explicit = _first(account, "current_account_eligible", "free_endpoint_eligible", "free_plan_eligible", "free_tier_eligible")
    if explicit is None:
        explicit = _first(raw, "current_account_eligible", "free_endpoint_eligible", "free_plan_eligible", "free_tier_eligible")
    if isinstance(explicit, bool):
        return explicit
    if provider in {"google", "groq"}:
        return True if tier in FREE_PLANS else (False if tier != "UNKNOWN" else None)
    if provider == "nvidia":
        return True if tier in FREE_PLANS else None
    return None


def _free_program(provider: str, model_id: str, entry: Mapping[str, Any], pricing: Mapping[str, Any], raw: Mapping[str, Any]) -> tuple[bool | None, str]:
    explicit = _first(raw, "free_program_exists", "free_tier_available", "free_plan_supported", "free_route_available")
    if isinstance(explicit, bool):
        return explicit, _upper(_first(raw, "free_access_type", "selected_route"), 64)
    if provider == "google":
        return isinstance(pricing.get("free_tier"), Mapping) or pricing.get("free_tier_available") is True, "FREE_TIER"
    if provider == "groq":
        supported = _first(pricing, "free_plan_supported", "free_plan_limits")
        return (supported is True or _free_plan_contains(model_id, supported)), "FREE_PLAN"
    if provider == "nvidia":
        routes = _first(pricing, "available_access_routes", "access_routes")
        available = pricing.get("free_endpoint_available") is True
        if isinstance(routes, Sequence) and not isinstance(routes, (str, bytes, bytearray)):
            available = available or any(_upper(item, 80) in {"FREE_ENDPOINT", "FREE API", "FREE_API"} for item in routes)
        if isinstance(routes, Mapping):
            available = available or routes.get("FREE_ENDPOINT") is True
        return available, "FREE_ENDPOINT"
    if provider == "openrouter":
        return model_id.endswith(":free") and bool(entry), "FREE_MODEL_ENDPOINT"
    return None, "UNKNOWN"


def resolve_free_evidence(
    provider: str,
    model: str,
    catalog: Any,
    account_metadata: Mapping[str, Any] | None,
    pricing_metadata: Mapping[str, Any] | None,
    quota_metadata: Mapping[str, Any] | None,
    *,
    evidence_source: str = "",
    evidence_timestamp: str | None = None,
    current: bool = True,
    free_only: bool = True,
) -> dict[str, Any]:
    """Resolve current zero-cost evidence for one exact model and route.

    The return value is safe to persist: it contains only redacted booleans,
    bounded tier/limit metadata, prices, hashes, and blocker codes.
    """
    provider_id = _text(provider, 64).lower()
    model_id = _canonical_model_id(provider_id, model)
    if provider_id not in KNOWN_PROVIDERS:
        raise FreeEvidenceError("UNKNOWN_PROVIDER")
    if not model_id:
        raise FreeEvidenceError("INVALID_MODEL_ID")

    raw_account = _mapping(account_metadata)
    raw_pricing = _mapping(pricing_metadata)
    raw_quota = _mapping(quota_metadata)
    entries = _catalog_entries(catalog)
    entry, duplicate_conflict = _find_catalog_entry(provider_id, model_id, entries)
    catalog_id_present = entry is not None
    raw_catalog_verified = _bool_or_none(raw_pricing.get("catalog_verified"))
    catalog_verified = raw_catalog_verified if raw_catalog_verified is not None else catalog_id_present
    exact_model_verified = raw_pricing.get("exact_model_verified") is True or catalog_id_present
    if raw_pricing.get("model_verified") is True:
        exact_model_verified = True

    public_page_exists = _bool_or_none(_first(raw_pricing, "public_page_exists", "official_page_exists"))
    api_catalog_exists = _bool_or_none(_first(raw_pricing, "api_catalog_exists", "current_api_catalog_exists"))
    conflicts: list[str] = []
    if duplicate_conflict:
        conflicts.append("DUPLICATE_CATALOG_METADATA_CONFLICT")
    if public_page_exists is True and api_catalog_exists is False:
        conflicts.append("CATALOG_INCONSISTENCY")
    if raw_pricing.get("catalog_inconsistency") is True:
        conflicts.append("CATALOG_INCONSISTENCY")

    route_hint = _upper(_first(raw_pricing, "selected_route", "free_access_type"), 64)
    if not route_hint:
        route_hint = {"google": "FREE_TIER", "groq": "FREE_PLAN", "nvidia": "FREE_ENDPOINT", "openrouter": "FREE_MODEL_ENDPOINT"}[provider_id]
    free_program_exists, provider_route = _free_program(provider_id, model_id, entry or {}, raw_pricing, raw_pricing)
    selected_route = route_hint or provider_route
    if selected_route not in {"FREE_TIER", "FREE_PLAN", "FREE_ENDPOINT", "FREE_MODEL_ENDPOINT"}:
        conflicts.append("FREE_ROUTE_NOT_RECOGNIZED")
    if provider_id == "openrouter" and not model_id.endswith(":free"):
        conflicts.append("OPENROUTER_FREE_SUFFIX_REQUIRED")
    if provider_id == "openrouter" and entry is not None and not model_id.endswith(":free"):
        conflicts.append("PAID_SIBLING_NOT_ELIGIBLE")

    current_tier = _account_tier(provider_id, raw_account, raw_pricing)
    current_account_eligible = _current_account_eligible(provider_id, raw_account, raw_pricing, current_tier)
    input_price, output_price, paid_price_exists = _route_prices(provider_id, entry or {}, raw_pricing, selected_route)
    if provider_id == "groq" and _free_plan_contains(model_id, raw_pricing.get("free_plan_limits")):
        # Groq's model page can expose paid per-token prices while the current
        # Free Plan is request/token limited.  Those are separate routes.
        input_price, output_price = "0", "0"
    if provider_id in {"google", "nvidia"} and raw_pricing.get("free_price_verified") is True:
        input_price, output_price = "0", "0"

    quota_limits = _quota_limits(raw_quota, raw_pricing)
    quota_source = _text(_first(raw_quota, "quota_source", "source") or _first(raw_pricing, "quota_source"), 240)
    quota_verified = _first(raw_quota, "quota_verified", "verified") is True or raw_pricing.get("quota_verified") is True
    quota_safe = _first(raw_quota, "quota_safe", "safe") is True or raw_pricing.get("quota_safe") is True
    if raw_quota.get("rate_headers_verified") is True:
        quota_verified = True
    if quota_verified and raw_quota.get("remaining_safe") is not False:
        quota_safe = True

    billing_enabled = _bool_or_none(_first(raw_account, "billing_enabled", "billing_active"))
    if billing_enabled is None:
        billing_enabled = _bool_or_none(_first(raw_pricing, "billing_enabled"))
    auto_paid = _bool_or_none(_first(raw_account, "automatic_paid_transition_possible", "auto_paid_transition"))
    if auto_paid is None:
        auto_paid = _bool_or_none(_first(raw_pricing, "automatic_paid_transition_possible", "auto_paid_transition"))
    fallback = _bool_or_none(_first(raw_account, "fallback_to_paid_possible", "paid_fallback_possible"))
    if fallback is None:
        fallback = _bool_or_none(_first(raw_pricing, "fallback_to_paid_possible", "paid_fallback_possible"))
    if raw_pricing.get("provider_allow_fallbacks") is False or raw_pricing.get("paid_fallback_disabled") is True:
        fallback = False
    paid_transition_disabled = auto_paid is False or raw_pricing.get("paid_route_unreachable") is True
    paid_fallback_disabled = fallback is False or raw_pricing.get("paid_fallback_disabled") is True
    billing_risk = _upper(_first(raw_pricing, "billing_transition_risk") or raw_account.get("billing_transition_risk"), 32)
    if not billing_risk:
        if auto_paid is True or fallback is True:
            billing_risk = "KNOWN"
        elif auto_paid is False and fallback is False:
            billing_risk = "NONE"
        else:
            billing_risk = "UNKNOWN"

    blockers: list[str] = []
    if current is not True:
        blockers.append("CURRENT_EVIDENCE_REQUIRED")
    if conflicts:
        blockers.extend(conflicts)
    if catalog_verified is not True:
        blockers.append("CATALOG_NOT_VERIFIED")
    if exact_model_verified is not True:
        blockers.append("EXACT_MODEL_NOT_VERIFIED")
    if free_program_exists is not True:
        blockers.append("FREE_PROGRAM_NOT_VERIFIED")
    if selected_route not in {"FREE_TIER", "FREE_PLAN", "FREE_ENDPOINT", "FREE_MODEL_ENDPOINT"}:
        blockers.append("FREE_ROUTE_NOT_SELECTED")
    if current_account_eligible is not True:
        blockers.append("CURRENT_ACCOUNT_ELIGIBILITY_UNKNOWN" if current_account_eligible is None else "CURRENT_ACCOUNT_NOT_ELIGIBLE")
    if input_price != "0":
        blockers.append("INPUT_PRICE_NOT_ZERO")
    if output_price != "0":
        blockers.append("OUTPUT_PRICE_NOT_ZERO")
    if quota_verified is not True:
        blockers.append("QUOTA_NOT_VERIFIED")
    if quota_safe is not True:
        blockers.append("QUOTA_NOT_SAFE")
    if paid_transition_disabled is not True:
        blockers.append("AUTOMATIC_PAID_TRANSITION_UNKNOWN")
    if paid_fallback_disabled is not True:
        blockers.append("PAID_FALLBACK_NOT_DISABLED")
    if billing_risk != "NONE":
        blockers.append("BILLING_TRANSITION_RISK_NOT_NONE")
    if free_only and selected_route in {"TRIAL_CREDIT", "TRIAL_CREDITS"}:
        blockers.append("TRIAL_CREDIT_NOT_FREE_ONLY_SAFE")
    if raw_pricing.get("available") is False or (entry is not None and entry.get("available") is False):
        blockers.append("MODEL_UNAVAILABLE")

    deduped_blockers = sorted(set(blockers))
    inconsistent = any(item in {"CATALOG_INCONSISTENCY", "DUPLICATE_CATALOG_METADATA_CONFLICT"} for item in deduped_blockers)
    zero_cost_verified = not deduped_blockers
    status = "INCONSISTENT" if inconsistent else ("VERIFIED" if zero_cost_verified else "BLOCKED")
    source = _text(evidence_source or _first(raw_pricing, "evidence_source", "source"), 400)
    observed_at = _text(evidence_timestamp or _first(raw_pricing, "evidence_timestamp", "verified_at"), 80)
    if not observed_at:
        observed_at = datetime.now(timezone.utc).isoformat()
    if not source:
        deduped_blockers.append("EVIDENCE_SOURCE_REQUIRED")
        zero_cost_verified = False
        status = "BLOCKED"
    confidence = "HIGH" if zero_cost_verified else ("MEDIUM" if current and catalog_verified and free_program_exists is True else "LOW")
    if not source:
        confidence = "LOW"

    return {
        "provider": provider_id,
        "model_id": model_id,
        "model_verified": exact_model_verified,
        "catalog_verified": catalog_verified,
        "catalog_id_present": catalog_id_present,
        "catalog_hash": _safe_catalog_digest(provider_id, entries),
        "public_page_exists": public_page_exists,
        "api_catalog_exists": api_catalog_exists,
        "catalog_inconsistency": bool(conflicts and "CATALOG_INCONSISTENCY" in conflicts),
        "free_program_exists": free_program_exists,
        "free_access_type": selected_route,
        "current_account_tier": current_tier,
        "current_account_eligible": current_account_eligible,
        "free_route_selected": selected_route in {"FREE_TIER", "FREE_PLAN", "FREE_ENDPOINT", "FREE_MODEL_ENDPOINT"},
        "selected_route": selected_route,
        "input_price": input_price,
        "output_price": output_price,
        "paid_price_exists": paid_price_exists,
        "quota_source": quota_source or "UNKNOWN",
        "quota_limits": quota_limits,
        "quota_verified": quota_verified,
        "quota_safe": quota_safe,
        "billing_enabled": billing_enabled,
        "automatic_paid_transition_possible": auto_paid,
        "fallback_to_paid_possible": fallback,
        "paid_transition_disabled": paid_transition_disabled,
        "paid_fallback_disabled": paid_fallback_disabled,
        "billing_transition_risk": billing_risk,
        "evidence_source": source,
        "evidence_timestamp": observed_at,
        "is_current": current is True,
        "confidence": confidence,
        "zero_price_verified": input_price == "0" and output_price == "0",
        "zero_cost_verified": zero_cost_verified,
        "blockers": deduped_blockers,
        "status": status,
        "revalidation_required": not zero_cost_verified,
        "paid_fallback": False,
        "auto_top_up": False,
        "model_family": _text(_first(raw_pricing, "model_family"), 80),
    }


__all__ = ["FreeEvidenceError", "resolve_free_evidence"]
