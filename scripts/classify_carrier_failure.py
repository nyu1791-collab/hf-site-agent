#!/usr/bin/env python3
"""Classify bounded carrier failures without retaining provider responses.

The carrier may encounter several independent provider failures in one run.
This adapter turns the already-redacted evidence/probe/live reports into
stable failure signatures and next actions.  It deliberately accepts only
status-shaped fields; raw responses and exception text are never copied to
the report.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
import json
from pathlib import Path
from typing import Any


ERROR_ACTIONS: dict[str, str] = {
    "DISPATCH_REGISTRATION_MISMATCH": "switch to a workflow registered on the default branch",
    "BRANCH_GUARD_BLOCKED": "fix the target-branch guard before dispatching again",
    "INPUT_SCHEMA_MISMATCH": "remove unavailable required inputs and use dispatch plus target branch",
    "SECRET_ABSENT": "block this provider and continue independent provider lanes",
    "AUTH_FAILED": "block this provider; inspect provider authentication without exposing the secret",
    "PROVIDER_HTTP_ERROR": "keep this provider blocked and inspect the redacted HTTP status before changing routes",
    "MODEL_NOT_FOUND": "revalidate the exact current catalog ID; do not guess a sibling",
    "FREE_ROUTE_NOT_VERIFIED": "block the route until current zero-cost evidence is available",
    "QUOTA_EXHAUSTED": "wait or route to an independently verified eligible provider",
    "RATE_LIMITED": "stop new calls for this provider; do not start a retry storm",
    "PROVIDER_5XX": "isolate the provider circuit and resume from checkpoint when safe",
    "CATALOG_CONFLICT": "keep this provider blocked until official catalog evidence agrees",
    "MODEL_MISMATCH": "discard the response and revalidate the exact requested model",
    "SCHEMA_INVALID": "discard the malformed result and stop the affected step",
    "REVIEW_FAILED": "return bounded findings to the executor for revision",
    "LOCAL_TEST_FAILED": "return deterministic test findings to the executor",
}

_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("DISPATCH_REGISTRATION_MISMATCH", ("THIS_WORKFLOW_DOES_NOT_EXIST", "WORKFLOW_NOT_FOUND", "DISPATCH_REGISTRATION")),
    ("BRANCH_GUARD_BLOCKED", ("BRANCH_GUARD", "TARGET_BRANCH_NOT_ALLOWED")),
    ("INPUT_SCHEMA_MISMATCH", ("INPUT_SCHEMA", "REQUIRED_INPUT")),
    ("SECRET_ABSENT", ("SECRET_NOT_PRESENT", "AUTH_NOT_CONFIGURED", "SECRET_ABSENT")),
    ("AUTH_FAILED", ("AUTH_FAILED", "AUTH_ERROR", "HTTP_401", "HTTP_403", "UNAUTHORIZED", "FORBIDDEN")),
    ("MODEL_NOT_FOUND", ("MODEL_NOT_FOUND", "MODEL_NOT_CONFIGURED", "MODEL_UNAVAILABLE", "NOT_IN_CATALOG")),
    ("RATE_LIMITED", ("RATE_LIMITED", "HTTP_429", "TOO_MANY_REQUESTS")),
    ("PROVIDER_5XX", ("PROVIDER_5XX", "HTTP_500", "HTTP_502", "HTTP_503", "TEMPORARY_PROVIDER_ERROR")),
    ("CATALOG_CONFLICT", ("CATALOG_CONFLICT", "CATALOG_INCONSISTENCY", "CATALOG_HTTP_ERROR")),
    ("PROVIDER_HTTP_ERROR", ("PROVIDER_HTTP_ERROR", "HTTP_ERROR", "COLLECTOR_FAILED")),
    ("FREE_ROUTE_NOT_VERIFIED", ("ZERO_COST_PREFLIGHT", "FREE_ROUTE", "COST_UNVERIFIED", "PAID_ROUTE")),
    ("QUOTA_EXHAUSTED", ("QUOTA_EXHAUSTED", "QUOTA_NOT_SAFE", "HARD_STOP", "DAILY_CAP")),
    ("MODEL_MISMATCH", ("MODEL_MISMATCH",)),
    ("SCHEMA_INVALID", ("SCHEMA_INVALID", "EVIDENCE_SCHEMA", "REPORT_SCHEMA")),
    ("REVIEW_FAILED", ("REVIEW_FAILED", "REVIEW_SCHEMA_INVALID")),
    ("LOCAL_TEST_FAILED", ("LOCAL_TEST_FAILED", "LOCAL_VALIDATION_FAILED", "TEST_FAILED")),
)


def _safe_text(value: Any, limit: int = 160) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > limit or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return ""
    return value


def _read(path: str | Path) -> Mapping[str, Any]:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError("report path must stay inside the workspace")
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("report must be an object")
    return payload


def _classify(values: Iterable[Any]) -> str | None:
    status_codes: list[int] = []
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            candidate = value
        elif isinstance(value, str) and value.strip().isdigit():
            candidate = int(value.strip())
        else:
            continue
        if 100 <= candidate <= 599:
            status_codes.append(candidate)
    if any(code in {401, 403} for code in status_codes):
        return "AUTH_FAILED"
    if 429 in status_codes:
        return "RATE_LIMITED"
    if any(500 <= code <= 599 for code in status_codes):
        return "PROVIDER_5XX"
    haystack = " ".join(_safe_text(value).upper() for value in values if _safe_text(value))
    if not haystack:
        return None
    for error_type, tokens in _TOKENS:
        if any(token in haystack for token in tokens):
            return error_type
    return None


def _signature(error_type: str, provider: str = "", model: str = "", step: str = "") -> dict[str, str]:
    return {
        "error_type": error_type,
        "provider": _safe_text(provider, 40),
        "workflow": "probe-free-models.yml",
        "step": _safe_text(step, 80),
        "model": _safe_text(model, 200),
    }


def _append_failure(failures: list[dict[str, Any]], error_type: str | None, *, provider: str = "", model: str = "", step: str = "") -> None:
    if not error_type:
        return
    signature = _signature(error_type, provider, model, step)
    if signature not in [item["failure_signature"] for item in failures]:
        failures.append({
            "failure_signature": signature,
            "next_action": ERROR_ACTIONS[error_type],
            "same_signature_threshold": 2,
            "recovery": "REPLAN_ON_SECOND_IDENTICAL_SIGNATURE",
        })


def _evidence_failures(report: Mapping[str, Any], failures: list[dict[str, Any]]) -> None:
    providers = report.get("providers")
    if not isinstance(providers, Mapping):
        _append_failure(failures, _classify((report.get("status"),)), step="secure_account_evidence")
        return
    for provider, lane in providers.items():
        if not isinstance(lane, Mapping):
            continue
        lane_type = _classify((lane.get("status"), lane.get("http_status")))
        models = lane.get("models") if isinstance(lane.get("models"), Mapping) else {}
        if not models:
            _append_failure(failures, lane_type, provider=str(provider), step="secure_account_evidence")
            continue
        for model, record in models.items():
            if not isinstance(record, Mapping):
                continue
            status_values: list[Any] = [record.get("status"), record.get("http_status")]
            if isinstance(record.get("blockers"), list):
                status_values.extend(record["blockers"])
            error_type = _classify(status_values)
            # A lane-level transport/auth failure is more specific than the
            # derived quota/account blockers added to each model record.
            if lane_type in {"AUTH_FAILED", "RATE_LIMITED", "PROVIDER_5XX", "CATALOG_CONFLICT", "PROVIDER_HTTP_ERROR"}:
                error_type = lane_type
            _append_failure(
                failures,
                error_type or lane_type,
                provider=str(provider),
                model=str(model),
                step="secure_account_evidence",
            )


def _probe_failures(report: Mapping[str, Any], failures: list[dict[str, Any]]) -> None:
    providers = report.get("providers")
    if not isinstance(providers, list):
        _append_failure(failures, _classify((report.get("status"),)), step="probe_providers")
        return
    for item in providers:
        if not isinstance(item, Mapping) or item.get("status") in {"PROBE_OK", "DRY_RUN_NO_REQUEST"}:
            continue
        _append_failure(
            failures,
            _classify((item.get("status"),)),
            provider=str(item.get("provider") or ""),
            model=str(item.get("model") or ""),
            step="probe_providers",
        )


def classify_reports(
    evidence: Mapping[str, Any],
    probe: Mapping[str, Any],
    live: Mapping[str, Any],
    *,
    branch: str = "ai-army/provider-v3",
) -> dict[str, Any]:
    """Return a redacted carrier status and stable failure signatures."""
    failures: list[dict[str, Any]] = []
    _evidence_failures(evidence, failures)
    _probe_failures(probe, failures)
    live_status = _safe_text(live.get("status"))
    if live_status not in {"", "completed"}:
        _append_failure(
            failures,
            _classify((live_status, live.get("stop_reason"))),
            provider="",
            model=str(live.get("executor_model") or ""),
            step="run_live_staging_from_probe",
        )
    status = "SUCCESS" if not failures and live_status in {"", "completed"} else ("PARTIAL" if failures else "BLOCKED")
    return {
        "schema_version": "carrier-failure-classification-v1",
        "status": status,
        "branch": _safe_text(branch, 120),
        "workflow": "probe-free-models.yml",
        "failure_signatures": failures,
        "failure_count": len(failures),
        "same_failure_policy": "same error_type/provider/workflow/step/model twice => REPLAN",
        "raw_provider_response_retained": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", default="artifacts/secure_account_evidence.json")
    parser.add_argument("--probe", default="artifacts/provider_probe.json")
    parser.add_argument("--live", default="artifacts/live_staging_report.json")
    parser.add_argument("--branch", default="ai-army/provider-v3")
    parser.add_argument("--output", default="artifacts/failure_classification.json")
    args = parser.parse_args(argv)
    try:
        report = classify_reports(_read(args.evidence), _read(args.probe), _read(args.live), branch=args.branch)
    except Exception:
        report = {
            "schema_version": "carrier-failure-classification-v1",
            "status": "BLOCKED",
            "branch": _safe_text(args.branch, 120),
            "workflow": "probe-free-models.yml",
            "failure_signatures": [{
                "failure_signature": _signature("SCHEMA_INVALID", step="carrier_report_read"),
                "next_action": ERROR_ACTIONS["SCHEMA_INVALID"],
                "same_signature_threshold": 2,
                "recovery": "REPLAN_ON_SECOND_IDENTICAL_SIGNATURE",
            }],
            "failure_count": 1,
            "same_failure_policy": "same error_type/provider/workflow/step/model twice => REPLAN",
            "raw_provider_response_retained": False,
        }
    output = Path(args.output)
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output must stay inside the workspace")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "failure_count": report["failure_count"],
        "raw_provider_response_retained": report["raw_provider_response_retained"],
    }, sort_keys=True))
    return 0


__all__ = ["classify_reports"]


if __name__ == "__main__":
    raise SystemExit(main())
