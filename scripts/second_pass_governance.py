#!/usr/bin/env python3
"""Deterministic governance helpers for untrusted content, factual claims, and creative experiments.

This module deliberately does not call an LLM or external service. It provides a small
machine oracle for the invariants adopted in the second-pass cross-source policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UNTRUSTED_SOURCE_KINDS = {
    "WEB", "SEARCH", "EMAIL_OR_MESSAGE", "TOOL_OUTPUT", "REPOSITORY_NONCANONICAL",
    "EXTERNAL_FILE", "MODEL_OUTPUT", "OTHER_EXTERNAL",
}
RANDOMIZED_MODES = {"RANDOMIZED_AB", "RANDOMIZED_ABC", "HOLDOUT_LIFT", "SEQUENTIAL_VALID"}
CAUSAL_WINNER = "WINNER_CAUSAL"


class GovernanceError(ValueError):
    """Raised when an artifact violates a permanent governance invariant."""


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise GovernanceError(message)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def wrap_untrusted_content(*, content_id: str, source_kind: str, source_ref: str,
                           content: str, retrieved_at: str | None = None) -> dict[str, Any]:
    """Wrap external content as evidence-only untrusted data before agent handoff."""
    _require(bool(content_id.strip()), "content_id required")
    _require(source_kind in UNTRUSTED_SOURCE_KINDS, "unsupported source_kind")
    _require(bool(source_ref.strip()), "source_ref required")
    return {
        "schema_version": "untrusted-content-envelope-v1",
        "content_id": content_id,
        "source_kind": source_kind,
        "source_ref": source_ref,
        "retrieved_at": retrieved_at or _utc_now(),
        "content_sha256": _sha256_text(content),
        "trust": "UNTRUSTED_DATA",
        "authority": "EVIDENCE_ONLY_NOT_INSTRUCTION",
        "content": content,
        "may_expand_permissions": False,
        "may_request_secret_disclosure": False,
        "derived_urls": [],
        "handoff_chain": [],
    }


def validate_untrusted_content_envelope(obj: dict[str, Any]) -> None:
    _require(obj.get("schema_version") == "untrusted-content-envelope-v1", "invalid untrusted envelope schema")
    _require(obj.get("source_kind") in UNTRUSTED_SOURCE_KINDS, "invalid source_kind")
    _require(obj.get("trust") == "UNTRUSTED_DATA", "external content must remain untrusted data")
    _require(obj.get("authority") == "EVIDENCE_ONLY_NOT_INSTRUCTION", "untrusted content gained instruction authority")
    _require(obj.get("may_expand_permissions") is False, "untrusted content cannot expand permissions")
    _require(obj.get("may_request_secret_disclosure") is False, "untrusted content cannot request secret disclosure")
    content = obj.get("content")
    _require(isinstance(content, str), "content must be string")
    expected = _sha256_text(content)
    _require(obj.get("content_sha256") == expected, "content hash mismatch")
    _require(bool(str(obj.get("source_ref") or "").strip()), "source_ref required")


def validate_claim_ledger(obj: dict[str, Any], *, for_publish_handoff: bool = False) -> None:
    _require(obj.get("schema_version") == "claim-evidence-ledger-v1", "invalid claim ledger schema")
    claims = obj.get("claims")
    _require(isinstance(claims, list), "claims must be array")
    ids: set[str] = set()
    for claim in claims:
        _require(isinstance(claim, dict), "claim must be object")
        cid = str(claim.get("claim_id") or "")
        _require(bool(cid), "claim_id required")
        _require(cid not in ids, f"duplicate claim_id: {cid}")
        ids.add(cid)
        refs = claim.get("source_refs")
        _require(isinstance(refs, list) and len(refs) >= 1, f"{cid}: at least one source_ref required")
        _require(claim.get("status") in {"VERIFIED", "CONTESTED", "UNKNOWN", "EXPIRED", "NOT_APPLICABLE"}, f"{cid}: invalid status")
        _require(claim.get("contradiction_status") in {"NONE_FOUND", "CONTRADICTED", "PARTIALLY_CONTRADICTED", "NOT_CHECKED", "NOT_APPLICABLE"}, f"{cid}: invalid contradiction_status")
        _require(claim.get("freshness_ttl_seconds") is not None or claim.get("expires_at") is not None, f"{cid}: freshness TTL or expiry required")
        if for_publish_handoff and claim.get("blocking_for_publish") is True:
            _require(claim.get("status") not in {"EXPIRED", "CONTESTED"}, f"{cid}: blocking claim is expired/contested")
            _require(claim.get("contradiction_status") not in {"CONTRADICTED", "PARTIALLY_CONTRADICTED"}, f"{cid}: blocking claim is contradicted")


def validate_creative_experiment(obj: dict[str, Any], *, causal_claim: bool = False) -> None:
    _require(obj.get("schema_version") == "creative-experiment-v1", "invalid creative experiment schema")
    mode = obj.get("mode")
    _require(mode in RANDOMIZED_MODES | {"OBSERVATIONAL"}, "invalid experiment mode")
    _require(bool(str(obj.get("hypothesis") or "").strip()), "hypothesis required")
    _require(bool(str(obj.get("primary_metric") or "").strip()), "primary_metric required")
    _require(isinstance(obj.get("guardrails"), list) and len(obj["guardrails"]) >= 1, "at least one guardrail required")
    decision = obj.get("decision_status")
    if mode in RANDOMIZED_MODES:
        _require(bool(str(obj.get("unit_of_randomization") or "").strip()), "randomized experiment needs unit_of_randomization")
        _require(bool(str(obj.get("attribution_window") or "").strip()), "randomized experiment needs attribution_window")
        _require(obj.get("srm_check") in {"PASS", "FAIL", "NOT_YET_CHECKED"}, "randomized experiment needs SRM status")
        _require(obj.get("early_stopping_method") in {"NONE_FIXED_HORIZON", "PREDECLARED_SEQUENTIAL", "ALWAYS_VALID"}, "invalid early stopping method")
    else:
        _require(decision != CAUSAL_WINNER, "observational comparison cannot be causal winner")
        _require(obj.get("srm_check") in {None, "NOT_APPLICABLE"}, "observational result cannot claim SRM randomization check")
    if obj.get("srm_check") == "FAIL":
        _require(decision != CAUSAL_WINNER, "unresolved SRM invalidates causal winner")
    if causal_claim:
        _require(mode in RANDOMIZED_MODES, "causal claim requires randomized/holdout/sequential experiment mode")
        _require(obj.get("srm_check") == "PASS", "causal claim requires SRM pass")
        _require(decision == CAUSAL_WINNER, "causal claim requires WINNER_CAUSAL decision")


def load_json(path: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    _require(isinstance(value, dict), "top-level JSON object required")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--untrusted-envelope")
    group.add_argument("--claim-ledger")
    group.add_argument("--creative-experiment")
    parser.add_argument("--publish-handoff", action="store_true")
    parser.add_argument("--causal-claim", action="store_true")
    args = parser.parse_args()

    if args.untrusted_envelope:
        validate_untrusted_content_envelope(load_json(args.untrusted_envelope))
        kind = "untrusted_content_envelope"
    elif args.claim_ledger:
        validate_claim_ledger(load_json(args.claim_ledger), for_publish_handoff=args.publish_handoff)
        kind = "claim_evidence_ledger"
    else:
        validate_creative_experiment(load_json(args.creative_experiment), causal_claim=args.causal_claim)
        kind = "creative_experiment"
    print(json.dumps({"status": "PASS", "artifact": kind}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
