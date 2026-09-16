#!/usr/bin/env python3
"""Focused NVIDIA + Google staging admission.

Both commander routes use fresh secure, exact-model route evidence for admission
and defer inference liveness to their first real agent task.  This removes a
redundant tiny request immediately before Executor work, reducing free-tier rate
pressure and latency without weakening paid-route, model, endpoint, auth, or
freshness boundaries.

Unknown account tier, quota headers, usage-cost fields and response-model fields
are warnings. Explicit paid routing, non-zero route price, auth/model failure
and stale evidence remain hard stops.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.provider_adapters import NVIDIA_NEMOTRON_MODEL, create_provider_adapter
from scripts.provider_registry import load_provider_registry

GOOGLE_MODEL = "gemini-3.8-flash"
GOOGLE_BOUNDED_TIMEOUT_SECONDS = 120.0


def _read_json(path_value: str) -> Mapping[str, Any]:
    path = Path(path_value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("evidence file must stay inside the workspace")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("evidence file must contain an object")
    return value


def _model_record(evidence: Mapping[str, Any], provider: str, model: str) -> Mapping[str, Any]:
    providers = evidence.get("providers") if isinstance(evidence.get("providers"), Mapping) else {}
    provider_record = providers.get(provider) if isinstance(providers, Mapping) else {}
    models = provider_record.get("models") if isinstance(provider_record, Mapping) else {}
    record = models.get(model) if isinstance(models, Mapping) else {}
    return record if isinstance(record, Mapping) else {}


def _fresh(record: Mapping[str, Any]) -> bool:
    value = record.get("expires_at")
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed > datetime.now(timezone.utc)


def _google_bounded_probe_allowed(record: Mapping[str, Any]) -> bool:
    """Unknown account/quota metadata is soft; only explicit bad facts block."""
    account = record.get("account_metadata") if isinstance(record.get("account_metadata"), Mapping) else {}
    return (
        record.get("secure_evidence") is True
        and record.get("current") is True
        and _fresh(record)
        and record.get("model_verified") is True
        and record.get("endpoint_verified") is True
        and record.get("auth_verified") is True
        and record.get("free_program_available") is True
        and record.get("free_route_selected") is True
        and record.get("selected_route") == "FREE_TIER"
        and record.get("zero_price_verified") is True
        and record.get("paid_fallback_possible") is False
        and record.get("paid_transition_possible") is not True
        and record.get("billing_enabled_class") is not True
        and account.get("billing_enabled") is not True
        and account.get("current_account_eligible") is not False
        and account.get("fallback_to_paid_possible") is not True
        and account.get("automatic_paid_transition_possible") is not True
    )


def _nvidia_direct_agent_allowed(record: Mapping[str, Any]) -> bool:
    return (
        record.get("secure_evidence") is True
        and record.get("current") is True
        and _fresh(record)
        and record.get("model_verified") is True
        and record.get("endpoint_verified") is True
        and record.get("auth_verified") is True
        and record.get("selected_route") == "FREE_ENDPOINT"
        and record.get("zero_price_verified") is True
        and record.get("paid_fallback_possible") is False
        and record.get("paid_transition_possible") is False
    )


def _deferred_result(provider: str, model: str, allowed: bool, *, route_label: str) -> dict[str, Any]:
    warnings = [
        "QUOTA_METADATA_UNAVAILABLE",
        "PER_RESPONSE_COST_MAY_BE_UNREPORTED",
        "RESPONSE_MODEL_FIELD_MAY_BE_UNREPORTED",
        "SEPARATE_LIVENESS_PROBE_SKIPPED",
    ]
    if provider == "google":
        warnings.insert(0, "ACCOUNT_BILLING_METADATA_UNAVAILABLE")
    else:
        warnings.insert(0, "ACCOUNT_ENTITLEMENT_UNKNOWN")
    return {
        "provider": provider,
        "model": model,
        "model_configured": True,
        "status": "PROBE_DEFERRED_TO_AGENT" if allowed else "FREE_ROUTE_PREFLIGHT_BLOCKED",
        "model_calls": 0,
        "retry_count": 0,
        "paid_fallback": False,
        "network_enabled": True,
        "probe_mode": "DIRECT_AGENT_LIVENESS",
        "request_hard_limit": 0,
        "automatic_model_fallback": False,
        "generic_paid_router_disabled": True,
        "auto_top_up": False,
        "staging_only": True,
        "direct_agent_admission": allowed,
        "selected_route": route_label,
        "soft_warnings": warnings if allowed else [],
    }


def _nvidia_deferred_result(evidence: Mapping[str, Any]) -> dict[str, Any]:
    record = _model_record(evidence, "nvidia", NVIDIA_NEMOTRON_MODEL)
    return _deferred_result(
        "nvidia", NVIDIA_NEMOTRON_MODEL, _nvidia_direct_agent_allowed(record), route_label="FREE_ENDPOINT"
    )


def _google_deferred_result(evidence: Mapping[str, Any]) -> dict[str, Any]:
    record = _model_record(evidence, "google", GOOGLE_MODEL)
    return _deferred_result(
        "google", GOOGLE_MODEL, _google_bounded_probe_allowed(record), route_label="FREE_TIER"
    )


def _google_bounded_probe(registry: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Legacy explicit tiny probe retained for diagnostics/tests, not default carrier admission."""
    record = _model_record(evidence, "google", GOOGLE_MODEL)
    if not _google_bounded_probe_allowed(record):
        return {
            "provider": "google", "model": GOOGLE_MODEL, "model_configured": True,
            "status": "ZERO_COST_PREFLIGHT_BLOCKED", "model_calls": 0,
            "retry_count": 0, "paid_fallback": False, "network_enabled": True,
            "bounded_free_tier_probe_allowed": False,
        }
    adapter = create_provider_adapter(registry, "google", network_enabled=True, timeout_seconds=GOOGLE_BOUNDED_TIMEOUT_SECONDS)
    adapter.timeout_seconds = max(float(getattr(adapter, "timeout_seconds", 0) or 0), GOOGLE_BOUNDED_TIMEOUT_SECONDS)
    try:
        raw = adapter.probe(GOOGLE_MODEL)
    except Exception:
        return {
            "provider": "google", "model": GOOGLE_MODEL, "model_configured": True,
            "status": "NETWORK_TIMEOUT_OR_PROVIDER_FAILURE", "model_calls": 1,
            "retry_count": 0, "paid_fallback": False, "network_enabled": True,
            "probe_mode": "BOUNDED_FREE_TIER_PROBE", "request_hard_limit": 1,
            "max_output_tokens": 8, "automatic_model_fallback": False,
            "generic_paid_router_disabled": True, "auto_top_up": False,
            "staging_only": True, "account_specific_zero_cost_proven": False,
            "bounded_free_tier_probe_allowed": True,
        }
    status = str(raw.get("status") or "PROBE_FAILED")
    usage_cost = raw.get("usage_cost")
    if usage_cost is not None and str(usage_cost).strip() not in {"0", "0.0", "0.00"}:
        status = "FREE_COST_NONZERO"
    response_model = raw.get("response_model")
    normalized_model = str(response_model or "").removeprefix("models/")
    if status == "PROBE_OK" and normalized_model and normalized_model != GOOGLE_MODEL:
        status = "MODEL_MISMATCH"
    if status == "PROBE_OK" and not normalized_model:
        status = "PROBE_OK_MODEL_FIELD_UNREPORTED"
    return {
        "provider": "google", "model": GOOGLE_MODEL, "model_configured": True,
        "status": status, "model_calls": 1, "retry_count": 0,
        "paid_fallback": False, "network_enabled": True,
        "probe_mode": "BOUNDED_FREE_TIER_PROBE", "request_hard_limit": 1,
        "max_output_tokens": 8, "automatic_model_fallback": False,
        "generic_paid_router_disabled": True, "auto_top_up": False,
        "staging_only": True, "account_specific_zero_cost_proven": False,
        "bounded_free_tier_probe_allowed": True,
        "response_model": response_model if isinstance(response_model, str) else None,
        "usage_cost": usage_cost,
        "latency_ms": raw.get("latency_ms") if isinstance(raw.get("latency_ms"), int) else None,
        "http_status": raw.get("http_status") if isinstance(raw.get("http_status"), int) else None,
        "soft_warnings": ["ACCOUNT_BILLING_METADATA_UNAVAILABLE", "QUOTA_METADATA_UNAVAILABLE", "PER_RESPONSE_COST_MAY_BE_UNREPORTED"],
    }


def run_focused_probe(evidence: Mapping[str, Any], *, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    # No inference is spent here. Fresh exact-route facts admit the first real
    # Executor/Reviewer calls, which themselves provide liveness evidence.
    nvidia_result = _nvidia_deferred_result(evidence)
    google_result = _google_deferred_result(evidence)
    providers = [nvidia_result, google_result]
    return {
        "schema_version": "provider-probe-v1",
        "network_enabled": True,
        "model_calls": 0,
        "retry_count": 0,
        "paid_fallback": False,
        "registry_changed": False,
        "explicit_probe_approval": True,
        "limited_staging_probe_approval": True,
        "focused_nvidia_model": NVIDIA_NEMOTRON_MODEL,
        "focused_google_model": GOOGLE_MODEL,
        "nvidia_liveness_deferred_to_first_agent_task": True,
        "google_liveness_deferred_to_first_agent_task": True,
        "google_bounded_free_tier_probe": False,
        "providers": providers,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-file", required=True)
    parser.add_argument("--output", default="artifacts/provider_probe.json")
    args = parser.parse_args()
    try:
        report = run_focused_probe(_read_json(args.evidence_file))
    except Exception:
        report = {
            "schema_version": "provider-probe-v1", "network_enabled": True,
            "model_calls": 0, "retry_count": 0, "paid_fallback": False,
            "registry_changed": False, "explicit_probe_approval": True,
            "limited_staging_probe_approval": True,
            "focused_nvidia_model": NVIDIA_NEMOTRON_MODEL,
            "focused_google_model": GOOGLE_MODEL,
            "nvidia_liveness_deferred_to_first_agent_task": True,
            "google_liveness_deferred_to_first_agent_task": True,
            "google_bounded_free_tier_probe": False,
            "status": "FOCUSED_PROBE_INVALID", "providers": [],
        }
    output = Path(args.output)
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output must stay inside the workspace")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
