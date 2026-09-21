#!/usr/bin/env python3
"""Deterministic OpenRouter exact-free efficiency router.

This module performs catalog-only selection. It never reads an API key, never
calls a model, never spends credits, and never enables paid fallback. All current
zero-priced exact :free models may enter standby. Normal task execution selects
one best-fit primary and keeps the rest as sequential standby only.
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
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping) or not exact_free_catalog_entry(entry):
            continue
        model = str(entry["id"])
        result[model] = dict(entry)
    return result


def _modalities(entry: Mapping[str, Any]) -> set[str]:
    arch = entry.get("architecture")
    if not isinstance(arch, Mapping):
        return set()
    raw = arch.get("input_modalities")
    if not isinstance(raw, list):
        return set()
    return {str(x).lower() for x in raw}


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

    dynamic = []
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


def plan_task(
    task: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    *,
    account_ten_dollar_eligibility_verified: bool = False,
) -> dict[str, Any]:
    policy = load_policy()
    candidates = ordered_candidates(policy, entries, task)
    quota = policy.get("quota") or {}
    hard_stop = int(
        quota.get("verified_ten_dollar_account_daily_hard_stop_requests", 900)
        if account_ten_dollar_eligibility_verified
        else quota.get("unverified_account_daily_hard_stop_requests", 45)
    )
    if not candidates:
        return {
            "schema_version": "openrouter-free-efficiency-plan-v1",
            "status": "BLOCKED_NO_EXACT_FREE_MATCH",
            "lane": infer_lane(task),
            "primary_model": None,
            "active_model_count": 0,
            "standby_models": [],
            "parallel_model_calls": 0,
            "paid_fallback": False,
            "quota_hard_stop": hard_stop,
        }
    return {
        "schema_version": "openrouter-free-efficiency-plan-v1",
        "status": "READY",
        "lane": infer_lane(task),
        "primary_model": candidates[0],
        "active_model_count": 1,
        "standby_models": candidates[1:8],
        "standby_mode": "SEQUENTIAL_ESCALATION_ONLY",
        "parallel_model_calls": 1,
        "stop_after_first_acceptable_result": True,
        "second_model_requires_recorded_escalation_reason": True,
        "provider_allow_fallbacks": False,
        "generic_free_router_allowed": False,
        "paid_fallback": False,
        "auto_top_up": False,
        "quota_hard_stop": hard_stop,
        "catalog_free_model_count": len(current_free_catalog(entries)),
    }


def fetch_catalog(timeout: int = 8) -> list[dict[str, Any]]:
    request = Request(
        CATALOG_URL,
        headers={"Accept": "application/json", "User-Agent": "hf-site-agent-openrouter-free-efficiency/1.0"},
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
    }
    plan = plan_task(
        task,
        entries,
        account_ten_dollar_eligibility_verified=args.ten_dollar_eligibility_verified,
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
