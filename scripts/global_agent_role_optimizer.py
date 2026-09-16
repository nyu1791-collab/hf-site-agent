#!/usr/bin/env python3
"""Global role optimizer for the replaceable AI Army.

Greedy slot-by-slot routing lets an early generic role consume a scarce strong
worker before a later specialist role is considered. This module instead uses
a bounded beam search across the whole organization. The objective rewards
role fit and measured quality while penalizing model/provider concentration.

The optimizer makes no provider calls and cannot enable paid fallback. It only
chooses among candidate rows that were already admitted by the surrounding
reconciliation layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from scripts.replaceable_agent_organization import (
    ROLE_ALIASES,
    SLOT_PRIORITY,
    _role_score,
    autonomy_policy,
    candidate_score,
    normalize_candidate,
    swap_decision,
)


OPTIMIZATION_ORDER = (
    "ENGINEERING_AGENT",
    "CODE_EXECUTOR",
    "QA_VALIDATOR",
    "OPERATIONS_LEAD",
    "CONTEXT_LIBRARIAN",
    "RESULT_SYNTHESIZER",
    "FAST_OPERATOR",
)

SLOT_IMPORTANCE = {
    "ENGINEERING_AGENT": 1.24,
    "CODE_EXECUTOR": 1.22,
    "QA_VALIDATOR": 1.14,
    "OPERATIONS_LEAD": 1.18,
    "CONTEXT_LIBRARIAN": 1.00,
    "RESULT_SYNTHESIZER": 1.05,
    "FAST_OPERATOR": 0.94,
}

MINIMUM_ROLE_FIT = {
    "ENGINEERING_AGENT": 0.68,
    "CODE_EXECUTOR": 0.72,
    "QA_VALIDATOR": 0.68,
    "OPERATIONS_LEAD": 0.68,
    "CONTEXT_LIBRARIAN": 0.62,
    "RESULT_SYNTHESIZER": 0.64,
    "FAST_OPERATOR": 0.72,
}

# A compound agent role must demonstrate more than one isolated strength.
# With two required capabilities this requires both; with three it requires
# at least two. This is routing quality control, not an execution permission
# gate: it prevents a JSON-only model from being mistaken for a QA agent.
MINIMUM_REQUIRED_CAPABILITY_COVERAGE = 0.66
CAPABILITY_SCORE_EVIDENCE_FLOOR = 0.60

TOP_CANDIDATES_PER_SLOT = 10
BEAM_WIDTH = 512
MODEL_REUSE_PENALTY = 0.055
PROVIDER_CONCENTRATION_PENALTY = 0.018
QA_SAME_AS_CODE_PENALTY = 0.11
UNFILLED_PENALTY = 0.20


@dataclass(frozen=True)
class _State:
    objective: float
    assignments: tuple[tuple[str, int | None], ...]
    model_uses: tuple[tuple[str, str, int], ...]
    provider_uses: tuple[tuple[str, int], ...]
    code_binding: tuple[str, str] | None


def _uses_to_dict(rows: tuple[tuple[str, str, int], ...]) -> dict[tuple[str, str], int]:
    return {(provider, model): count for provider, model, count in rows}


def _provider_uses_to_dict(rows: tuple[tuple[str, int], ...]) -> dict[str, int]:
    return {provider: count for provider, count in rows}


def _freeze_model_uses(values: Mapping[tuple[str, str], int]) -> tuple[tuple[str, str, int], ...]:
    return tuple(sorted((provider, model, int(count)) for (provider, model), count in values.items() if count > 0))


def _freeze_provider_uses(values: Mapping[str, int]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted((provider, int(count)) for provider, count in values.items() if count > 0))


def _binding(candidate: Mapping[str, Any]) -> tuple[str, str]:
    return str(candidate.get("provider") or ""), str(candidate.get("model") or "")


def _score_value(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return 0.0
    return max(0.0, min(1.0, number))


def required_capability_coverage(candidate: Mapping[str, Any], slot: Mapping[str, Any]) -> float:
    """Measure evidenced coverage of a compound role.

    Explicit capability/role tags count as evidence. Numeric role scores count
    only when they meet a real evidence floor. When a benchmark provides a
    direct score for a required capability (for example FAST=0.0), that direct
    observation is authoritative: a broad overlapping alias such as JSON must
    not override an explicit failed capability measurement. If there is no
    direct capability measurement, compatible aliases remain usable so older
    role-scoped benchmark evidence stays backward-compatible.
    """
    required = [
        str(item).strip().lower()
        for item in slot.get("required_capabilities", [])
        if str(item).strip()
    ]
    if not required:
        return 1.0

    declared: set[str] = set()
    for key in ("roles", "capability_tags", "capabilities"):
        raw = candidate.get(key)
        if isinstance(raw, (list, tuple, set)):
            declared.update(str(item).strip().upper() for item in raw if str(item).strip())
    role_scores = candidate.get("role_scores") if isinstance(candidate.get("role_scores"), Mapping) else {}

    hits = 0
    for capability in required:
        direct_key = capability.upper()
        if direct_key in role_scores:
            direct_hit = _score_value(role_scores.get(direct_key)) >= CAPABILITY_SCORE_EVIDENCE_FLOOR
            explicit_hit = direct_key in declared
            if direct_hit or explicit_hit:
                hits += 1
            continue

        aliases = ROLE_ALIASES.get(capability, (direct_key,))
        explicit_hit = direct_key in declared or any(alias in declared for alias in aliases)
        numeric_hit = any(
            _score_value(role_scores.get(alias)) >= CAPABILITY_SCORE_EVIDENCE_FLOOR
            for alias in aliases
            if alias in role_scores
        )
        if explicit_hit or numeric_hit:
            hits += 1
    return hits / len(required)


def _eligible_for_slot(candidate: Mapping[str, Any], slot_name: str, slot: Mapping[str, Any]) -> tuple[bool, float, float]:
    score = candidate_score(candidate, slot)
    if score < 0:
        return False, score, 0.0
    role_fit = _role_score(candidate, slot)
    minimum = float(slot.get("minimum_role_fit") or MINIMUM_ROLE_FIT.get(slot_name, 0.60))
    if role_fit + 1e-12 < minimum:
        return False, score, role_fit
    minimum_coverage = float(slot.get("minimum_required_capability_coverage") or MINIMUM_REQUIRED_CAPABILITY_COVERAGE)
    if required_capability_coverage(candidate, slot) + 1e-12 < minimum_coverage:
        return False, score, role_fit
    return True, score, role_fit


def _slot_options(
    candidates: Sequence[Mapping[str, Any]],
    slot_name: str,
    slot: Mapping[str, Any],
) -> list[tuple[int, float, float]]:
    options: list[tuple[int, float, float]] = []
    for index, candidate in enumerate(candidates):
        eligible, score, role_fit = _eligible_for_slot(candidate, slot_name, slot)
        if eligible:
            options.append((index, score, role_fit))
    options.sort(key=lambda item: (-item[1], -item[2], _binding(candidates[item[0]])))
    return options[:TOP_CANDIDATES_PER_SLOT]


def _state_sort_key(state: _State) -> tuple[Any, ...]:
    """Return a total-order key even when a beam branch leaves a slot unfilled."""
    stable_assignments = tuple(
        (slot_name, -1 if candidate_index is None else int(candidate_index))
        for slot_name, candidate_index in state.assignments
    )
    return (-round(state.objective, 10), stable_assignments)


def globally_select_challengers(
    candidates: Sequence[Mapping[str, Any]],
    *,
    config: Mapping[str, Any],
) -> dict[str, Mapping[str, Any] | None]:
    normalized: list[dict[str, Any]] = []
    for raw in candidates:
        try:
            normalized.append(normalize_candidate(raw))
        except Exception:
            continue
    normalized.sort(key=_binding)
    slots = config.get("slots") if isinstance(config.get("slots"), Mapping) else {}
    adaptive = config.get("adaptive_controls") if isinstance(config.get("adaptive_controls"), Mapping) else {}
    max_model_uses = max(1, min(4, int(adaptive.get("max_slots_per_same_model") or 2)))

    options_by_slot = {
        slot_name: _slot_options(normalized, slot_name, slots.get(slot_name, {}))
        for slot_name in OPTIMIZATION_ORDER
    }
    beam = [
        _State(
            objective=0.0,
            assignments=(),
            model_uses=(),
            provider_uses=(),
            code_binding=None,
        )
    ]

    for slot_name in OPTIMIZATION_ORDER:
        importance = float(SLOT_IMPORTANCE.get(slot_name, 1.0))
        expanded: list[_State] = []
        for state in beam:
            expanded.append(_State(
                objective=state.objective - UNFILLED_PENALTY * importance,
                assignments=state.assignments + ((slot_name, None),),
                model_uses=state.model_uses,
                provider_uses=state.provider_uses,
                code_binding=state.code_binding,
            ))
            model_uses = _uses_to_dict(state.model_uses)
            provider_uses = _provider_uses_to_dict(state.provider_uses)
            for candidate_index, base_score, role_fit in options_by_slot[slot_name]:
                candidate = normalized[candidate_index]
                binding = _binding(candidate)
                current_model_uses = model_uses.get(binding, 0)
                if current_model_uses >= max_model_uses:
                    continue
                provider = binding[0]
                concentration = provider_uses.get(provider, 0)
                penalty = MODEL_REUSE_PENALTY * current_model_uses
                penalty += PROVIDER_CONCENTRATION_PENALTY * concentration
                if slot_name == "QA_VALIDATOR" and state.code_binding == binding:
                    penalty += QA_SAME_AS_CODE_PENALTY
                incremental = importance * (0.88 * base_score + 0.12 * role_fit) - penalty

                next_model_uses = dict(model_uses)
                next_model_uses[binding] = current_model_uses + 1
                next_provider_uses = dict(provider_uses)
                next_provider_uses[provider] = concentration + 1
                expanded.append(_State(
                    objective=state.objective + incremental,
                    assignments=state.assignments + ((slot_name, candidate_index),),
                    model_uses=_freeze_model_uses(next_model_uses),
                    provider_uses=_freeze_provider_uses(next_provider_uses),
                    code_binding=binding if slot_name == "CODE_EXECUTOR" else state.code_binding,
                ))
        expanded.sort(key=_state_sort_key)
        beam = expanded[:BEAM_WIDTH]

    winner = min(beam, key=_state_sort_key) if beam else None
    selected: dict[str, Mapping[str, Any] | None] = {slot: None for slot in SLOT_PRIORITY}
    if winner:
        for slot_name, candidate_index in winner.assignments:
            selected[slot_name] = normalized[candidate_index] if candidate_index is not None else None
    return selected


def optimize_agent_slots(
    candidates: Sequence[Mapping[str, Any]],
    *,
    config: Mapping[str, Any],
    incumbents: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Select challengers globally, then apply per-slot swap hysteresis."""
    incumbents = incumbents if isinstance(incumbents, Mapping) else {}
    slots = config.get("slots") if isinstance(config.get("slots"), Mapping) else {}
    selected = globally_select_challengers(candidates, config=config)
    assignments: dict[str, dict[str, Any]] = {}
    swap_events: list[dict[str, Any]] = []

    for slot_name in SLOT_PRIORITY:
        slot = slots.get(slot_name) if isinstance(slots.get(slot_name), Mapping) else {}
        challenger = selected.get(slot_name)
        incumbent = incumbents.get(slot_name) if isinstance(incumbents.get(slot_name), Mapping) else None
        if challenger is None:
            if incumbent:
                chosen = normalize_candidate(incumbent)
                decision = {"decision": "KEEP", "reason": "no_globally_eligible_challenger"}
            else:
                assignments[slot_name] = {
                    "status": "UNFILLED",
                    "reason": "no_globally_eligible_candidate",
                }
                continue
        else:
            decision = swap_decision(incumbent, challenger, slot, config)
            if incumbent and decision.get("decision") in {"KEEP", "SHADOW_CANARY"}:
                chosen = normalize_candidate(incumbent)
            else:
                chosen = normalize_candidate(challenger)

        autonomy = autonomy_policy(
            config,
            risk_level=str(slot.get("default_risk") or "MEDIUM"),
            deterministic_validator_available=slot_name not in {"OPERATIONS_LEAD", "ENGINEERING_AGENT"},
        )
        assignments[slot_name] = {
            "status": "ASSIGNED",
            "provider": chosen["provider"],
            "model": chosen["model"],
            "slot_score": candidate_score(chosen, slot),
            "role_fit": _role_score(chosen, slot),
            "required_capability_coverage": required_capability_coverage(chosen, slot),
            "mission": slot.get("mission"),
            "may_delegate": bool(slot.get("may_delegate")),
            "max_child_tasks": int(slot.get("max_child_tasks") or 0),
            "max_parallel_children": int(slot.get("max_parallel_children") or 0),
            "default_risk": str(slot.get("default_risk") or "MEDIUM"),
            "autonomy": autonomy,
            "swap_decision": decision,
        }
        swap_events.append({
            "slot": slot_name,
            **decision,
            "selected_provider": chosen["provider"],
            "selected_model": chosen["model"],
        })

    adaptive = config.get("adaptive_controls") if isinstance(config.get("adaptive_controls"), Mapping) else {}
    provider_limits = adaptive.get("provider_parallelism") if isinstance(adaptive.get("provider_parallelism"), Mapping) else {}
    provider_load: dict[str, int] = {}
    for row in assignments.values():
        if row.get("status") == "ASSIGNED":
            provider = str(row.get("provider") or "")
            provider_load[provider] = provider_load.get(provider, 0) + 1
    concurrency = {
        provider: min(count, max(1, int(provider_limits.get(provider) or 1)))
        for provider, count in sorted(provider_load.items())
    }
    return {
        "schema_version": "replaceable-agent-organization-report-v2",
        "organization_mode": "REPLACEABLE_ROLE_SLOTS_GLOBAL_OPTIMIZATION",
        "assignment_policy": "BOUNDED_GLOBAL_BEAM_SEARCH_WITH_ROLE_FIT_AND_DIVERSITY",
        "assignments": assignments,
        "swap_events": swap_events,
        "provider_concurrency": concurrency,
        "organization_parallel_limit": max(1, int(adaptive.get("organization_parallel_limit") or 6)),
        "hard_boundaries": dict(config.get("hard_boundaries") or {}),
        "generic_paid_fallback": False,
        "auto_top_up": False,
        "external_model_repository_write": False,
    }


__all__ = [
    "CAPABILITY_SCORE_EVIDENCE_FLOOR",
    "MINIMUM_REQUIRED_CAPABILITY_COVERAGE",
    "MINIMUM_ROLE_FIT",
    "OPTIMIZATION_ORDER",
    "SLOT_IMPORTANCE",
    "globally_select_challengers",
    "optimize_agent_slots",
    "required_capability_coverage",
]
