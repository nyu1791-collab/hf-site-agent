#!/usr/bin/env python3
"""Value-oriented routing and learning controls for AI Army V4.

This module turns the business goal "make better things" into deterministic
routing constraints. It does not call providers, spend money, publish, deploy,
write repositories, read secrets, or promote a production model. It only scores
already-admitted candidates and emits bounded routing/escalation/council plans.

The key invariant is that cost can optimize among safe, validated options but
can never replace evidence, validation, or hard-boundary approval.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.replaceable_agent_organization import normalize_candidate


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "value_optimized_ai_army.json"
SCHEMA_VERSION = "value-task-routing-v1"


class ValueRoutingError(ValueError):
    pass


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _num(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    value = float(value)
    if value != value or value in {float("inf"), float("-inf")}:
        return default
    return value


def _clamp(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _num(value, default)))


def load_value_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "value-optimized-ai-army-v1":
        raise ValueRoutingError("invalid value optimized AI Army config")
    hard = _mapping(payload.get("hard_boundaries"))
    governor = _mapping(payload.get("cost_governor"))
    if hard.get("external_model_repository_write") is not False:
        raise ValueRoutingError("external repository writes must stay disabled")
    if hard.get("generic_paid_fallback") is not False or governor.get("generic_paid_fallback") is not False:
        raise ValueRoutingError("generic paid fallback must stay disabled")
    if hard.get("auto_top_up") is not False or governor.get("auto_top_up") is not False:
        raise ValueRoutingError("auto top up must stay disabled")
    return payload


def _task_mapping(task: Any) -> Mapping[str, Any]:
    if isinstance(task, Mapping):
        return task
    if is_dataclass(task):
        return asdict(task)
    return {
        key: getattr(task, key)
        for key in ("task_id", "slot", "objective", "risk_level", "deterministic_validator_available", "metadata")
        if hasattr(task, key)
    }


def build_task_profile(task: Any, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_value_config()
    row = _task_mapping(task)
    metadata = _mapping(row.get("metadata"))
    objective = str(row.get("objective") or "")
    objective_lower = objective.lower()
    slot = str(row.get("slot") or "").upper()
    risk = str(row.get("risk_level") or "MEDIUM").upper()

    def hinted(name: str, default: float = 0.0) -> float:
        explicit = metadata.get(name)
        if isinstance(explicit, (int, float)) and not isinstance(explicit, bool):
            return _clamp(explicit)
        return default

    coding_default = 0.90 if slot in {"ENGINEERING_AGENT", "CODE_EXECUTOR"} else 0.20
    review_default = 0.90 if slot in {"QA_VALIDATOR", "RESULT_SYNTHESIZER"} else 0.20
    research_default = 0.85 if slot == "CONTEXT_LIBRARIAN" else 0.20
    latency_default = 0.90 if slot == "FAST_OPERATOR" else 0.35
    reasoning_default = 0.85 if slot in {"OPERATIONS_LEAD", "ENGINEERING_AGENT", "RESULT_SYNTHESIZER"} else 0.45

    context_chars = max(0, int(metadata.get("context_chars") or metadata.get("estimated_context_chars") or len(objective)))
    profile_cfg = _mapping(config.get("task_profile"))
    large_threshold = max(1, int(profile_cfg.get("large_context_chars") or 120000))
    very_large_threshold = max(large_threshold, int(profile_cfg.get("very_large_context_chars") or 400000))
    long_context = 1.0 if context_chars >= very_large_threshold else (0.75 if context_chars >= large_threshold else hinted("long_context", 0.0))

    multimodal_tokens = ("image", "video", "audio", "pdf", "multimodal", "画像", "動画", "音声")
    multimodal_default = 0.80 if any(token in objective_lower for token in multimodal_tokens) else 0.0
    complexity_default = {
        "LOW": 0.30,
        "MEDIUM": 0.55,
        "HIGH": 0.80,
        "CRITICAL": 0.95,
    }.get(risk, 0.55)
    user_impact_default = {
        "LOW": 0.30,
        "MEDIUM": 0.55,
        "HIGH": 0.80,
        "CRITICAL": 0.95,
    }.get(risk, 0.55)

    profile = {
        "task_id": str(row.get("task_id") or "")[:128],
        "slot": slot,
        "risk": risk,
        "coding": hinted("coding", coding_default),
        "research": hinted("research", research_default),
        "reasoning": hinted("reasoning", reasoning_default),
        "review": hinted("review", review_default),
        "multimodal": hinted("multimodal", multimodal_default),
        "long_context": long_context,
        "structured_output": hinted("structured_output", 0.75),
        "latency_sensitivity": hinted("latency_sensitivity", latency_default),
        "user_impact": hinted("user_impact", user_impact_default),
        "complexity": hinted("complexity", complexity_default),
        "context_chars": context_chars,
        "very_large_context": context_chars >= very_large_threshold,
        "deterministic_validator_available": bool(row.get("deterministic_validator_available", True)),
        "paid_specialist_authorized": metadata.get("paid_specialist_authorized") is True,
        "paid_specialist_budget_usd": max(0.0, _num(metadata.get("paid_specialist_budget_usd"), 0.0)),
        "prior_failure_count": max(0, int(metadata.get("prior_failure_count") or 0)),
        "prior_same_error_count": max(0, int(metadata.get("prior_same_error_count") or 0)),
    }
    profile["task_profile_hash"] = task_profile_hash(profile)
    return profile


def task_profile_hash(profile: Mapping[str, Any]) -> str:
    stable = {
        key: profile.get(key)
        for key in (
            "slot", "risk", "coding", "research", "reasoning", "review", "multimodal",
            "long_context", "structured_output", "latency_sensitivity", "user_impact", "complexity",
            "very_large_context", "deterministic_validator_available",
        )
    }
    raw = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def model_family(model: str) -> str:
    text = str(model or "").lower()
    for family in ("deepseek", "gemini", "qwen", "nemotron", "glm", "llama", "mistral", "minimax"):
        if family in text:
            return family
    return text.split("/")[-1].split(":")[0].split("-")[0][:40] or "unknown"


def _declared_capabilities(candidate: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("roles", "capabilities", "capability_tags"):
        raw = candidate.get(key)
        if isinstance(raw, (list, tuple, set)):
            values.update(str(item).strip().lower() for item in raw if str(item).strip())
    role_scores = candidate.get("role_scores")
    if isinstance(role_scores, Mapping):
        values.update(str(key).strip().lower() for key in role_scores)
    return values


def _task_fit(candidate: Mapping[str, Any], profile: Mapping[str, Any]) -> float:
    caps = _declared_capabilities(candidate)
    provider = str(candidate.get("provider") or candidate.get("provider_id") or "").lower()
    family = model_family(str(candidate.get("model") or candidate.get("model_id") or ""))
    weighted = 0.0
    total = 0.0
    aliases = {
        "coding": {"coding", "code", "coding_worker", "simple_coding", "debugging"},
        "research": {"research", "general", "general_worker", "citation"},
        "reasoning": {"reasoning", "review", "review_worker", "general", "general_worker"},
        "review": {"review", "qa", "testing", "review_worker"},
        "multimodal": {"multimodal", "image", "video", "audio", "vision"},
        "long_context": {"long_context", "general", "general_worker", "summarization"},
        "structured_output": {"json", "structured_output", "fast_worker", "review_worker"},
    }
    for dimension, accepted in aliases.items():
        weight = _clamp(profile.get(dimension))
        if weight <= 0:
            continue
        total += weight
        hit = bool(caps & accepted)
        weighted += weight * (1.0 if hit else 0.25)
    fit = weighted / total if total else 0.5

    # Intent boosts are deliberately modest; measured outcomes still dominate.
    if provider == "google" or family == "gemini":
        fit += 0.10 * max(_clamp(profile.get("research")), _clamp(profile.get("long_context")), _clamp(profile.get("multimodal")))
    if provider == "deepseek" or family == "deepseek":
        fit += 0.10 * max(_clamp(profile.get("coding")), _clamp(profile.get("reasoning")), _clamp(profile.get("review")), _clamp(profile.get("complexity")))
    if provider == "nvidia":
        fit += 0.07 * max(_clamp(profile.get("review")), _clamp(profile.get("reasoning")))
    if "qwen" in family or provider == "groq":
        fit += 0.07 * max(_clamp(profile.get("coding")), _clamp(profile.get("latency_sensitivity")))
    return max(0.0, min(1.0, fit))


def candidate_value_score(
    candidate: Mapping[str, Any],
    profile: Mapping[str, Any],
    *,
    config: Mapping[str, Any] | None = None,
    producer_bindings: Sequence[Mapping[str, Any]] = (),
) -> float:
    config = config or load_value_config()
    row = normalize_candidate(candidate)
    provider = str(row.get("provider") or "")
    family = model_family(str(row.get("model") or ""))

    if row.get("paid") is True:
        allowlist = set(str(item).lower() for item in _mapping(config.get("cost_governor")).get("paid_specialist_allowlist", []))
        if provider not in allowlist:
            return -1.0
        if not profile.get("paid_specialist_authorized") or _num(profile.get("paid_specialist_budget_usd")) <= 0:
            return -1.0
    elif row.get("free_verified") is not True:
        return -1.0

    success = _clamp(row.get("success_rate"), 0.5)
    quality = _clamp(row.get("quality_score"), 0.5)
    fit = _task_fit(row, profile)
    samples = min(1.0, max(0, int(row.get("samples") or 0)) / 5.0)
    latency_ms = max(1.0, _num(row.get("average_latency_ms"), 60000.0))
    latency_score = 1.0 / (1.0 + latency_ms / 12000.0)
    latency_weight = 0.05 + 0.08 * _clamp(profile.get("latency_sensitivity"))
    cost_score = 1.0 if row.get("free_verified") is True else 0.35

    score = 0.30 * fit + 0.24 * quality + 0.20 * success + 0.08 * samples + latency_weight * latency_score + 0.10 * cost_score

    # Independent review should not merely echo the producer when another good
    # family/provider is available.
    if str(profile.get("slot")) in {"QA_VALIDATOR", "RESULT_SYNTHESIZER"} or _clamp(profile.get("review")) >= 0.70:
        for producer in producer_bindings:
            p_provider = str(producer.get("provider") or "").lower()
            p_family = model_family(str(producer.get("model") or ""))
            if provider == p_provider:
                score -= 0.09
            if family == p_family:
                score -= 0.13

    return round(max(0.0, min(1.0, score)), 8)


def strong_model_preferred(profile: Mapping[str, Any]) -> bool:
    return bool(
        _clamp(profile.get("complexity")) >= 0.85
        or (profile.get("very_large_context") is True)
        or (_clamp(profile.get("multimodal")) >= 0.75 and _clamp(profile.get("complexity")) >= 0.70)
        or (_clamp(profile.get("user_impact")) >= 0.90 and not profile.get("deterministic_validator_available"))
        or int(profile.get("prior_failure_count") or 0) >= 2
    )


def select_task_binding(
    task: Any,
    candidates: Sequence[Mapping[str, Any]],
    *,
    incumbent: Mapping[str, Any] | None = None,
    producer_bindings: Sequence[Mapping[str, Any]] = (),
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    config = config or load_value_config()
    profile = build_task_profile(task, config)
    ranked: list[tuple[float, str, str, dict[str, Any]]] = []
    for raw in candidates:
        try:
            row = normalize_candidate(raw)
        except Exception:
            continue
        score = candidate_value_score(row, profile, config=config, producer_bindings=producer_bindings)
        if score >= 0:
            ranked.append((score, str(row.get("provider") or ""), str(row.get("model") or ""), row))

    if incumbent:
        try:
            inc = normalize_candidate(incumbent)
            score = candidate_value_score(inc, profile, config=config, producer_bindings=producer_bindings)
            if score >= 0:
                ranked.append((score, str(inc.get("provider") or ""), str(inc.get("model") or ""), inc))
        except Exception:
            pass

    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    selected = ranked[0][3] if ranked else None
    return {
        "schema_version": SCHEMA_VERSION,
        "task_profile": profile,
        "strong_model_preferred": strong_model_preferred(profile),
        "selected": {
            "provider": selected.get("provider"),
            "model": selected.get("model"),
            "score": ranked[0][0],
            "model_family": model_family(str(selected.get("model") or "")),
        } if selected else None,
        "eligible_candidate_count": len(ranked),
        "top_candidates": [
            {"provider": provider, "model": model, "score": score, "model_family": model_family(model)}
            for score, provider, model, _ in ranked[:5]
        ],
        "generic_paid_fallback": False,
        "auto_top_up": False,
        "external_model_repository_write": False,
    }


def escalation_plan(profile: Mapping[str, Any], result_history: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    failures = [row for row in result_history if str(row.get("status") or "").upper() not in {"COMPLETED", "PASS"}]
    low_confidence = any(_clamp(row.get("effective_confidence"), 1.0) < 0.72 for row in result_history)
    low_quality = any(_clamp(row.get("quality_score"), 1.0) < 0.78 for row in result_history)
    errors = [str(row.get("error_class") or "") for row in result_history if row.get("error_class")]
    repeated_same_error = any(errors.count(error) >= 2 for error in set(errors))
    disagreement = len({str(row.get("conclusion") or "") for row in result_history if row.get("conclusion")}) > 1
    requires = bool(strong_model_preferred(profile) or len(failures) >= 2 or low_confidence or low_quality or repeated_same_error or disagreement)
    reasons = []
    if strong_model_preferred(profile): reasons.append("task_profile_requires_stronger_reasoning")
    if len(failures) >= 2: reasons.append("two_or_more_failed_attempts")
    if low_confidence: reasons.append("confidence_below_floor")
    if low_quality: reasons.append("quality_below_floor")
    if repeated_same_error: reasons.append("repeated_same_error")
    if disagreement: reasons.append("independent_views_disagree")
    return {
        "escalation_required": requires,
        "reasons": reasons,
        "preferred_specialist_order": ["DEEPSEEK_V4_1_FLASH", "GPT_5_6_SOL"],
        "paid_execution_automatic": False,
        "human_payment_approval_preserved": True,
    }


def council_plan(profile: Mapping[str, Any], views: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    conclusions = {str(row.get("conclusion") or "") for row in views if str(row.get("conclusion") or "")}
    disagreement = len(conclusions) > 1
    high_impact_without_validator = _clamp(profile.get("user_impact")) >= 0.85 and not profile.get("deterministic_validator_available")
    required = bool(disagreement or high_impact_without_validator or str(profile.get("risk")) in {"HIGH", "CRITICAL"})
    return {
        "required": required,
        "reason": (
            "disagreement" if disagreement else
            "high_impact_without_deterministic_validator" if high_impact_without_validator else
            "high_risk" if str(profile.get("risk")) in {"HIGH", "CRITICAL"} else
            "not_required"
        ),
        "decision_rule": "EVIDENCE_WEIGHTED_NOT_MAJORITY_VOTE",
        "deterministic_evidence_breaks_ties": True,
        "commander_arbitrates_unresolved_disagreement": True,
    }


def outcome_record(
    *,
    profile: Mapping[str, Any],
    binding: Mapping[str, Any],
    result: Mapping[str, Any],
    estimated_cost_usd: float = 0.0,
    latency_ms: float = 0.0,
) -> dict[str, Any]:
    status = str(result.get("status") or "FAILED").upper()
    validation = str(result.get("validation_status") or "UNKNOWN").upper()
    return {
        "contract_version": "value-outcome-memory-v1",
        "provider": str(binding.get("provider") or ""),
        "model": str(binding.get("model") or ""),
        "model_family": model_family(str(binding.get("model") or "")),
        "task_profile_hash": str(profile.get("task_profile_hash") or ""),
        "validated_success": status == "COMPLETED" and validation in {"PASS", "VALIDATED", "UNKNOWN"},
        "quality_score": _clamp(result.get("quality_score")),
        "effective_confidence": _clamp(result.get("effective_confidence")),
        "rework_count": max(0, int(result.get("revisions") or result.get("revision_count") or 0)),
        "latency_ms": max(0.0, _num(latency_ms)),
        "estimated_cost_usd": max(0.0, _num(estimated_cost_usd)),
        "validation_status": validation,
        "error_class": str(result.get("error_class") or "")[:120] or None,
        "secret_content_persisted": False,
    }


def aggregate_outcomes(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [row for row in records if isinstance(row, Mapping)]
    if not rows:
        return {"samples": 0, "validated_success_rate": 0.0, "quality_score": 0.0, "rework_rate": 0.0, "cost_per_validated_success": None}
    successes = sum(1 for row in rows if row.get("validated_success") is True)
    quality = sum(_clamp(row.get("quality_score")) for row in rows) / len(rows)
    reworked = sum(1 for row in rows if int(row.get("rework_count") or 0) > 0)
    total_cost = sum(max(0.0, _num(row.get("estimated_cost_usd"))) for row in rows)
    return {
        "samples": len(rows),
        "validated_success_rate": round(successes / len(rows), 6),
        "quality_score": round(quality, 6),
        "rework_rate": round(reworked / len(rows), 6),
        "cost_per_validated_success": round(total_cost / successes, 8) if successes else None,
    }


def champion_challenger_decision(champion: Mapping[str, Any], challenger: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_value_config()
    policy = _mapping(config.get("champion_challenger"))
    minimum_samples = max(1, int(policy.get("minimum_challenger_samples") or 3))
    quality_margin = max(0.0, _num(policy.get("minimum_quality_margin"), 0.03))
    if int(challenger.get("samples") or 0) < minimum_samples:
        return {"decision": "SHADOW", "reason": "insufficient_challenger_samples", "automatic_production_promotion": False}
    champion_success = _clamp(champion.get("validated_success_rate"))
    challenger_success = _clamp(challenger.get("validated_success_rate"))
    champion_quality = _clamp(champion.get("quality_score"))
    challenger_quality = _clamp(challenger.get("quality_score"))
    wins = challenger_success >= champion_success and challenger_quality >= champion_quality + quality_margin
    return {
        "decision": "PROMOTION_CANDIDATE" if wins else "KEEP_CHAMPION",
        "reason": "validated_quality_gain" if wins else "challenger_not_materially_better",
        "automatic_production_promotion": False,
        "requires_commander_and_human_approval": wins,
    }


def value_score(metrics: Mapping[str, Any], config: Mapping[str, Any] | None = None) -> float:
    config = config or load_value_config()
    weights = _mapping(config.get("value_metrics"))
    validated = _clamp(metrics.get("validated_success_rate"))
    quality = _clamp(metrics.get("quality_score"))
    user_value = _clamp(metrics.get("user_value_score"), validated)
    reliability = _clamp(metrics.get("reliability_score"), validated)
    rework = _clamp(metrics.get("rework_rate"))
    latency = _clamp(metrics.get("latency_score"), 0.5)
    cost_eff = _clamp(metrics.get("cost_efficiency_score"), 0.5)
    reuse = _clamp(metrics.get("reusability_score"), 0.0)
    score = (
        _num(weights.get("validated_success_weight"), 0.28) * validated
        + _num(weights.get("quality_weight"), 0.22) * quality
        + _num(weights.get("user_value_weight"), 0.18) * user_value
        + _num(weights.get("reliability_weight"), 0.12) * reliability
        - _num(weights.get("rework_penalty_weight"), 0.08) * rework
        + _num(weights.get("latency_weight"), 0.05) * latency
        + _num(weights.get("cost_efficiency_weight"), 0.05) * cost_eff
        + _num(weights.get("reusability_weight"), 0.02) * reuse
    )
    return round(max(0.0, min(1.0, score)), 6)


__all__ = [
    "ValueRoutingError",
    "aggregate_outcomes",
    "build_task_profile",
    "candidate_value_score",
    "champion_challenger_decision",
    "council_plan",
    "escalation_plan",
    "load_value_config",
    "model_family",
    "outcome_record",
    "select_task_binding",
    "strong_model_preferred",
    "task_profile_hash",
    "value_score",
]
