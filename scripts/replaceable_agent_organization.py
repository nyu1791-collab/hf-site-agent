#!/usr/bin/env python3
"""Hot-swappable, capability-first organization routing for the AI Army.

Roles are stable; model bindings are not.  This module scores current execution
signals, assigns models/providers to agent slots, and decides when an incumbent
should be kept, shadowed, or replaced.  It also centralizes risk-adaptive
review policy so low-risk work is not forced through commander review when a
machine validator can settle it.

The remaining hard boundaries are intentionally small: no secret exposure,
no generic paid fallback, no external-model repository writes, no stale-result
overwrite, and explicit human approval for merge/deploy/publish/secret/payment
boundaries.  Everything else is a routing/quality decision, not a safety stop.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "replaceable_agent_organization.json"
SCHEMA_VERSION = "replaceable-agent-organization-report-v1"

SLOT_PRIORITY = (
    "OPERATIONS_LEAD",
    "ENGINEERING_AGENT",
    "CODE_EXECUTOR",
    "QA_VALIDATOR",
    "FAST_OPERATOR",
    "CONTEXT_LIBRARIAN",
    "RESULT_SYNTHESIZER",
)

ROLE_ALIASES: Mapping[str, tuple[str, ...]] = {
    "planning": ("GENERAL_WORKER", "REVIEW_WORKER", "GENERAL", "PLANNING"),
    "reasoning": ("REVIEW_WORKER", "GENERAL_WORKER", "GENERAL", "REASONING"),
    "agent": ("GENERAL_WORKER", "CODING_WORKER", "GENERAL", "AGENT"),
    "coding": ("CODING_WORKER", "CODING", "CODE", "SIMPLE_CODING"),
    "debugging": ("CODING_WORKER", "REVIEW_WORKER", "CODING", "DEBUGGING"),
    "review": ("REVIEW_WORKER", "GENERAL_WORKER", "REVIEW", "QA"),
    "testing": ("REVIEW_WORKER", "CODING_WORKER", "TEST", "QA"),
    "fast": ("FAST_WORKER", "FAST", "JSON"),
    "json": ("FAST_WORKER", "REVIEW_WORKER", "JSON", "STRUCTURED_OUTPUT"),
    "long_context": ("GENERAL_WORKER", "REVIEW_WORKER", "LONG_CONTEXT", "GENERAL"),
    "general": ("GENERAL_WORKER", "GENERAL"),
    "summarization": ("GENERAL_WORKER", "FAST_WORKER", "SUMMARY", "GENERAL"),
}

HARD_BOUNDARY_ACTIONS = frozenset({
    "merge",
    "deploy",
    "publish",
    "secret_mutation",
    "payment",
    "new_paid_provider",
})


class OrganizationError(ValueError):
    pass


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return default
    return number


def _clamp(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _number(value, default)))


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "replaceable-agent-organization-v1":
        raise OrganizationError("invalid replaceable-agent organization config")
    hard = _mapping(payload.get("hard_boundaries"))
    if hard.get("generic_paid_fallback") is not False or hard.get("auto_top_up") is not False:
        raise OrganizationError("generic paid fallback and auto top-up must stay disabled")
    if hard.get("external_model_repository_write") is not False:
        raise OrganizationError("external model repository writes must stay disabled")
    slots = _mapping(payload.get("slots"))
    if not slots or not set(SLOT_PRIORITY).issubset(slots):
        raise OrganizationError("required replaceable role slots are missing")
    return payload


def _candidate_roles(candidate: Mapping[str, Any]) -> set[str]:
    values: set[str] = set()
    for key in ("roles", "capability_tags", "capabilities"):
        raw = candidate.get(key)
        if isinstance(raw, (list, tuple, set)):
            values.update(str(item).strip().upper() for item in raw if str(item).strip())
    role_scores = candidate.get("role_scores")
    if isinstance(role_scores, Mapping):
        values.update(str(key).strip().upper() for key in role_scores if str(key).strip())
    return values


def _role_score(candidate: Mapping[str, Any], slot: Mapping[str, Any]) -> float:
    role_scores = candidate.get("role_scores") if isinstance(candidate.get("role_scores"), Mapping) else {}
    declared = _candidate_roles(candidate)
    requested = [str(item).strip().upper() for item in slot.get("preferred_role_scores", []) if str(item).strip()]
    numeric = [_clamp(role_scores.get(role)) for role in requested if role in role_scores]
    direct = max(numeric, default=0.0)

    required = [str(item).strip().lower() for item in slot.get("required_capabilities", []) if str(item).strip()]
    capability_hits = 0
    capability_total = 0
    for capability in required:
        aliases = ROLE_ALIASES.get(capability, (capability.upper(),))
        capability_total += 1
        alias_scores = [_clamp(role_scores.get(alias)) for alias in aliases if alias in role_scores]
        if alias_scores:
            direct = max(direct, max(alias_scores))
        if any(alias in declared for alias in aliases) or capability.upper() in declared:
            capability_hits += 1
    coverage = capability_hits / max(1, capability_total)
    return max(direct, coverage * 0.90)


def normalize_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    provider = str(candidate.get("provider") or candidate.get("provider_id") or "").strip().lower()
    model = str(candidate.get("model") or candidate.get("model_id") or "").strip()
    if not provider or not model:
        raise OrganizationError("candidate requires provider and model")

    samples = int(candidate.get("samples") or candidate.get("attempts") or candidate.get("task_count") or 0)
    successes = int(candidate.get("successes") or candidate.get("task_success_count") or 0)
    success_rate = candidate.get("success_rate")
    if not isinstance(success_rate, (int, float)) or isinstance(success_rate, bool):
        success_rate = successes / samples if samples > 0 else _clamp(candidate.get("availability"), 0.5)

    quality = max(
        _clamp(candidate.get("weighted_quality_score")),
        _clamp(candidate.get("best_score")),
        _clamp(candidate.get("quality_score")),
    )
    if quality == 0.0 and isinstance(candidate.get("task_scores"), Mapping):
        values = [_clamp(value) for value in candidate["task_scores"].values()]
        quality = sum(values) / len(values) if values else 0.0

    latency = _number(
        candidate.get("average_latency_ms"),
        _number(candidate.get("best_latency_ms"), _number(candidate.get("latency_ms"), 60_000.0)),
    )
    rate_limit_rate = _clamp(candidate.get("rate_limit_rate"))
    if rate_limit_rate == 0.0 and samples > 0:
        rate_limits = int(candidate.get("rate_limits") or candidate.get("rate_limited_calls") or 0)
        rate_limit_rate = max(0.0, min(1.0, rate_limits / samples))

    free_verified = (
        candidate.get("free_verified") is True
        or candidate.get("free_admitted") is True
        or candidate.get("zero_cost_verified") is True
        or model.endswith(":free")
    )
    paid = bool(candidate.get("paid") is True or candidate.get("is_paid") is True)
    if free_verified:
        paid = False

    result = dict(candidate)
    result.update({
        "provider": provider,
        "model": model,
        "samples": max(0, samples),
        "success_rate": round(_clamp(success_rate, 0.5), 6),
        "quality_score": round(_clamp(quality), 6),
        "average_latency_ms": max(1.0, latency),
        "rate_limit_rate": round(rate_limit_rate, 6),
        "free_verified": free_verified,
        "paid": paid,
    })
    return result


def candidate_score(candidate: Mapping[str, Any], slot: Mapping[str, Any]) -> float:
    row = normalize_candidate(candidate)
    paid_provider = str(slot.get("paid_specialist_provider") or "").strip().lower()
    if row["paid"]:
        if slot.get("paid_specialist_allowed") is not True or row["provider"] != paid_provider:
            return -1.0
    elif not row["free_verified"]:
        return -1.0

    role_fit = _role_score(row, slot)
    quality = _clamp(row.get("quality_score"))
    success = _clamp(row.get("success_rate"), 0.5)
    latency_factor = 1.0 / (1.0 + max(1.0, _number(row.get("average_latency_ms"), 60_000.0)) / 10_000.0)
    rate_limit = _clamp(row.get("rate_limit_rate"))
    sample_factor = min(1.0, max(0, int(row.get("samples", 0))) / 3.0)
    cost_factor = 1.0 if row["free_verified"] else 0.35

    score = (
        0.34 * role_fit
        + 0.22 * quality
        + 0.20 * success
        + 0.08 * latency_factor
        + 0.06 * sample_factor
        + 0.10 * cost_factor
        - 0.14 * rate_limit
    )
    return round(max(0.0, min(1.0, score)), 8)


def autonomy_policy(
    config: Mapping[str, Any],
    *,
    risk_level: str,
    deterministic_validator_available: bool = True,
    boundary_action: str | None = None,
) -> dict[str, Any]:
    risk = str(risk_level or "MEDIUM").strip().upper()
    if risk not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
        raise OrganizationError("unknown risk level")
    boundary = str(boundary_action or "").strip().lower()
    hard_boundary = boundary in HARD_BOUNDARY_ACTIONS
    adaptive = _mapping(config.get("adaptive_controls"))

    revisions = {
        "LOW": int(adaptive.get("low_risk_max_revisions") or 2),
        "MEDIUM": int(adaptive.get("medium_risk_max_revisions") or 2),
        "HIGH": int(adaptive.get("high_risk_max_revisions") or 3),
        "CRITICAL": int(adaptive.get("high_risk_max_revisions") or 3),
    }[risk]

    commander_review = risk in {"HIGH", "CRITICAL"}
    peer_review = risk == "MEDIUM" and not deterministic_validator_available
    human_approval = hard_boundary
    if risk == "LOW":
        mode = "AUTONOMOUS_VALIDATE_CONTINUE"
    elif risk == "MEDIUM":
        mode = "AUTONOMOUS_WITH_PEER_REVIEW" if peer_review else "AUTONOMOUS_VALIDATE_CONTINUE"
    elif risk == "HIGH":
        mode = "COMMANDER_REVIEW"
    else:
        mode = "HUMAN_BOUNDARY_APPROVAL" if human_approval else "COMMANDER_REVIEW"

    return {
        "risk_level": risk,
        "mode": mode,
        "max_revisions": max(0, min(5, revisions)),
        "deterministic_validator_available": bool(deterministic_validator_available),
        "peer_review_required": peer_review,
        "commander_review_required": commander_review,
        "human_approval_required": human_approval,
        "autonomous_handoff_allowed": not human_approval and not commander_review,
        "hard_boundary_action": boundary if hard_boundary else None,
        "external_model_repository_write": False,
    }


def incumbent_degraded(candidate: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    row = normalize_candidate(candidate)
    adaptive = _mapping(config.get("adaptive_controls"))
    failures = int(row.get("consecutive_failures") or row.get("recent_failures") or 0)
    return (
        failures >= int(adaptive.get("consecutive_failures_for_degraded_state") or 2)
        or _clamp(row.get("rate_limit_rate")) >= _clamp(adaptive.get("rate_limit_rate_for_degraded_state"), 0.5)
        or _clamp(row.get("health_score"), 1.0) < _clamp(adaptive.get("minimum_health_for_incumbent"), 0.55)
    )


def swap_decision(
    incumbent: Mapping[str, Any] | None,
    challenger: Mapping[str, Any],
    slot: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    challenger_row = normalize_candidate(challenger)
    challenger_score = candidate_score(challenger_row, slot)
    if challenger_score < 0:
        return {"decision": "REJECT_CHALLENGER", "reason": "ineligible_for_slot", "challenger_score": challenger_score}
    if not incumbent:
        return {"decision": "PROMOTE", "reason": "empty_slot", "challenger_score": challenger_score}

    incumbent_row = normalize_candidate(incumbent)
    if incumbent_row["provider"] == challenger_row["provider"] and incumbent_row["model"] == challenger_row["model"]:
        return {"decision": "KEEP", "reason": "same_binding", "incumbent_score": candidate_score(incumbent_row, slot), "challenger_score": challenger_score}

    incumbent_score = candidate_score(incumbent_row, slot)
    adaptive = _mapping(config.get("adaptive_controls"))
    degraded = incumbent_degraded(incumbent_row, config) or incumbent_score < 0
    minimum_samples = int(adaptive.get("minimum_samples_for_normal_promotion") or 2)
    samples = int(challenger_row.get("samples") or 0)

    if degraded:
        margin = float(adaptive.get("degraded_swap_margin") or -0.02)
        if samples >= 1 and challenger_score >= incumbent_score + margin:
            return {
                "decision": "REPLACE",
                "reason": "incumbent_degraded",
                "incumbent_score": incumbent_score,
                "challenger_score": challenger_score,
                "score_delta": round(challenger_score - incumbent_score, 8),
            }
        return {
            "decision": "KEEP",
            "reason": "degraded_but_no_better_verified_challenger",
            "incumbent_score": incumbent_score,
            "challenger_score": challenger_score,
        }

    margin = float(adaptive.get("normal_swap_margin") or 0.06)
    delta = challenger_score - incumbent_score
    if samples < minimum_samples:
        return {
            "decision": "SHADOW_CANARY" if delta > 0 else "KEEP",
            "reason": "challenger_needs_more_samples" if delta > 0 else "incumbent_still_better",
            "incumbent_score": incumbent_score,
            "challenger_score": challenger_score,
            "score_delta": round(delta, 8),
            "minimum_samples": minimum_samples,
        }
    if delta >= margin:
        return {
            "decision": "REPLACE",
            "reason": "verified_challenger_wins_by_margin",
            "incumbent_score": incumbent_score,
            "challenger_score": challenger_score,
            "score_delta": round(delta, 8),
        }
    return {
        "decision": "KEEP",
        "reason": "hysteresis_prevents_model_churn",
        "incumbent_score": incumbent_score,
        "challenger_score": challenger_score,
        "score_delta": round(delta, 8),
    }


def _best_candidate_for_slot(
    candidates: Sequence[Mapping[str, Any]],
    slot: Mapping[str, Any],
    *,
    uses: Mapping[tuple[str, str], int],
    max_slots_per_model: int,
    avoid_binding: tuple[str, str] | None = None,
) -> tuple[dict[str, Any] | None, float]:
    best: dict[str, Any] | None = None
    best_score = -1.0
    for raw in candidates:
        try:
            row = normalize_candidate(raw)
        except OrganizationError:
            continue
        binding = (row["provider"], row["model"])
        if uses.get(binding, 0) >= max_slots_per_model:
            continue
        score = candidate_score(row, slot)
        if score < 0:
            continue
        load_penalty = 0.045 * uses.get(binding, 0)
        if avoid_binding and binding == avoid_binding:
            load_penalty += 0.08
        adjusted = max(0.0, score - load_penalty)
        if adjusted > best_score + 1e-12 or (
            abs(adjusted - best_score) <= 1e-12 and best is not None and binding < (best["provider"], best["model"])
        ):
            best = row
            best_score = adjusted
    return best, round(best_score, 8)


def assign_agent_slots(
    candidates: Sequence[Mapping[str, Any]],
    *,
    config: Mapping[str, Any] | None = None,
    incumbents: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    config = dict(config or load_config())
    slots = _mapping(config.get("slots"))
    adaptive = _mapping(config.get("adaptive_controls"))
    incumbent_map = incumbents if isinstance(incumbents, Mapping) else {}
    max_slots_per_model = max(1, min(4, int(adaptive.get("max_slots_per_same_model") or 2)))
    uses: dict[tuple[str, str], int] = {}
    assignments: dict[str, dict[str, Any]] = {}
    swap_events: list[dict[str, Any]] = []

    code_binding: tuple[str, str] | None = None
    for slot_name in SLOT_PRIORITY:
        slot = _mapping(slots.get(slot_name))
        avoid = code_binding if slot_name == "QA_VALIDATOR" and slot.get("prefer_independent_model_from_code_executor") is True else None
        challenger, adjusted_score = _best_candidate_for_slot(
            candidates,
            slot,
            uses=uses,
            max_slots_per_model=max_slots_per_model,
            avoid_binding=avoid,
        )
        incumbent = incumbent_map.get(slot_name) if isinstance(incumbent_map.get(slot_name), Mapping) else None
        if challenger is None:
            if incumbent:
                chosen = normalize_candidate(incumbent)
                decision = {"decision": "KEEP", "reason": "no_eligible_challenger"}
            else:
                assignments[slot_name] = {"status": "UNFILLED", "reason": "no_eligible_candidate"}
                continue
        else:
            decision = swap_decision(incumbent, challenger, slot, config)
            if incumbent and decision["decision"] in {"KEEP", "SHADOW_CANARY"}:
                chosen = normalize_candidate(incumbent)
            else:
                chosen = challenger
        binding = (chosen["provider"], chosen["model"])
        uses[binding] = uses.get(binding, 0) + 1
        if slot_name == "CODE_EXECUTOR":
            code_binding = binding
        risk = str(slot.get("default_risk") or "MEDIUM")
        assignments[slot_name] = {
            "status": "ASSIGNED",
            "provider": chosen["provider"],
            "model": chosen["model"],
            "slot_score": candidate_score(chosen, slot),
            "adjusted_for_load_score": adjusted_score if challenger and chosen["model"] == challenger["model"] and chosen["provider"] == challenger["provider"] else candidate_score(chosen, slot),
            "mission": slot.get("mission"),
            "may_delegate": bool(slot.get("may_delegate")),
            "max_child_tasks": int(slot.get("max_child_tasks") or 0),
            "max_parallel_children": int(slot.get("max_parallel_children") or 0),
            "default_risk": risk,
            "autonomy": autonomy_policy(config, risk_level=risk, deterministic_validator_available=slot_name != "OPERATIONS_LEAD"),
            "swap_decision": decision,
        }
        swap_events.append({"slot": slot_name, **decision, "selected_provider": chosen["provider"], "selected_model": chosen["model"]})

    provider_parallelism = _mapping(adaptive.get("provider_parallelism"))
    provider_load: dict[str, int] = {}
    for row in assignments.values():
        if row.get("status") == "ASSIGNED":
            provider = str(row.get("provider") or "")
            provider_load[provider] = provider_load.get(provider, 0) + 1
    concurrency = {
        provider: min(count, max(1, int(provider_parallelism.get(provider) or 1)))
        for provider, count in sorted(provider_load.items())
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "organization_mode": "REPLACEABLE_ROLE_SLOTS",
        "assignments": assignments,
        "swap_events": swap_events,
        "provider_concurrency": concurrency,
        "organization_parallel_limit": max(1, int(adaptive.get("organization_parallel_limit") or 6)),
        "hard_boundaries": dict(_mapping(config.get("hard_boundaries"))),
        "generic_paid_fallback": False,
        "auto_top_up": False,
        "external_model_repository_write": False,
    }


def candidates_from_direct_free_report(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    provider_status = _mapping(report.get("provider_status"))
    rows: list[dict[str, Any]] = []
    for raw in report.get("rankings", []) if isinstance(report.get("rankings"), list) else []:
        if not isinstance(raw, Mapping):
            continue
        provider = str(raw.get("provider") or "").lower()
        state = _mapping(provider_status.get(provider))
        free_verified = state.get("free_verified") is True and raw.get("free_admitted") is True
        row = dict(raw)
        row.update({
            "provider": provider,
            "free_verified": free_verified,
            "samples": int(raw.get("task_count") or 0),
            "successes": int(raw.get("task_success_count") or 0),
            "rate_limits": sum(
                1
                for result in report.get("results", []) if isinstance(report.get("results"), list)
                and isinstance(result, Mapping)
                and result.get("provider") == provider
                and result.get("model") == raw.get("model")
                and result.get("error_class") == "RATE_LIMITED"
            ),
            "roles": [key for key, value in _mapping(raw.get("task_scores")).items() if _clamp(value) >= 0.75],
            "role_scores": dict(_mapping(raw.get("task_scores"))),
        })
        rows.append(row)
    return rows


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--candidates", default="")
    parser.add_argument("--direct-free-report", default="")
    parser.add_argument("--incumbents", default="")
    parser.add_argument("--output", default="artifacts/replaceable_agent_organization.json")
    args = parser.parse_args()

    config = load_config(args.config)
    candidates: list[dict[str, Any]] = []
    if args.candidates:
        payload = _load_json(Path(args.candidates))
        raw = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
        candidates.extend(dict(row) for row in raw if isinstance(row, Mapping))
    if args.direct_free_report:
        candidates.extend(candidates_from_direct_free_report(_load_json(Path(args.direct_free_report))))
    incumbent_payload = _load_json(Path(args.incumbents)) if args.incumbents else {}
    incumbents = incumbent_payload.get("assignments") if isinstance(incumbent_payload.get("assignments"), Mapping) else incumbent_payload
    report = assign_agent_slots(candidates, config=config, incumbents=incumbents if isinstance(incumbents, Mapping) else {})

    output = Path(args.output)
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output path must stay inside workspace")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "organization_mode": report["organization_mode"],
        "assigned_slots": sum(row.get("status") == "ASSIGNED" for row in report["assignments"].values()),
        "unfilled_slots": sum(row.get("status") != "ASSIGNED" for row in report["assignments"].values()),
        "provider_concurrency": report["provider_concurrency"],
        "generic_paid_fallback": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
