#!/usr/bin/env python3
"""Run a redacted, bounded Modal readiness check.

The default mode is deterministic and makes no network request.  A live
billing/auth check requires ``--network`` and the exact confirmation token;
this command never activates Modal, deploys an App, publishes anything, or
starts a GPU job.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover - script invocation path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.compute_provider_registry import load_compute_provider_registry, safe_compute_provider_status
from scripts.modal_adapter import ModalAdapter, ModalAdapterError, VALIDATION_CONFIRMATION
from scripts.modal_cost_guard import ModalBillingSnapshot, ModalCostGuard, ModalCostGuardError, ModalCostState, ModalJobSpec


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_error(error: BaseException) -> str:
    return str(getattr(error, "reason", None) or getattr(error, "error_class", None) or "CHECK_FAILED")[:100]


def _dry_run_fail_closed() -> dict[str, Any]:
    """Exercise unknown billing and invalid numeric boundaries without I/O."""

    with tempfile.TemporaryDirectory(prefix="modal-guard-check-") as directory:
        guard = ModalCostGuard(Path(directory) / "ledger.json", shared_store_ready=True, allow_initialize=True)
        job = ModalJobSpec(
            mission_id="VALIDATION-MISSION",
            job_id="VALIDATION-JOB",
            idempotency_key="VALIDATION-IDEMPOTENCY",
            estimated_max_cost="0.01",
        )
        unknown = guard.preflight(ModalBillingSnapshot(None, None, None, None), job)
        unknown_blocked = unknown.get("MODAL_COST_STATE") == ModalCostState.UNKNOWN and unknown.get("MODAL_NEW_JOBS_ALLOWED") is False
        invalid_blocked = False
        try:
            ModalBillingSnapshot.from_mapping({
                "billing_cycle": "2026-09",
                "official_metered_cost": "NaN",
                "usage_limit": "1",
                "credit_remaining": "1",
                "billing_verified": True,
                "rates_verified": True,
            })
        except ModalCostGuardError:
            invalid_blocked = True
        return {
            "unknown_billing_denied": unknown_blocked,
            "invalid_numeric_denied": invalid_blocked,
            "passed": unknown_blocked and invalid_blocked,
        }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    registry = load_compute_provider_registry(args.registry)
    provider = registry["providers"]["modal"]
    live_enabled = bool(args.network and args.confirm == VALIDATION_CONFIRMATION)
    adapter = ModalAdapter(network_enabled=live_enabled, confirmation=args.confirm)
    secrets = adapter.secret_presence()
    sdk = adapter.sdk_version()

    if live_enabled:
        auth = adapter.probe_auth()
    else:
        auth = {"status": "DRY_RUN_NO_REQUEST", "secret_values_emitted": False}

    billing_result: dict[str, Any]
    rates_result: dict[str, Any]
    if live_enabled and auth.get("status") == "AUTH_OK":
        billing_result = adapter.get_billing_summary(args.billing_cycle)
        rates_result = adapter.get_billing_rates()
    else:
        billing_result = {"status": "NOT_RUN", "billing_verified": False, "rates_verified": False}
        rates_result = {"status": "NOT_RUN", "rates_verified": False}

    combined = dict(billing_result)
    combined["rates_verified"] = rates_result.get("rates_verified") is True
    try:
        billing = ModalBillingSnapshot.from_mapping(combined)
        billing_safe = billing.as_report()
    except ModalCostGuardError as error:
        billing = None
        billing_safe = {"known": False, "unknown_reason": error.reason}

    shared_ready = os.environ.get("MODAL_LEDGER_SHARED", "").strip().lower() == "true"
    guard = ModalCostGuard(
        args.ledger,
        policy=registry["policy"],
        shared_store_ready=shared_ready,
        allow_initialize=False,
    )
    ledger_status = guard.ledger_status()
    ledger_summary = guard.summary(billing)
    fail_closed = _dry_run_fail_closed()
    auth_ready = auth.get("status") == "AUTH_OK"
    billing_ready = billing is not None and billing.known and billing_result.get("status") == "BILLING_OK"
    rates_ready = rates_result.get("status") == "RATES_OK" and rates_result.get("rates_verified") is True
    atomic_ready = ledger_status.get("ready") is True and ledger_status.get("cross_runner_lock_supported") is True
    ready_for_activation = bool(
        auth_ready and billing_ready and rates_ready and sdk.get("status") == "SDK_AVAILABLE"
        and atomic_ready and fail_closed.get("passed") is True
        and secrets.get("modal_token_id_present") is True
        and secrets.get("modal_token_secret_present") is True
    )

    safe_provider = safe_compute_provider_status(provider)
    safe_provider.update({
        "secret_presence": secrets,
        "sdk": sdk,
        "auth_status": auth.get("status"),
        "billing_status": billing_result.get("status"),
        "rates_status": rates_result.get("status"),
        "new_jobs_allowed": False,
        "activation_approved": False,
    })
    return {
        "schema_version": "modal-validation-report-v1",
        "phase": "MODAL_COMPUTE_VALIDATION",
        "checked_at": _now(),
        "network_requested": bool(args.network),
        "network_enabled": live_enabled,
        "confirmation_ok": args.confirm == VALIDATION_CONFIRMATION,
        "provider": safe_provider,
        "billing": billing_safe,
        "rates": {
            "status": rates_result.get("status"),
            "rates_verified": rates_result.get("rates_verified") is True,
            "rate_count": rates_result.get("rate_count"),
        },
        "ledger": ledger_status,
        "ledger_summary": ledger_summary,
        "fail_closed": fail_closed,
        "safety": {
            "free_only_mode": True,
            "paid_execution_count": 0,
            "paid_fallback": False,
            "auto_top_up": False,
            "compute_jobs_started": 0,
            "cpu_jobs_started": 0,
            "gpu_jobs_started": 0,
            "deploy_count": 0,
            "publish_count": 0,
            "secret_values_emitted": False,
            "registry_changed": False,
            "production_routing_changed": False,
        },
        "gates": {
            "MODAL_AUTH_READY": auth_ready,
            "SDK_READY": sdk.get("status") == "SDK_AVAILABLE",
            "BILLING_API_READY": billing_ready,
            "RATES_API_READY": rates_ready,
            "LEDGER_READY": ledger_status.get("ready") is True,
            "ATOMIC_RESERVATION_READY": atomic_ready,
            "FAIL_CLOSED_VERIFIED": fail_closed.get("passed") is True,
            "MODAL_NEW_JOBS_ALLOWED": False,
        },
        "final": {
            "MODAL_STATE": "READY_FOR_ACTIVATION" if ready_for_activation else "NOT_READY",
            "MODAL_NEW_JOBS_ALLOWED": False,
            "activation_approved": False,
            "production_activation_performed": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=Path(__file__).resolve().parents[1] / "config" / "compute_provider_registry.json")
    parser.add_argument("--ledger", type=Path, default=Path("artifacts/modal_cost_ledger.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/modal_validation_report.json"))
    parser.add_argument("--network", action="store_true", help="Allow official auth/billing calls only with the exact confirmation token")
    parser.add_argument("--confirm", default="", help="Exact validation confirmation; never a secret")
    parser.add_argument("--billing-cycle", default=None, help="Explicit official billing cycle; no cycle is guessed")
    args = parser.parse_args(argv)
    try:
        report = build_report(args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "MODAL_STATE": report["final"]["MODAL_STATE"],
            "MODAL_NEW_JOBS_ALLOWED": report["final"]["MODAL_NEW_JOBS_ALLOWED"],
            "network_enabled": report["network_enabled"],
            "paid_execution_count": report["safety"]["paid_execution_count"],
            "secret_values_emitted": report["safety"]["secret_values_emitted"],
        }, sort_keys=True))
        return 0
    except (ModalAdapterError, ModalCostGuardError, OSError, ValueError):
        print(json.dumps({"MODAL_STATE": "NOT_READY", "MODAL_NEW_JOBS_ALLOWED": False, "error": "VALIDATION_BLOCKED"}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
