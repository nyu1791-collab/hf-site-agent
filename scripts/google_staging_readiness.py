#!/usr/bin/env python3
"""Build a deterministic Google staging readiness / integration packet.

The packet separates model availability from account/billing/quota evidence and
prevents repeated model calls when the external blocker has not changed.  It
also performs a narrow integration-scope review of the NVIDIA Google bootstrap
proposal: a mission whose objective is Google free-only evidence/diagnostics is
not allowed to silently rewrite execution authorization policy.

This module performs no provider call and never reads credential values.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


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
    """Read an optional workspace JSON object, treating absence as no proposal.

    A skipped NVIDIA lead step intentionally produces no Result Inbox.  Missing
    optional output therefore means there is nothing to integrate, not that the
    Google evidence/probe inputs are invalid.  Malformed or unsafe paths still
    fail closed through ``_read``.
    """
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
        if (
            isinstance(item, Mapping)
            and item.get("provider") == "google"
            and item.get("model") == GOOGLE_MODEL
        ):
            return item
    return {}


def _proposal_files(result_inbox: Mapping[str, Any]) -> list[str]:
    proposal = _mapping(result_inbox.get("proposal"))
    files = proposal.get("files_to_change")
    if not isinstance(files, list):
        return []
    return [str(item) for item in files if isinstance(item, str) and item]


def build_google_readiness_packet(
    evidence: Mapping[str, Any],
    probe: Mapping[str, Any],
    result_inbox: Mapping[str, Any] | None = None,
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

    transport_ready = model_verified and endpoint_verified and auth_verified
    live_ready = zero_cost_verified and quota_safe and probe_status == "PROBE_OK"

    if live_ready:
        state = "READY_FOR_TWO_AGENT_STAGING"
        next_action = "BIND_GOOGLE_STAGING_AGENT"
    elif any(
        item in required_evidence
        for item in (
            "CURRENT_GOOGLE_ACCOUNT_TIER",
            "CURRENT_GOOGLE_BILLING_STATE",
            "NO_AUTOMATIC_PAID_TRANSITION",
        )
    ):
        state = "ACCOUNT_EVIDENCE_REQUIRED"
        next_action = "PROVIDE_CURRENT_GOOGLE_PLAN_AND_BILLING_EVIDENCE"
    elif "CURRENT_GOOGLE_QUOTA" in required_evidence:
        state = "QUOTA_EVIDENCE_REQUIRED"
        next_action = "REFRESH_CURRENT_GOOGLE_QUOTA_EVIDENCE"
    elif not transport_ready:
        state = "TRANSPORT_EVIDENCE_REQUIRED"
        next_action = "REFRESH_GOOGLE_CATALOG_AUTH_EVIDENCE"
    else:
        state = "PROBE_REQUIRED"
        next_action = "RUN_BOUNDED_GOOGLE_PROBE"

    inbox = _mapping(result_inbox)
    proposal_files = _proposal_files(inbox)
    out_of_scope_files = sorted(set(proposal_files) - GOOGLE_EVIDENCE_PATCH_PATHS)
    proposal_claims_complete = inbox.get("result_complete") is True and inbox.get("status") == "COMPLETE"
    proposal_safe_to_integrate = bool(proposal_claims_complete and proposal_files and not out_of_scope_files)
    if not proposal_files:
        integration_decision = "NO_PROPOSAL"
        integration_reason = "NO_PATCH_BUNDLE_TO_REVIEW"
    elif out_of_scope_files:
        integration_decision = "REJECT"
        integration_reason = "GOOGLE_EVIDENCE_MISSION_OUT_OF_SCOPE_PATH"
    elif not proposal_claims_complete:
        integration_decision = "REJECT"
        integration_reason = "LEAD_RESULT_NOT_COMPLETE"
    else:
        integration_decision = "ELIGIBLE_FOR_CODE_REVIEW"
        integration_reason = "PATH_SCOPE_PASS"

    # When Google is blocked by unchanged external account/billing evidence,
    # another NVIDIA design call cannot resolve the external fact.  Stop the
    # model loop and preserve the remaining provider quota / Work budget.
    repeat_nvidia_call_allowed = state not in {"ACCOUNT_EVIDENCE_REQUIRED", "QUOTA_EVIDENCE_REQUIRED"}

    return {
        "schema_version": "google-staging-readiness-v1",
        "provider": "google",
        "model": GOOGLE_MODEL,
        "state": state,
        "next_action": next_action,
        "transport_ready": transport_ready,
        "live_ready": live_ready,
        "probe_status": probe_status,
        "model_verified": model_verified,
        "endpoint_verified": endpoint_verified,
        "auth_verified": auth_verified,
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
        },
        "safety": {
            "paid_execution_allowed": False,
            "paid_fallback_allowed": False,
            "production_activation_allowed": False,
            "google_probe_allowed_while_account_evidence_missing": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--probe", required=True)
    parser.add_argument("--result-inbox", default="")
    parser.add_argument("--output", default="artifacts/google_staging_readiness.json")
    args = parser.parse_args()
    try:
        inbox = _read_optional(args.result_inbox)
        report = build_google_readiness_packet(_read(args.evidence), _read(args.probe), inbox)
    except Exception:
        report = {
            "schema_version": "google-staging-readiness-v1",
            "provider": "google",
            "model": GOOGLE_MODEL,
            "state": "BLOCKED_INVALID_INPUT",
            "next_action": "REFRESH_GOOGLE_EVIDENCE",
            "live_ready": False,
            "repeat_nvidia_call_allowed": False,
            "external_model_calls_recommended": 0,
            "safety": {
                "paid_execution_allowed": False,
                "paid_fallback_allowed": False,
                "production_activation_allowed": False,
                "google_probe_allowed_while_account_evidence_missing": False,
            },
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
