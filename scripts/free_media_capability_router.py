#!/usr/bin/env python3
"""Capability-first FREE-ONLY router for AI Army media work.

A candidate may own more than one media work type when its verified capabilities
fit. Reusing a strong candidate is mildly rewarded to reduce handoff/context
cost, while fact-check and final-quality work prefer an independent provider or
model family when an equally capable free route is available.

This module never calls providers, spends money, publishes, deploys, mutates
secrets or grants repository write access. It only plans assignments from fresh
controller-provided route evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "free_media_capability_pool.json"
SCHEMA_VERSION = "free-media-capability-plan-v1"
INDEPENDENT_REVIEW_TYPES = frozenset({"FACT_CHECK", "QUALITY_REVIEW"})


class FreeMediaCapabilityError(ValueError):
    pass


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "free-media-capability-pool-v1":
        raise FreeMediaCapabilityError("invalid free media capability config")
    policy = value.get("policy") if isinstance(value.get("policy"), Mapping) else {}
    if policy.get("free_only") is not True:
        raise FreeMediaCapabilityError("free-only must remain enabled")
    for key in ("generic_paid_fallback", "auto_top_up", "unverified_free_route_can_execute", "license_unknown_can_publish", "external_model_repository_write"):
        if policy.get(key) is not False:
            raise FreeMediaCapabilityError(f"unsafe policy: {key}")
    return value


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _candidate_ready(candidate: Mapping[str, Any], evidence: Mapping[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    cost = str(candidate.get("cost_class") or "").upper()
    if cost not in {"FREE", "FREE_RUNTIME_ONLY", "FREE_ENDPOINT_ONLY", "FREE_QUOTA_ONLY"}:
        failures.append("non_free_cost_class")
    if evidence.get("available") is not True:
        failures.append("available")
    if evidence.get("free_verified") is not True:
        failures.append("free_verified")
    surface = str(candidate.get("execution_surface") or "")
    if "RUNTIME" in surface or "LOCAL" in surface or "CPU" in surface:
        if evidence.get("runtime_present") is not True:
            failures.append("runtime_present")
    else:
        if evidence.get("quota_safe") is not True:
            failures.append("quota_safe")
    if candidate.get("commercial_license_verification_required") is True and evidence.get("commercial_license_verified") is not True:
        failures.append("commercial_license_verified")
    if str(candidate.get("license_gate") or "") == "SERVICE_TERMS" and evidence.get("service_terms_ok") is not True:
        failures.append("service_terms_ok")
    return not failures, failures


def _coverage(required: Sequence[str], candidate: Mapping[str, Any]) -> float:
    need = {str(item) for item in required if str(item)}
    if not need:
        return 1.0
    have = {str(item) for item in candidate.get("capabilities", []) if str(item)}
    return len(need & have) / len(need)


def _same_family(left: Mapping[str, Any] | None, right: Mapping[str, Any]) -> bool:
    if not left:
        return False
    return str(left.get("model_family") or left.get("model") or "").lower() == str(right.get("model_family") or right.get("model") or "").lower()


def _score(
    *,
    work_type: str,
    candidate_id: str,
    candidate: Mapping[str, Any],
    required: Sequence[str],
    prior_assignments: Mapping[str, Mapping[str, Any]],
) -> float:
    coverage = _coverage(required, candidate)
    if coverage < 1.0:
        return -1.0
    score = 0.70
    # Reuse a proven multi-role worker when quality is equivalent: fewer handoffs,
    # smaller context duplication and simpler failure recovery.
    if candidate.get("can_multi_role") is True and any(row.get("candidate_id") == candidate_id for row in prior_assignments.values()):
        score += 0.08
    # Local deterministic and open-model runtimes are slightly preferred over
    # hosted quota routes when the capability fit is identical.
    cost_class = str(candidate.get("cost_class") or "")
    if cost_class == "FREE":
        score += 0.12
    elif cost_class == "FREE_RUNTIME_ONLY":
        score += 0.09
    elif cost_class == "FREE_ENDPOINT_ONLY":
        score += 0.06
    elif cost_class == "FREE_QUOTA_ONLY":
        score += 0.04

    if work_type in INDEPENDENT_REVIEW_TYPES:
        producer = prior_assignments.get("SCRIPT_DRAFT") or prior_assignments.get("NEWS_RESEARCH")
        if producer:
            if str(producer.get("provider") or "") == str(candidate.get("provider") or ""):
                score -= 0.14
            if _same_family(producer, candidate):
                score -= 0.16
    return round(score, 6)


def build_capability_plan(
    evidence: Mapping[str, Any],
    *,
    work_types: Sequence[str] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = dict(config or load_config())
    candidates = _mapping(cfg.get("candidates"))
    definitions = _mapping(cfg.get("work_types"))
    requested = list(work_types or cfg.get("preferred_pipeline") or ())
    assignments: dict[str, dict[str, Any]] = {}
    evaluations: dict[str, list[dict[str, Any]]] = {}

    for work_type in requested:
        work = str(work_type).upper()
        required = [str(item) for item in definitions.get(work, [])]
        ranked: list[tuple[float, str, Mapping[str, Any]]] = []
        attempts: list[dict[str, Any]] = []
        for candidate_id, raw in candidates.items():
            if not isinstance(raw, Mapping) or work not in {str(item) for item in raw.get("work_types", [])}:
                continue
            observed = _mapping(evidence.get(candidate_id))
            ready, failures = _candidate_ready(raw, observed)
            score = _score(
                work_type=work,
                candidate_id=str(candidate_id),
                candidate=raw,
                required=required,
                prior_assignments=assignments,
            ) if ready else -1.0
            attempt = {
                "candidate_id": str(candidate_id),
                "provider": str(raw.get("provider") or ""),
                "model": str(raw.get("model") or raw.get("model_family") or ""),
                "ready": ready,
                "coverage": round(_coverage(required, raw), 6),
                "score": score,
                "failures": failures,
            }
            attempts.append(attempt)
            if ready and score >= 0:
                ranked.append((score, str(candidate_id), raw))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        evaluations[work] = sorted(attempts, key=lambda row: (-float(row["score"]), row["candidate_id"]))
        if not ranked:
            assignments[work] = {
                "status": "BLOCKED",
                "candidate_id": None,
                "reason": "NO_VERIFIED_FREE_CAPABILITY_ROUTE",
            }
            continue
        score, candidate_id, row = ranked[0]
        assignments[work] = {
            "status": "ASSIGNED",
            "candidate_id": candidate_id,
            "provider": str(row.get("provider") or ""),
            "model": str(row.get("model") or row.get("model_family") or ""),
            "model_family": str(row.get("model_family") or row.get("model") or ""),
            "score": score,
            "multi_role": bool(row.get("can_multi_role") is True),
            "cost_class": str(row.get("cost_class") or ""),
        }

    blocked = [name for name, row in assignments.items() if row.get("status") != "ASSIGNED"]
    providers_used = sorted({str(row.get("provider")) for row in assignments.values() if row.get("provider")})
    candidate_counts: dict[str, int] = {}
    for row in assignments.values():
        cid = row.get("candidate_id")
        if cid:
            candidate_counts[str(cid)] = candidate_counts.get(str(cid), 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "READY" if not blocked else "BLOCKED_FREE_ROUTE_UNAVAILABLE",
        "assignments": assignments,
        "evaluations": evaluations,
        "multi_role_reuse": {key: count for key, count in sorted(candidate_counts.items()) if count > 1},
        "providers_used": providers_used,
        "blocked_work_types": blocked,
        "hard_boundaries": {
            "free_only": True,
            "generic_paid_fallback": False,
            "auto_top_up": False,
            "paid_media_tools_disabled": list(_mapping(cfg.get("policy")).get("paid_media_tools_disabled", [])),
            "unverified_route_can_execute": False,
            "license_unknown_can_publish": False,
            "external_model_repository_write": False,
        },
    }


__all__ = ["FreeMediaCapabilityError", "build_capability_plan", "load_config"]
