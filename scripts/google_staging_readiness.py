#!/usr/bin/env python3
"""Build a deterministic Google staging readiness / integration packet.

The packet separates public model/free-tier availability from current
account/billing/quota evidence. A verified fixed FREE_TIER route may defer its
inference liveness check to the first real Executor task, avoiding a redundant
request immediately before agent work. Known paid routing, billing enablement,
non-zero pricing, stale evidence or model mismatch remain hard blockers.

This module performs no provider call and never reads credential values.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover - direct script entrypoint
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.mission_integrity import validate_result_inbox


GOOGLE_MODEL = "gemini-3.8-flash"
GOOGLE_EVIDENCE_PATCH_PATHS = frozenset({
    "scripts/secure_account_evidence.py",
    "scripts/free_evidence.py",
    "scripts/probe_providers.py",
    "tests/test_secure_account_evidence.py",
    "tests/test_free_evidence.py",
    "tests/test_probe_providers.py",
})


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _read(path_value: str) -> Mapping[str, Any]:
    path = Path(path_value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("input must stay inside the workspace")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("input must contain an object")
    return value


def _read_optional(path_value: str) -> Mapping[str, Any]:
    if not path_value:
        return {}
    path = Path(path_value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("input must stay inside the workspace")
    if not path.exists():
        return {}
    return _read(path_value)


def _google_record(evidence: Mapping[str, Any]) -> Mapping[str, Any]:
    providers = _mapping(evidence.get("providers"))
    google = _mapping(providers.get("google"))
    models = _mapping(google.get("models"))
    return _mapping(models.get(GOOGLE_MODEL))


def _google_probe(probe: Mapping[str, Any]) -> Mapping[str, Any]:
    items = probe.get("providers")
    if not isinstance(items, list):
        return {}
    for item in items:
        if isinstance(item, Mapping) and item.get("provider") == "google" and item.get("model") == GOOGLE_MODEL:
            return item
    return {}


def _proposal_files(result_inbox: Mapping[str, Any]) -> list[str]:
    proposal = _mapping(result_inbox.get("proposal"))
    files = proposal.get("files_to_change")
    if not isinstance(files, list):
        return []
    return [str(item) for item in files if isinstance(item, str) and item]


def _fresh(record: Mapping[str, Any]) -> bool:
    expiry = record.get("expires_at")
    if not isinstance(expiry, str) or not expiry.strip():
        return False
    try:
        parsed = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed > datetime.now(timezone.utc)


def _bounded_free_tier_evidence(record: Mapping[str, Any]) -> bool:
    account = _mapping(record.get("account_metadata"))
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


def _deferred_agent_ready(record: Mapping[str, Any], probe_record: Mapping[str, Any]) -> bool:
    return (
        _bounded_free_tier_evidence(record)
        and probe_record.get("status") == "PROBE_DEFERRED_TO_AGENT"
        and probe_record.get("probe_mode") == "DIRECT_AGENT_LIVENESS"
        and probe_record.get("direct_agent_admission") is True
        and probe_record.get("model_calls") == 0
        and probe_record.get("automatic_model_fallback") is False
        and probe_record.get("generic_paid_router_disabled") is True
        and probe_record.get("staging_only") is True
    )


def build_google_readiness_packet(
    evidence: Mapping[str, Any],
    probe: Mapping[str, Any],
    result_inbox: Mapping[str, Any] | None = None,
    *,
    expected_source_head: str | None = None,
    expected_mission_id: str | None = None,
    expected_run_id: str | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    record = _google_record(evidence)
    probe_record = _google_probe(probe)
    account = _mapping(record.get("account_metadata"))

    tier = str(account.get("current_account_tier") or record.get("account_tier_class") or "UNKNOWN").upper()
    account_eligible = account.get("current_account_eligible")
    billing_enabled = account.get("billing_enabled")
    auto_paid = account.get("automatic_paid_transition_possible")
    billing_risk = str(account.get("billing_transition_risk") or "UNKNOWN").upper()
    quota_verified = record.get("quota_verified") is True
    quota_safe = record.get("quota_safe") is True
    model_verified = record.get("model_verified") is True
    endpoint_verified = record.get("endpoint_verified") is True
    auth_verified = record.get("auth_verified") is True
    zero_cost_verified = record.get("zero_cost_verified") is True
    secure_evidence = record.get("secure_evidence") is True
    evidence_fresh = _fresh(record)
    probe_status = str(probe_record.get("status") or "NOT_RUN")

    required_evidence: list[str] = []
    if tier == "UNKNOWN" or account_eligible is not True:
        required_evidence.append("CURRENT_GOOGLE_ACCOUNT_TIER")
    if billing_enabled is None:
        required_evidence.append("CURRENT_GOOGLE_BILLING_STATE")
    if auto_paid is not False or billing_risk != "NONE":
        required_evidence.append("NO_AUTOMATIC_PAID_TRANSITION")
    if not quota_verified or not quota_safe:
        required_evidence.append("CURRENT_GOOGLE_QUOTA")
    if not secure_evidence or not evidence_fresh:
        required_evidence.append("FRESH_SECURE_GOOGLE_EVIDENCE")

    transport_ready = model_verified and endpoint_verified and auth_verified
    strict_live_ready = (
        transport_ready and secure_evidence and evidence_fresh and zero_cost_verified
        and quota_safe and probe_status == "PROBE_OK"
    )
    bounded_probe_ready = (
        _bounded_free_tier_evidence(record)
        and probe_status == "PROBE_OK"
        and probe_record.get("probe_mode") == "BOUNDED_FREE_TIER_PROBE"
        and probe_record.get("bounded_free_tier_probe_allowed") is True
        and probe_record.get("model_calls") == 1
        and probe_record.get("automatic_model_fallback") is False
        and probe_record.get("generic_paid_router_disabled") is True
    )
    deferred_agent_ready = _deferred_agent_ready(record, probe_record)
    live_ready = strict_live_ready or bounded_probe_ready or deferred_agent_ready
    readiness_mode = (
        "STRICT_ZERO_COST" if strict_live_ready
        else "BOUNDED_FREE_TIER" if bounded_probe_ready
        else "DIRECT_AGENT_LIVENESS" if deferred_agent_ready
        else "BLOCKED"
    )

    if live_ready:
        state = "READY_FOR_TWO_AGENT_STAGING"
        next_action = "BIND_GOOGLE_STAGING_AGENT"
    elif any(item in required_evidence for item in (
        "CURRENT_GOOGLE_ACCOUNT_TIER", "CURRENT_GOOGLE_BILLING_STATE", "NO_AUTOMATIC_PAID_TRANSITION"
    )):
        state = "ACCOUNT_EVIDENCE_REQUIRED"
        next_action = "REFRESH_ACCOUNT_EVIDENCE_OR_VERIFY_FIXED_FREE_ROUTE"
    elif "CURRENT_GOOGLE_QUOTA" in required_evidence:
        state = "QUOTA_EVIDENCE_REQUIRED"
        next_action = "REFRESH_QUOTA_EVIDENCE_OR_VERIFY_FIXED_FREE_ROUTE"
    elif "FRESH_SECURE_GOOGLE_EVIDENCE" in required_evidence:
        state = "EVIDENCE_REFRESH_REQUIRED"
        next_action = "REFRESH_GOOGLE_EVIDENCE"
    elif not transport_ready:
        state = "TRANSPORT_EVIDENCE_REQUIRED"
        next_action = "REFRESH_GOOGLE_CATALOG_AUTH_EVIDENCE"
    else:
        state = "PROBE_REQUIRED"
        next_action = "VERIFY_GOOGLE_FIXED_FREE_ROUTE"

    inbox = _mapping(result_inbox)
    proposal_files = _proposal_files(inbox)
    out_of_scope_files = sorted(set(proposal_files) - GOOGLE_EVIDENCE_PATCH_PATHS)

    if not inbox:
        integrity = {"valid": False, "reason": "NO_PROPOSAL", "result_hash_verified": False, "source_head_verified": False}
        integration_decision = "NO_PROPOSAL"
        integration_reason = "NO_PATCH_BUNDLE_TO_REVIEW"
        proposal_safe_to_integrate = False
    else:
        integrity = validate_result_inbox(
            inbox,
            expected_mission_id=expected_mission_id,
            expected_run_id=expected_run_id,
            expected_revision=expected_revision,
            expected_source_head=expected_source_head,
            require_complete=True,
        )
        if not integrity.get("valid"):
            integration_decision = "REJECT"
            integration_reason = str(integrity.get("reason") or "RESULT_INTEGRITY_FAILED")
            proposal_safe_to_integrate = False
        elif out_of_scope_files:
            integration_decision = "REJECT"
            integration_reason = "GOOGLE_EVIDENCE_MISSION_OUT_OF_SCOPE_PATH"
            proposal_safe_to_integrate = False
        elif not proposal_files:
            integration_decision = "REJECT"
            integration_reason = "PATCH_FILE_SET_MISSING"
            proposal_safe_to_integrate = False
        else:
            integration_decision = "ELIGIBLE_FOR_CODE_REVIEW"
            integration_reason = "RESULT_INTEGRITY_AND_PATH_SCOPE_PASS"
            proposal_safe_to_integrate = True

    repeat_nvidia_call_allowed = state not in {
        "ACCOUNT_EVIDENCE_REQUIRED", "QUOTA_EVIDENCE_REQUIRED", "EVIDENCE_REFRESH_REQUIRED"
    } or bounded_probe_ready or deferred_agent_ready

    return {
        "schema_version": "google-staging-readiness-v3",
        "provider": "google",
        "model": GOOGLE_MODEL,
        "state": state,
        "next_action": next_action,
        "readiness_mode": readiness_mode,
        "transport_ready": transport_ready,
        "live_ready": live_ready,
        "strict_live_ready": strict_live_ready,
        "bounded_probe_ready": bounded_probe_ready,
        "deferred_agent_ready": deferred_agent_ready,
        "probe_status": probe_status,
        "model_verified": model_verified,
        "endpoint_verified": endpoint_verified,
        "auth_verified": auth_verified,
        "secure_evidence": secure_evidence,
        "evidence_fresh": evidence_fresh,
        "zero_cost_verified": zero_cost_verified,
        "quota_verified": quota_verified,
        "quota_safe": quota_safe,
        "account_tier": tier,
        "account_eligible": account_eligible,
        "billing_enabled": billing_enabled,
        "automatic_paid_transition_possible": auto_paid,
        "billing_transition_risk": billing_risk,
        "required_evidence": required_evidence,
        "external_model_calls_recommended": 0 if not repeat_nvidia_call_allowed else 1,
        "repeat_nvidia_call_allowed": repeat_nvidia_call_allowed,
        "lead_integration": {
            "decision": integration_decision,
            "reason": integration_reason,
            "proposal_safe_to_integrate": proposal_safe_to_integrate,
            "proposal_files": proposal_files,
            "out_of_scope_files": out_of_scope_files,
            "result_integrity": integrity,
        },
        "safety": {
            "paid_execution_allowed": False,
            "paid_fallback_allowed": False,
            "production_activation_allowed": False,
            "unknown_account_metadata_may_use_bounded_free_tier": True,
            "known_billing_enabled_blocks_bounded_route": True,
            "known_paid_transition_blocks_bounded_route": True,
            "redundant_liveness_probe_required": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--probe", required=True)
    parser.add_argument("--result-inbox", default="")
    parser.add_argument("--expected-head", default="")
    parser.add_argument("--expected-mission-id", default="")
    parser.add_argument("--expected-run-id", default="")
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--output", default="artifacts/google_staging_readiness.json")
    args = parser.parse_args()
    try:
        inbox = _read_optional(args.result_inbox)
        report = build_google_readiness_packet(
            _read(args.evidence), _read(args.probe), inbox,
            expected_source_head=args.expected_head or None,
            expected_mission_id=args.expected_mission_id or None,
            expected_run_id=args.expected_run_id or None,
            expected_revision=args.expected_revision,
        )
    except Exception:
        report = {
            "schema_version": "google-staging-readiness-v3", "provider": "google", "model": GOOGLE_MODEL,
            "state": "BLOCKED_INVALID_INPUT", "next_action": "REFRESH_GOOGLE_EVIDENCE", "live_ready": False,
            "repeat_nvidia_call_allowed": False, "external_model_calls_recommended": 0,
            "safety": {"paid_execution_allowed": False, "paid_fallback_allowed": False, "production_activation_allowed": False},
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
