#!/usr/bin/env python3
"""Evidence-weighted product opportunity portfolio for the AI Army.

Monetization is treated as a consequence of durable user value. This planner
ranks what to improve next using user pain, evidence, expected quality/reliability
impact, strategic reuse, confidence, effort and downside risk. Raw engagement
alone is never a positive objective. Every selected experiment receives a
success metric, a kill metric and a rollback condition.

The planner is deterministic and side-effect free: no model/provider calls,
payments, publishing, deploys or repository writes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "value-opportunity-portfolio-v1"
MAX_OPPORTUNITIES = 100
MAX_SELECTED = 12


class OpportunityError(ValueError):
    pass


def _num(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return default
    return number


def _clamp(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _num(value, default)))


def _normalize(raw: Mapping[str, Any]) -> dict[str, Any]:
    opportunity_id = str(raw.get("opportunity_id") or raw.get("id") or "")[:96]
    title = str(raw.get("title") or "")[:180]
    if not opportunity_id or not title:
        raise OpportunityError("opportunity requires id and title")
    category = str(raw.get("category") or "QUALITY").upper()
    if category not in {"QUALITY", "RELIABILITY", "EFFICIENCY", "DISCOVERY"}:
        raise OpportunityError("unsupported opportunity category")
    evidence = _clamp(raw.get("evidence_strength"))
    effort = max(0.05, _clamp(raw.get("effort"), 0.5))
    risk = _clamp(raw.get("risk"), 0.5)
    reversibility = _clamp(raw.get("reversibility"), 0.5)
    row = {
        "opportunity_id": opportunity_id,
        "title": title,
        "category": category,
        "user_pain": _clamp(raw.get("user_pain")),
        "evidence_strength": evidence,
        "expected_quality_gain": _clamp(raw.get("expected_quality_gain")),
        "expected_reliability_gain": _clamp(raw.get("expected_reliability_gain")),
        "expected_repeat_value_gain": _clamp(raw.get("expected_repeat_value_gain")),
        "strategic_reuse": _clamp(raw.get("strategic_reuse")),
        "monetization_fit": _clamp(raw.get("monetization_fit")),
        "confidence": _clamp(raw.get("confidence"), evidence),
        "effort": effort,
        "risk": risk,
        "reversibility": reversibility,
        "dependency_ready": raw.get("dependency_ready") is not False,
        "safety_blocked": raw.get("safety_blocked") is True,
        "success_metric": str(raw.get("success_metric") or "validated_task_success_rate")[:180],
        "kill_metric": str(raw.get("kill_metric") or "defect_or_user_correction_rate_increases")[:180],
        "notes": str(raw.get("notes") or "")[:500],
    }
    row["fingerprint"] = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()[:24]
    return row


def opportunity_score(raw: Mapping[str, Any]) -> float:
    row = _normalize(raw)
    if row["safety_blocked"] or not row["dependency_ready"]:
        return -1.0
    # Monetization fit is deliberately a small component. User pain, quality,
    # reliability and evidence dominate so the portfolio cannot optimize shallow
    # revenue proxies at the expense of product quality.
    durable_value = (
        0.25 * row["user_pain"]
        + 0.22 * row["expected_quality_gain"]
        + 0.17 * row["expected_reliability_gain"]
        + 0.12 * row["expected_repeat_value_gain"]
        + 0.12 * row["strategic_reuse"]
        + 0.06 * row["monetization_fit"]
        + 0.06 * row["reversibility"]
    )
    evidence_multiplier = 0.35 + 0.65 * row["evidence_strength"]
    confidence_multiplier = 0.50 + 0.50 * row["confidence"]
    execution_penalty = 0.20 * row["effort"] + 0.22 * row["risk"]
    score = durable_value * evidence_multiplier * confidence_multiplier - execution_penalty
    return round(max(0.0, min(1.0, score)), 6)


def _experiment_tier(row: Mapping[str, Any], score: float) -> str:
    if score >= 0.55 and row["evidence_strength"] >= 0.70 and row["risk"] <= 0.45:
        return "BUILD_NEXT"
    if score >= 0.32:
        return "BOUNDED_EXPERIMENT"
    return "DISCOVERY_ONLY"


def build_opportunity_portfolio(
    opportunities: Sequence[Mapping[str, Any]],
    *,
    max_selected: int = 8,
) -> dict[str, Any]:
    if len(opportunities) > MAX_OPPORTUNITIES:
        raise OpportunityError("too many opportunities")
    ranked: list[tuple[float, str, dict[str, Any]]] = []
    blocked: list[dict[str, Any]] = []
    for raw in opportunities:
        try:
            row = _normalize(raw)
        except Exception:
            continue
        score = opportunity_score(row)
        if score < 0:
            blocked.append({
                "opportunity_id": row["opportunity_id"],
                "reason": "SAFETY_BLOCKED" if row["safety_blocked"] else "DEPENDENCY_NOT_READY",
            })
            continue
        ranked.append((score, row["opportunity_id"], row))
    ranked.sort(key=lambda item: (-item[0], item[1]))

    limit = max(1, min(MAX_SELECTED, int(max_selected)))
    selected: list[dict[str, Any]] = []
    discovery_count = 0
    for score, _, row in ranked:
        if len(selected) >= limit:
            break
        tier = _experiment_tier(row, score)
        # Avoid letting uncertain discovery experiments consume the entire
        # portfolio. At most 20% (minimum one) of selected items may be discovery.
        if tier == "DISCOVERY_ONLY" and discovery_count >= max(1, limit // 5):
            continue
        if tier == "DISCOVERY_ONLY":
            discovery_count += 1
        selected.append({
            **row,
            "score": score,
            "tier": tier,
            "success_gate": {
                "metric": row["success_metric"],
                "requires_measured_improvement": True,
                "minimum_evidence_strength_before_production": 0.70,
            },
            "kill_gate": {
                "metric": row["kill_metric"],
                "rollback_on_safety_regression": True,
                "rollback_on_material_defect_increase": True,
            },
            "production_change_automatic": False,
        })

    portfolio = {
        "schema_version": SCHEMA_VERSION,
        "objective": "MAXIMIZE_DURABLE_VALIDATED_USER_VALUE",
        "selected": selected,
        "blocked": blocked,
        "candidate_count": len(ranked),
        "selected_count": len(selected),
        "raw_engagement_is_north_star": False,
        "monetization_is_quality_consequence": True,
        "small_reversible_experiments_first": True,
        "automatic_production_change": False,
        "generic_paid_fallback": False,
        "auto_top_up": False,
    }
    portfolio["portfolio_hash"] = hashlib.sha256(json.dumps(portfolio, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
    return portfolio


__all__ = [
    "OpportunityError",
    "build_opportunity_portfolio",
    "opportunity_score",
]
