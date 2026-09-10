#!/usr/bin/env python3
"""Focused NVIDIA + Google staging probe.

The focused lane keeps paid routing disabled but avoids deadlocking forever on
provider metadata that has no account/quota API. NVIDIA remains pinned to the
verified hosted FREE_ENDPOINT. Google may perform one tiny FREE_TIER probe when
all known paid-risk signals are absent and the only missing facts are account
or quota metadata that the provider does not expose.

Neither path selects a sibling model, enables production, retries inference,
uses auto top-up, or enables provider fallback.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover - script invocation path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.focused_nvidia_streaming_adapter import FocusedNvidiaStreamingAdapter
from scripts.probe_providers import run_probe
from scripts.provider_adapters import NVIDIA_NEMOTRON_MODEL, create_provider_adapter
from scripts.provider_registry import load_provider_registry


GOOGLE_MODEL = "gemini-3.8-flash"
GOOGLE_BOUNDED_TIMEOUT_SECONDS = 90.0
GOOGLE_SOFT_BLOCKERS = frozenset({
    "ACCOUNT_TIER_API_UNAVAILABLE",
    "AUTOMATIC_PAID_TRANSITION_UNKNOWN",
    "BILLING_TRANSITION_RISK_NOT_NONE",
    "CURRENT_ACCOUNT_ELIGIBILITY_UNKNOWN",
    "QUOTA_EVIDENCE_UNAVAILABLE",
    "QUOTA_NOT_SAFE",
    "QUOTA_NOT_VERIFIED",
})


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
    """Allow UNKNOWN provider metadata, never a known paid/bad route."""
    account = record.get("account_metadata") if isinstance(record.get("account_metadata"), Mapping) else {}
    blockers = set(str(item) for item in (record.get("blockers") or []) if isinstance(item, str))
    known_hard_blockers = blockers - GOOGLE_SOFT_BLOCKERS
    return (
        record.get("secure_evidence") is True
        and record.get("current") is True
        and _fresh(record)
        and record.get("model_verified") is True
        and record.get("catalog_verified") is True
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
        and not known_hard_blockers
    )


def _google_bounded_probe(registry: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    record = _model_record(evidence, "google", GOOGLE_MODEL)
    if not _google_bounded_probe_allowed(record):
        return {
            "provider": "google",
            "model": GOOGLE_MODEL,
            "model_configured": True,
            "status": "ZERO_COST_PREFLIGHT_BLOCKED",
            "model_calls": 0,
            "retry_count": 0,
            "paid_fallback": False,
            "network_enabled": True,
            "bounded_free_tier_probe_allowed": False,
        }
    adapter = create_provider_adapter(
        registry,
        "google",
        network_enabled=True,
        timeout_seconds=GOOGLE_BOUNDED_TIMEOUT_SECONDS,
    )
    try:
        raw = adapter.probe(GOOGLE_MODEL)
    except Exception:
        return {
            "provider": "google",
            "model": GOOGLE_MODEL,
            "model_configured": True,
            "status": "NETWORK_TIMEOUT_OR_PROVIDER_FAILURE",
            "model_calls": 1,
            "retry_count": 0,
            "paid_fallback": False,
            "network_enabled": True,
            "probe_mode": "BOUNDED_FREE_TIER_PROBE",
            "request_hard_limit": 1,
            "max_output_tokens": 8,
            "automatic_model_fallback": False,
            "generic_paid_router_disabled": True,
            "auto_top_up": False,
            "staging_only": True,
            "account_specific_zero_cost_proven": False,
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
    return {
        "provider": "google",
        "model": GOOGLE_MODEL,
        "model_configured": True,
        "status": status,
        "model_calls": 1,
        "retry_count": 0,
        "paid_fallback": False,
        "network_enabled": True,
        "probe_mode": "BOUNDED_FREE_TIER_PROBE",
        "request_hard_limit": 1,
        "max_output_tokens": 8,
        "automatic_model_fallback": False,
        "generic_paid_router_disabled": True,
        "auto_top_up": False,
        "staging_only": True,
        "account_specific_zero_cost_proven": False,
        "bounded_free_tier_probe_allowed": True,
        "response_model": response_model if isinstance(response_model, str) else None,
        "usage_cost": usage_cost,
        "latency_ms": raw.get("latency_ms") if isinstance(raw.get("latency_ms"), int) else None,
        "http_status": raw.get("http_status") if isinstance(raw.get("http_status"), int) else None,
        "soft_warnings": [
            "ACCOUNT_BILLING_METADATA_UNAVAILABLE",
            "QUOTA_METADATA_UNAVAILABLE",
        ],
    }


def run_focused_probe(
    evidence: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    # Copy instead of mutating os.environ/the caller mapping.  The focused lane
    # deliberately pins the proven Nemotron model so an old workflow variable
    # cannot silently reintroduce the slower/failed DeepSeek bootstrap path.
    env = dict(os.environ if environ is None else environ)
    env["NVIDIA_PROBE_MODEL"] = NVIDIA_NEMOTRON_MODEL
    registry = load_provider_registry()
    nvidia = FocusedNvidiaStreamingAdapter(registry, network_enabled=True)
    nvidia_report = run_probe(
        registry,
        ("nvidia",),
        network_enabled=True,
        adapters={"nvidia": nvidia},
        environ=env,
        free_evidence=evidence,
        explicit_approval=True,
        allow_limited_staging_probe=True,
    )
    google_result = _google_bounded_probe(registry, evidence)
    providers = list(nvidia_report.get("providers") or []) + [google_result]
    report = dict(nvidia_report)
    report["providers"] = providers
    report["model_calls"] = sum(int(item.get("model_calls", 0) or 0) for item in providers if isinstance(item, Mapping))
    report["focused_nvidia_model"] = NVIDIA_NEMOTRON_MODEL
    report["focused_google_model"] = GOOGLE_MODEL
    report["google_bounded_free_tier_probe"] = True
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-file", required=True)
    parser.add_argument("--output", default="artifacts/provider_probe.json")
    args = parser.parse_args()
    try:
        report = run_focused_probe(_read_json(args.evidence_file))
    except Exception:
        report = {
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
            "google_bounded_free_tier_probe": True,
            "status": "FOCUSED_PROBE_INVALID",
            "providers": [],
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
