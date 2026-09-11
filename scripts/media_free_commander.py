#!/usr/bin/env python3
"""Free-first media routing control plane.

This planner does not call providers, publish content, mutate secrets, or spend
money. It converts fresh provider/runtime evidence into a deterministic route
for Google analysis, free image/video generation, FFmpeg post-processing and
Google quality review. Paid media routes are intentionally absent.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "free_media_mesh.json"
PLAN_VERSION = "free-media-plan-v1"
PAID_ROUTE_PREFIXES = ("FAL_", "RUNWAY_", "GOOGLE_PAID_")


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("free media mesh config must be a JSON object")
    return value


def _bool(value: Any) -> bool:
    return value is True


def normalize_evidence(value: Mapping[str, Any] | None) -> dict[str, dict[str, bool]]:
    result: dict[str, dict[str, bool]] = {}
    for route_id, row in (value or {}).items():
        if not isinstance(row, Mapping):
            continue
        result[str(route_id)] = {str(key): _bool(flag) for key, flag in row.items()}
    return result


def route_readiness(
    config: Mapping[str, Any],
    route_id: str,
    evidence: Mapping[str, Mapping[str, bool]],
) -> dict[str, Any]:
    routes = config.get("routes") if isinstance(config.get("routes"), Mapping) else {}
    route = routes.get(route_id) if isinstance(routes.get(route_id), Mapping) else None
    if route is None:
        return {"route": route_id, "ready": False, "missing": ["route_config"], "kind": "unknown"}
    requirements = [str(v) for v in route.get("requires", [])]
    observed = evidence.get(route_id, {})
    missing = [name for name in requirements if observed.get(name) is not True]
    paid = route.get("paid") is True or str(route.get("cost_class") or "").upper().startswith("PAID")
    forbidden_paid_name = route_id.startswith(PAID_ROUTE_PREFIXES)
    return {
        "route": route_id,
        "kind": str(route.get("kind") or "unknown"),
        "provider": str(route.get("provider") or "unknown"),
        "cost_class": str(route.get("cost_class") or "UNKNOWN"),
        "ready": not missing and not paid and not forbidden_paid_name,
        "missing": missing,
        "paid": paid or forbidden_paid_name,
    }


def _role_route_order(config: Mapping[str, Any], role: str) -> list[str]:
    roles = config.get("roles") if isinstance(config.get("roles"), Mapping) else {}
    row = roles.get(role) if isinstance(roles.get(role), Mapping) else {}
    return [str(v) for v in row.get("route_order", [])]


def select_first_ready(
    config: Mapping[str, Any],
    route_order: list[str],
    evidence: Mapping[str, Mapping[str, bool]],
) -> dict[str, Any]:
    attempts = [route_readiness(config, route_id, evidence) for route_id in route_order]
    for attempt in attempts:
        if attempt["ready"] is True:
            return {"selected": attempt["route"], "ready": True, "attempts": attempts}
    return {"selected": None, "ready": False, "attempts": attempts}


def select_analysis_route(config: Mapping[str, Any], evidence: Mapping[str, Mapping[str, bool]]) -> dict[str, Any]:
    return select_first_ready(config, _role_route_order(config, "GOOGLE_MEDIA_ANALYST"), evidence)


def select_quality_review_route(config: Mapping[str, Any], evidence: Mapping[str, Mapping[str, bool]]) -> dict[str, Any]:
    return select_first_ready(config, _role_route_order(config, "MEDIA_QUALITY_REVIEWER"), evidence)


def select_image_route(config: Mapping[str, Any], evidence: Mapping[str, Mapping[str, bool]]) -> dict[str, Any]:
    return select_first_ready(config, _role_route_order(config, "IMAGE_GENERATION_LEAD"), evidence)


def select_video_route(config: Mapping[str, Any], evidence: Mapping[str, Mapping[str, bool]]) -> dict[str, Any]:
    return select_first_ready(config, _role_route_order(config, "VIDEO_GENERATION_LEAD"), evidence)


def select_post_process_route(config: Mapping[str, Any], evidence: Mapping[str, Mapping[str, bool]]) -> dict[str, Any]:
    return select_first_ready(config, _role_route_order(config, "MEDIA_POST_PROCESSOR"), evidence)


def _task(task_id: str, owner: str, depends_on: list[str], ready: bool, detail: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "owner_role": owner,
        "depends_on": depends_on,
        "state": "READY" if ready else "BLOCKED",
        "detail": detail,
    }


def build_free_media_plan(
    *,
    asset_type: str,
    evidence: Mapping[str, Any] | None = None,
    source_analysis_required: bool = True,
    quality_review_required: bool = True,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    mesh = dict(config or load_config())
    normalized = normalize_evidence(evidence)
    kind = str(asset_type or "").strip().lower()
    if kind not in {"image", "video"}:
        raise ValueError("asset_type must be image or video")

    analysis = select_analysis_route(mesh, normalized)
    generation = select_image_route(mesh, normalized) if kind == "image" else select_video_route(mesh, normalized)
    post = select_post_process_route(mesh, normalized)
    review = select_quality_review_route(mesh, normalized)

    analysis_ready = analysis["ready"] or not source_analysis_required
    review_ready = review["ready"] or not quality_review_required
    tasks = [
        _task(
            "analyze_source",
            "GOOGLE_MEDIA_ANALYST",
            [],
            analysis_ready,
            analysis["selected"] or ("ANALYSIS_NOT_REQUIRED" if not source_analysis_required else "NO_VERIFIED_FREE_GOOGLE_ANALYSIS_ROUTE"),
        ),
        _task(
            "generate_asset",
            "IMAGE_GENERATION_LEAD" if kind == "image" else "VIDEO_GENERATION_LEAD",
            ["analyze_source"] if source_analysis_required else [],
            generation["ready"],
            generation["selected"] or "NO_VERIFIED_FREE_GENERATION_ROUTE",
        ),
        _task(
            "post_process",
            "MEDIA_POST_PROCESSOR",
            ["generate_asset"],
            post["ready"],
            post["selected"] or "NO_VERIFIED_FREE_POST_PROCESS_ROUTE",
        ),
        _task(
            "quality_review",
            "MEDIA_QUALITY_REVIEWER",
            ["post_process"],
            review_ready,
            review["selected"] or ("QUALITY_REVIEW_NOT_REQUIRED" if not quality_review_required else "NO_VERIFIED_FREE_GOOGLE_REVIEW_ROUTE"),
        ),
    ]
    ready = all(task["state"] == "READY" for task in tasks)
    plan = {
        "schema_version": PLAN_VERSION,
        "asset_type": kind,
        "status": "READY" if ready else "BLOCKED_FREE_ROUTE_UNAVAILABLE",
        "selected_routes": {
            "analysis": analysis["selected"] if source_analysis_required else "NOT_REQUIRED",
            "generation": generation["selected"],
            "post_process": post["selected"],
            "quality_review": review["selected"] if quality_review_required else "NOT_REQUIRED",
        },
        "route_evaluation": {
            "analysis": analysis,
            "generation": generation,
            "post_process": post,
            "quality_review": review,
        },
        "tasks": tasks,
        "hard_boundaries": {
            "free_only_default": True,
            "unverified_route_can_execute": False,
            "generic_paid_fallback": False,
            "auto_top_up": False,
            "direct_publish": False,
            "secret_mutation": False,
            "external_model_repository_write": False,
        },
        "execution_contract": {
            "planner_calls_provider": False,
            "planner_spends_money": False,
            "planner_publishes": False,
            "live_execution_requires_fresh_runtime_evidence": True,
        },
    }
    validate_plan(plan)
    return plan


def validate_plan(plan: Mapping[str, Any]) -> None:
    boundaries = plan.get("hard_boundaries") if isinstance(plan.get("hard_boundaries"), Mapping) else {}
    if boundaries.get("free_only_default") is not True:
        raise ValueError("free-only default must remain enabled")
    for key in (
        "unverified_route_can_execute",
        "generic_paid_fallback",
        "auto_top_up",
        "direct_publish",
        "secret_mutation",
        "external_model_repository_write",
    ):
        if boundaries.get(key) is not False:
            raise ValueError(f"unsafe media boundary: {key}")
    selected = plan.get("selected_routes") if isinstance(plan.get("selected_routes"), Mapping) else {}
    for route_id in selected.values():
        if not route_id or route_id in {"NOT_REQUIRED"}:
            continue
        if str(route_id).startswith(PAID_ROUTE_PREFIXES):
            raise ValueError("paid route selected by free media commander")
    if plan.get("execution_contract", {}).get("planner_spends_money") is not False:
        raise ValueError("planner may not spend money")


def _load_evidence(path_text: str) -> dict[str, Any]:
    if not path_text:
        return {}
    value = json.loads(Path(path_text).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("evidence file must contain a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-type", choices=("image", "video"), required=True)
    parser.add_argument("--evidence-file", default="")
    parser.add_argument("--skip-source-analysis", action="store_true")
    parser.add_argument("--skip-quality-review", action="store_true")
    parser.add_argument("--output", default="artifacts/free_media_plan.json")
    args = parser.parse_args()
    plan = build_free_media_plan(
        asset_type=args.asset_type,
        evidence=_load_evidence(args.evidence_file),
        source_analysis_required=not args.skip_source_analysis,
        quality_review_required=not args.skip_quality_review,
    )
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": plan["status"],
        "asset_type": plan["asset_type"],
        "routes": plan["selected_routes"],
        "paid_fallback": plan["hard_boundaries"]["generic_paid_fallback"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
