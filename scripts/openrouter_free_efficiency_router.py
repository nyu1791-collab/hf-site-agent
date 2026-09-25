#!/usr/bin/env python3
"""Deterministic planner for efficient OpenRouter exact-free model fanout.

This module performs catalog-only planning. It never reads an API key, never
calls a model, never spends credits, and never enables paid fallback.

The current zero-priced exact :free catalog is standby capacity. The planner
chooses 1..3 models according to expected total system value. One model is
correct when coordination or quota cost dominates. Parallel models are correct
when independent work, complementary specialization, or verification value
materially improves quality or latency.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "openrouter_free_efficiency_policy.json"
CATALOG_URL = "https://openrouter.ai/api/v1/models"
GENERIC_FREE_ROUTER = "openrouter/free"
MAX_DYNAMIC_FANOUT = 3

TASK_LANE_MAP = {
    "CODING": "CODING_ENGINEERING",
    "ENGINEERING": "CODING_ENGINEERING",
    "DEBUG": "CODING_ENGINEERING",
    "TESTING": "CODING_ENGINEERING",
    "FAST": "FAST_CLASSIFICATION_EXTRACTION",
    "CLASSIFICATION": "FAST_CLASSIFICATION_EXTRACTION",
    "EXTRACTION": "FAST_CLASSIFICATION_EXTRACTION",
    "VISION": "VISION_AND_MEDIA_UNDERSTANDING",
    "IMAGE": "VISION_AND_MEDIA_UNDERSTANDING",
    "VIDEO": "VISION_AND_MEDIA_UNDERSTANDING",
    "MEDIA": "VISION_AND_MEDIA_UNDERSTANDING",
    "FINANCE": "FINANCE_DATA",
    "DATA": "FINANCE_DATA",
    "LONG_CONTEXT": "LONG_CONTEXT_ORCHESTRATION",
    "ORCHESTRATION": "LONG_CONTEXT_ORCHESTRATION",
    "GENERAL": "GENERAL_REASONING",
    "RESEARCH": "GENERAL_REASONING",
    "PLANNING": "GENERAL_REASONING",
}


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("policy must be an object")
    return value


def _zero(value: Any) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def exact_free_catalog_entry(entry: Mapping[str, Any]) -> bool:
    model = str(entry.get("id") or "").strip()
    pricing = entry.get("pricing")
    return bool(
        model
        and model != GENERIC_FREE_ROUTER
        and model.endswith(":free")
        and isinstance(pricing, Mapping)
        and _zero(pricing.get("prompt"))
        and _zero(pricing.get("completion"))
    )


def current_free_catalog(entries: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(entry["id"]): dict(entry)
        for entry in entries
        if isinstance(entry, Mapping) and exact_free_catalog_entry(entry)
    }


def _modalities(entry: Mapping[str, Any]) -> set[str]:
    arch = entry.get("architecture")
    if not isinstance(arch, Mapping):
        return set()
    raw = arch.get("input_modalities")
    return {str(x).lower() for x in raw} if isinstance(raw, list) else set()


def _supported(entry: Mapping[str, Any]) -> set[str]:
    raw = entry.get("supported_parameters")
    return {str(x) for x in raw} if isinstance(raw, list) else set()


def infer_lane(task: Mapping[str, Any]) -> str:
    explicit = str(task.get("lane") or "").strip().upper()
    if explicit:
        return explicit
    task_class = str(task.get("task_class") or task.get("role") or "GENERAL").strip().upper()
    if bool(task.get("requires_vision")) or bool(task.get("requires_image")) or bool(task.get("requires_video")):
        return "VISION_AND_MEDIA_UNDERSTANDING"
    if bool(task.get("long_context")):
        return "LONG_CONTEXT_ORCHESTRATION"
    return TASK_LANE_MAP.get(task_class, "GENERAL_REASONING")


def _meets_task(entry: Mapping[str, Any], task: Mapping[str, Any]) -> bool:
    mods = _modalities(entry)
    if bool(task.get("requires_image")) and "image" not in mods:
        return False
    if bool(task.get("requires_video")) and "video" not in mods:
        return False
    params = _supported(entry)
    if bool(task.get("requires_tools")) and not ({"tools", "tool_choice"} <= params):
        return False
    return True


def ordered_candidates(
    policy: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    task: Mapping[str, Any],
) -> list[str]:
    lane = infer_lane(task)
    free = current_free_catalog(entries)
    lane_map = policy.get("selection_lanes")
    preferred = lane_map.get(lane, []) if isinstance(lane_map, Mapping) else []

    ordered: list[str] = []
    seen: set[str] = set()
    for model in preferred:
        model = str(model)
        if model in free and _meets_task(free[model], task):
            ordered.append(model)
            seen.add(model)

    dynamic: list[tuple[int, int, str]] = []
    for model, entry in free.items():
        if model in seen or not _meets_task(entry, task):
            continue
        supported = _supported(entry)
        feature_score = (
            int({"tools", "tool_choice"} <= supported)
            + int(bool({"structured_outputs", "response_format"} & supported))
        )
        context = entry.get("context_length")
        context = int(context) if isinstance(context, int) else 0
        dynamic.append((-feature_score, -context, model))
    dynamic.sort()
    ordered.extend(model for _features, _context, model in dynamic)
    return ordered


def decide_fanout(
    task: Mapping[str, Any],
    *,
    candidate_count: int,
    remaining_quota: int,
) -> tuple[int, list[str]]:
    """Choose 1..3 models and record why.

    The heuristic intentionally uses explicit task evidence rather than a rigid
    single-model or parallel-model default. It is conservative under quota
    pressure and shared-state/sequential work, and expands only when the task
    exposes independent or verification value.
    """
    if candidate_count <= 0 or remaining_quota <= 0:
        return 0, ["NO_ELIGIBLE_MODEL_OR_QUOTA"]

    reasons: list[str] = []
    if (
        bool(task.get("shared_mutable_state"))
        or bool(task.get("strictly_sequential"))
        or bool(task.get("single_writer_only"))
    ):
        return 1, ["SERIAL_OR_SHARED_STATE_DOMINATES"]

    if remaining_quota <= 5 or bool(task.get("quota_pressure")):
        return 1, ["FREE_QUOTA_HEADROOM_LOW"]

    independent = max(1, int(task.get("independent_workstreams") or 1))
    parallel_fraction = float(task.get("parallelizable_fraction") or (1.0 if independent > 1 else 0.0))
    quality_priority = str(task.get("quality_priority") or "normal").lower()
    latency_priority = str(task.get("latency_priority") or "normal").lower()
    high_impact = bool(task.get("high_impact"))
    high_uncertainty = bool(task.get("high_uncertainty"))
    independent_verification = bool(task.get("independent_verification"))
    complementary = bool(task.get("complementary_specialization"))

    desired = 1
    if independent >= 2 and parallel_fraction >= 0.45:
        desired = min(MAX_DYNAMIC_FANOUT, independent)
        reasons.append("INDEPENDENT_WORKSTREAMS_REDUCE_WALL_CLOCK")
    if complementary and candidate_count >= 2:
        desired = max(desired, 2)
        reasons.append("COMPLEMENTARY_SPECIALIZATION_EXPECTED_TO_RAISE_QUALITY")
    if high_impact and independent_verification and candidate_count >= 2:
        desired = max(desired, 2)
        reasons.append("INDEPENDENT_VERIFICATION_MATERIALLY_REDUCES_RISK")
    if high_uncertainty and quality_priority in {"high", "critical"} and candidate_count >= 2:
        desired = max(desired, 2)
        reasons.append("HIGH_UNCERTAINTY_JUSTIFIES_COMPLEMENTARY_CHECK")
    if latency_priority in {"high", "critical"} and independent >= 3 and parallel_fraction >= 0.65:
        desired = max(desired, 3)
        reasons.append("PARALLELISM_MATERIALLY_REDUCES_LATENCY")

    coordination_penalty = float(task.get("coordination_overhead_ratio") or 0.0)
    if coordination_penalty >= 0.35:
        desired = 1
        reasons = ["COORDINATION_OVERHEAD_EXCEEDS_EXPECTED_FANOUT_GAIN"]

    desired = min(desired, candidate_count, MAX_DYNAMIC_FANOUT, remaining_quota)
    if desired <= 1:
        return 1, reasons or ["ONE_MODEL_HAS_HIGHEST_EXPECTED_TOTAL_SYSTEM_VALUE"]
    return desired, reasons


def plan_task(
    task: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    *,
    account_ten_dollar_eligibility_verified: bool = False,
    free_requests_today: int = 0,
) -> dict[str, Any]:
    policy = load_policy()
    candidates = ordered_candidates(policy, entries, task)
    quota = policy.get("quota") or {}
    hard_stop = int(
        quota.get("verified_ten_dollar_account_daily_hard_stop_requests", 900)
        if account_ten_dollar_eligibility_verified
        else quota.get("unverified_account_daily_hard_stop_requests", 45)
    )
    remaining = max(0, hard_stop - max(0, int(free_requests_today)))
    fanout, reasons = decide_fanout(task, candidate_count=len(candidates), remaining_quota=remaining)

    if fanout == 0:
        return {
            "schema_version": "openrouter-free-efficiency-plan-v2",
            "status": "BLOCKED_NO_EXACT_FREE_MATCH_OR_QUOTA",
            "lane": infer_lane(task),
            "selected_models": [],
            "active_model_count": 0,
            "standby_models": candidates[:8],
            "parallel_model_calls": 0,
            "fanout_reason": reasons,
            "paid_fallback": False,
            "quota_hard_stop": hard_stop,
            "remaining_quota_before_plan": remaining,
        }

    selected = candidates[:fanout]
    execution_mode = "SINGLE_MODEL" if fanout == 1 else "PARALLEL_INDEPENDENT_OR_VERIFICATION"
    return {
        "schema_version": "openrouter-free-efficiency-plan-v2",
        "status": "READY",
        "lane": infer_lane(task),
        "selected_models": selected,
        "primary_model": selected[0],
        "active_model_count": fanout,
        "execution_mode": execution_mode,
        "parallel_model_calls": fanout,
        "fanout_reason": reasons,
        "fanout_decision_owner": "chatgpt-top-commander",
        "standby_models": candidates[fanout:fanout + 8],
        "standby_mode": "SEQUENTIAL_ESCALATION_OR_FUTURE_REPLAN",
        "provider_allow_fallbacks": False,
        "generic_free_router_allowed": False,
        "paid_fallback": False,
        "auto_top_up": False,
        "quota_hard_stop": hard_stop,
        "remaining_quota_before_plan": remaining,
        "catalog_free_model_count": len(current_free_catalog(entries)),
        "stop_when_marginal_expected_value_nonpositive": True,
    }


def fetch_catalog(timeout: int = 8) -> list[dict[str, Any]]:
    request = Request(
        CATALOG_URL,
        headers={"Accept": "application/json", "User-Agent": "hf-site-agent-openrouter-free-efficiency/2.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("catalog data missing")
    return [dict(x) for x in rows if isinstance(x, Mapping)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-class", default="GENERAL")
    parser.add_argument("--requires-tools", action="store_true")
    parser.add_argument("--requires-image", action="store_true")
    parser.add_argument("--requires-video", action="store_true")
    parser.add_argument("--long-context", action="store_true")
    parser.add_argument("--independent-workstreams", type=int, default=1)
    parser.add_argument("--parallelizable-fraction", type=float, default=0.0)
    parser.add_argument("--quality-priority", default="normal")
    parser.add_argument("--latency-priority", default="normal")
    parser.add_argument("--high-impact", action="store_true")
    parser.add_argument("--high-uncertainty", action="store_true")
    parser.add_argument("--independent-verification", action="store_true")
    parser.add_argument("--complementary-specialization", action="store_true")
    parser.add_argument("--coordination-overhead-ratio", type=float, default=0.0)
    parser.add_argument("--free-requests-today", type=int, default=0)
    parser.add_argument("--catalog", default="")
    parser.add_argument("--network-catalog", action="store_true")
    parser.add_argument("--ten-dollar-eligibility-verified", action="store_true")
    parser.add_argument("--output", default="artifacts/openrouter_free_efficiency_plan.json")
    args = parser.parse_args()

    if args.catalog:
        payload = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
        entries = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            raise SystemExit("catalog must be a list or {data:[...]}")
    elif args.network_catalog:
        try:
            entries = fetch_catalog()
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
            entries = []
    else:
        entries = []

    task = {
        "task_class": args.task_class,
        "requires_tools": args.requires_tools,
        "requires_image": args.requires_image,
        "requires_video": args.requires_video,
        "long_context": args.long_context,
        "independent_workstreams": args.independent_workstreams,
        "parallelizable_fraction": args.parallelizable_fraction,
        "quality_priority": args.quality_priority,
        "latency_priority": args.latency_priority,
        "high_impact": args.high_impact,
        "high_uncertainty": args.high_uncertainty,
        "independent_verification": args.independent_verification,
        "complementary_specialization": args.complementary_specialization,
        "coordination_overhead_ratio": args.coordination_overhead_ratio,
    }
    plan = plan_task(
        task,
        entries,
        account_ten_dollar_eligibility_verified=args.ten_dollar_eligibility_verified,
        free_requests_today=args.free_requests_today,
    )
    out = Path(args.output)
    if out.is_absolute() or ".." in out.parts:
        raise SystemExit("output must stay inside workspace")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(plan, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
