#!/usr/bin/env python3
"""Fail closed on Jev fast-decision-plane policy/runtime drift."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config" / "jev_decision_engine_policy.json"
RUNTIME = ROOT / "scripts" / "jev_decision_engine.py"
COORDINATOR = ROOT / "scripts" / "jev_routing_coordinator.py"
FINAL_GUARD = ROOT / "scripts" / "final_execution_admission_guard.py"
JEV_PLAYBOOK = ROOT / "docs" / "JEV_FAST_DECISION_PLAYBOOK.md"
LEAN_RUNTIME = ROOT / "scripts" / "jev_lean_router.py"
SHAPE_RUNTIME = ROOT / "scripts" / "jev_shape_router.py"
MULTI = ROOT / "config" / "multi_agent_operating_policy.json"
ORG = ROOT / "config" / "ai_army_org_chart.json"


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> int:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    require(policy.get("status") == "ACTIVE_SCOPED_PAID_EXCEPTION", "Jev exception inactive")
    auth = policy.get("user_authorization") or {}
    require(auth.get("authorized") is True, "Jev not authorized")
    require(auth.get("persistent_across_chat_tabs") is True, "Jev authorization not persistent")
    require(auth.get("repeat_per_call_approval_required") is False, "Jev incorrectly requires repeat approval")

    provider = policy.get("provider") or {}
    require(provider.get("canonical_model_alias") == "~typesafe/jev-latest", "Jev latest alias drift")
    require(provider.get("last_known_good_model") == "typesafe/jev-1.13", "Jev last-known-good pin drift")
    require(provider.get("automatic_latest_migration") is True, "Jev auto latest migration disabled")

    guard = policy.get("cost_guard") or {}
    require(guard.get("cost_is_not_primary_optimization_target_for_jev") is True, "Jev cost became primary optimizer")
    require(float(guard.get("hard_emergency_price_ceiling_prompt_usd_per_million", 0)) == 1.0, "Jev emergency prompt ceiling drift")
    require(float(guard.get("hard_emergency_price_ceiling_completion_usd_per_million", 0)) == 1.0, "Jev emergency completion ceiling drift")
    require(int(guard.get("max_records_per_decisions_request", 0)) == 20, "Jev batch size must remain 20")
    require(int(guard.get("max_parallel_decision_batches", 0)) == 5, "Jev parallel batch ceiling must remain five")
    require(guard.get("auto_top_up") is False, "Jev auto top-up enabled")
    require(guard.get("other_paid_model_fallback") is False, "Jev may fall back to other paid model")

    contract = policy.get("decision_contract") or {}
    require(contract.get("typed_output_required") is True, "Jev typed output lost")
    require(contract.get("free_text_output_allowed") is False, "Jev free-text output was re-enabled")
    require(contract.get("python_normalizes_final_json") is True, "Python final normalization lost")
    require(contract.get("python_performs_all_counting_arithmetic_and_quota_math") is True, "Jev was given arithmetic responsibility")
    require(contract.get("no_markdown_or_json_text_parsing") is True, "text parsing path re-enabled")
    require(contract.get("final_authority") == "chatgpt-top-commander", "Jev gained final authority")
    require(contract.get("model_may_not_expand_candidate_set") is True, "Jev may expand candidate set")
    require(contract.get("model_may_not_expand_permissions") is True, "Jev may expand permissions")
    require(contract.get("model_may_not_authorize_paid_workers") is True, "Jev may authorize paid workers")
    quality = policy.get("decision_quality") or {}
    require(
        quality.get("priority_order", [None])[0] == "VERIFIED_ROUTE_CORRECTNESS",
        "Jev route correctness is not the first optimization priority",
    )
    require(quality.get("routing_latency_is_not_sufficient_reason_to_reduce_question_surface") is True, "routing latency can incorrectly reduce Jev decision surface")
    require(int(quality.get("minimum_domain_successes_for_clear_primary", 0)) >= 3, "clear primary requires insufficient domain evidence")
    require(quality.get("single_success_or_latency_advantage_alone_cannot_clear_primary") is True, "single success or latency can clear primary")
    require(quality.get("thin_or_tied_evidence_action") == "KEEP_JEV_LEAN_OR_RICH_DECISION_SURFACE", "thin evidence may bypass Jev")
    require(float(quality.get("minimum_confidence_for_autonomous_execute", 0)) >= 0.75, "Jev autonomous confidence threshold too low")
    require(float(contract.get("low_confidence_threshold", 0)) >= float(quality.get("minimum_confidence_for_autonomous_execute", 1)), "runtime confidence threshold is below Jev quality policy")
    fast_contract = contract.get("fast_route_contract") or {}
    require(int(fast_contract.get("routine_question_count", 0)) == 3, "rich Jev route must use three questions")
    require(int(fast_contract.get("max_question_count", 0)) == 4, "Jev fast route must cap at four questions")
    require(fast_contract.get("python_derives_fanout") is True, "Python no longer derives fast-route fanout")
    lean_contract = contract.get("lean_route_contract") or {}
    require(int(lean_contract.get("routine_question_count", 0)) == 2, "routine Jev route must use two questions")
    require(lean_contract.get("python_selects_secondary_and_tertiary_from_health_ranked_candidates") is True, "Python complement selection drift")
    shape_contract = contract.get("shape_only_route_contract") or {}
    require(int(shape_contract.get("routine_question_count", 0)) == 1, "shape-only Jev route must use one question")
    require(shape_contract.get("python_selects_primary_and_complements") is True, "Python shape-only composition drift")
    zero_contract = contract.get("deterministic_health_fast_path") or {}
    require(zero_contract.get("enabled") is True, "zero-question deterministic health fast path disabled")
    require(int(zero_contract.get("jev_question_count", -1)) == 0, "zero-question fast path drift")
    final_guard = contract.get("final_execution_admission_guard") or {}
    require(final_guard.get("required_for_every_route_surface") is True, "final execution guard is not universal")
    require(final_guard.get("runtime") == "scripts/final_execution_admission_guard.py", "final execution guard path drift")
    require(final_guard.get("health_may_reorder_prevalidated_candidates_only") is True, "health eligibility boundary drift")
    require(final_guard.get("missing_or_invalid_evidence_expiry_is_not_routable_evidence") is True, "invalid expiry may route")
    require(final_guard.get("domain_absent_evidence_may_not_qualify_zero_or_one_question_primary") is True, "domain-absent evidence may clear primary")
    require(final_guard.get("shared_mutable_state_forces_sequential_execution") is True, "shared-state serialization drift")
    require(final_guard.get("independent_verification_requires_explicit_verifier_role") is True, "verification role contract drift")

    source = RUNTIME.read_text(encoding="utf-8")
    require('DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"' in source, "Jev is not using Decisions API")
    require("~typesafe/jev-latest" in source, "Jev latest alias missing from runtime")
    require("candidate_expansion_blocked" in source, "candidate expansion guard missing")
    require("def decide_many(" in source, "batch-first Jev API missing")
    require("def decide_many_fast(" in source, "manual-aligned Jev fast batch API missing")
    require("def build_fast_route_batch_request(" in source, "three-question Jev request builder missing")
    require('"PARALLEL_PAIR"' in source and '"SEQUENTIAL_PAIR"' in source, "typed route-shape contract missing")
    require("ThreadPoolExecutor" in source, "parallel Jev batch execution missing")
    require("max_records_per_request" in source, "Jev 20-record batch control missing")
    require("quota_pressure_from_remaining" in source, "quota enum preprocessing missing")
    require(COORDINATOR.is_file(), "Jev routing coordinator missing")
    require((ROOT / "scripts" / "jev_mixed_router.py").is_file(), "Jev mixed router missing")
    batch = policy.get("batch_execution") or {}
    require(batch.get("production_mixed_surface_grouping") == "SHAPE_SEPARATE__LEAN_PLUS_RICH", "Jev mixed-surface grouping drift")
    require(int(batch.get("max_records_per_request", 0)) == 20, "Jev production batch size drift")
    require(FINAL_GUARD.is_file(), "final execution admission guard missing")
    require(JEV_PLAYBOOK.is_file(), "Jev permanent playbook missing")
    playbook = JEV_PLAYBOOK.read_text(encoding="utf-8")
    require("ZERO_QUESTION_DETERMINISTIC_HEALTH_FAST_PATH" in playbook, "Jev zero-question tier missing from playbook")
    require("SHAPE_ONE_QUESTION" in playbook, "Jev one-question tier missing from playbook")
    require("LEAN_TWO_QUESTION" in playbook, "Jev two-question tier missing from playbook")
    require("FAST_THREE_TO_FOUR_QUESTION" in playbook, "Jev rich tier missing from playbook")
    require(LEAN_RUNTIME.is_file(), "Jev lean routing runtime missing")
    require(SHAPE_RUNTIME.is_file(), "Jev shape routing runtime missing")
    lean_source = LEAN_RUNTIME.read_text(encoding="utf-8")
    require("def build_lean_route_batch_request(" in lean_source, "two-question request builder missing")
    require("def decide_many_lean(" in lean_source, "two-question batch API missing")
    require("__primary_worker" in lean_source and "__route_shape" in lean_source, "two-question typed surface missing")
    shape_source = SHAPE_RUNTIME.read_text(encoding="utf-8")
    require("def build_shape_route_batch_request(" in shape_source, "one-question shape request builder missing")
    require("def decide_many_shape(" in shape_source, "one-question shape batch API missing")
    require("__route_shape" in shape_source, "shape-only typed surface missing")
    coordinator_source = COORDINATOR.read_text(encoding="utf-8")
    require("decide_lean" in coordinator_source and "decide_many_lean" in coordinator_source, "coordinator not using lean route")
    require("decide_shape" in coordinator_source and "decide_many_shape" in coordinator_source, "coordinator not using shape-only route")
    require("DETERMINISTIC_HEALTH_FAST_PATH" in coordinator_source, "zero-question fast path missing")
    require("apply_final_execution_admission_guard" in coordinator_source, "coordinator bypasses final execution guard")
    require("_attach_delayed_latency_challenger" in coordinator_source, "coordinator lacks delayed challenger control")
    require("minimum_domain_successes_for_clear_primary" in coordinator_source, "coordinator does not enforce evidence threshold")
    require("reservation_models" in coordinator_source, "batch coordinator does not reserve delayed challenger quota")
    guard_source = FINAL_GUARD.read_text(encoding="utf-8")
    require("execution_reservation_models" in guard_source, "final guard does not validate delayed reservations")

    multi = json.loads(MULTI.read_text(encoding="utf-8"))
    require((multi.get("routing") or {}).get("jev_default_for_nontrivial_model_and_fanout_choice") is True, "Jev not default for nontrivial routing choice")
    exceptions = (multi.get("security_and_permissions") or {}).get("preauthorized_paid_execution_exceptions") or []
    jev_ex = [x for x in exceptions if isinstance(x, dict) and x.get("role") == "FAST_DECISION_PLANE"]
    require(len(jev_ex) == 1, "Jev paid exception must be exactly one")

    org = json.loads(ORG.read_text(encoding="utf-8"))
    plane = (org.get("hierarchy") or {}).get("decision_plane") or {}
    require(plane.get("role") == "FAST_DECISION_PLANE", "org chart lost Jev decision plane")
    require(plane.get("final_decision_authority") is False, "Jev gained final authority")
    require((plane.get("routing_priority") or [None])[0] == "VERIFIED_ROUTE_CORRECTNESS", "org chart lost Jev correctness priority")
    require(plane.get("thin_evidence_keeps_jev_decision_surface") is True, "org chart allows thin-evidence Jev bypass")

    print(json.dumps({
        "status": "PASS",
        "role": "FAST_DECISION_PLANE",
        "model_alias": provider.get("canonical_model_alias"),
        "last_known_good": provider.get("last_known_good_model"),
        "emergency_prompt_price_ceiling_per_million": guard.get("hard_emergency_price_ceiling_prompt_usd_per_million"),
        "max_records_per_request": guard.get("max_records_per_decisions_request"),
        "max_parallel_batches": guard.get("max_parallel_decision_batches"),
        "routine_shape_route_questions": shape_contract.get("routine_question_count"),
        "routine_lean_route_questions": lean_contract.get("routine_question_count"),
        "complex_fast_route_questions": fast_contract.get("routine_question_count"),
        "max_fast_route_questions": fast_contract.get("max_question_count"),
        "route_correctness_priority": quality.get("priority_order", [None])[0],
        "minimum_domain_successes_for_clear_primary": quality.get("minimum_domain_successes_for_clear_primary"),
        "auto_top_up": False,
        "other_paid_fallback": False,
        "chatgpt_final_authority": True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
