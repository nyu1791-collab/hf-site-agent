#!/usr/bin/env python3
"""Free-first media routing control plane.

This planner does not call providers, publish content, mutate secrets, or spend
money. It converts fresh provider/runtime evidence into a deterministic route
for Google analysis, free image/video generation, FFmpeg post-processing and
Google quality review. Paid media routes are intentionally absent.

Evidence is fail-closed. A bare collection of booleans is not sufficient to
make a route READY: every evidence row must be bound to its route/provider,
expected model family when configured, trusted internal producer class and a
fresh observation timestamp. This is provenance binding, not cryptographic
authentication; live callers must still supply evidence produced by a trusted
internal probe rather than accepting arbitrary external evidence files.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time
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


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _evidence_contract(config: Mapping[str, Any]) -> Mapping[str, Any]:
    value = config.get("evidence_contract")
    return value if isinstance(value, Mapping) else {}


def normalize_evidence(
    value: Mapping[str, Any] | None,
    *,
    config: Mapping[str, Any] | None = None,
    now_epoch_seconds: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Normalize and provenance-check route evidence.

    The caller controls the clock only for deterministic tests. In normal use
    the current wall clock is used. Unknown routes and malformed rows remain in
    the normalized output but are marked contract-invalid so route selection
    fails closed.
    """
    mesh = dict(config or load_config())
    routes = mesh.get("routes") if isinstance(mesh.get("routes"), Mapping) else {}
    contract = _evidence_contract(mesh)
    contract_version = str(contract.get("schema_version") or "")
    ttl_seconds = max(1.0, float(contract.get("ttl_seconds") or 1.0))
    max_future_skew = max(0.0, float(contract.get("max_future_skew_seconds") or 0.0))
    trusted_sources = {str(item) for item in (contract.get("trusted_sources") or ()) if str(item)}
    now = _finite_number(now_epoch_seconds)
    if now is None:
        now = time.time()

    result: dict[str, dict[str, Any]] = {}
    for route_id, row in (value or {}).items():
        route_key = str(route_id)
        if not isinstance(row, Mapping):
            result[route_key] = {
                "_evidence_contract_ready": False,
                "_evidence_failures": ["evidence_row_not_mapping"],
            }
            continue

        route = routes.get(route_key) if isinstance(routes.get(route_key), Mapping) else {}
        normalized: dict[str, Any] = {
            str(key): _bool(flag)
            for key, flag in row.items()
            if isinstance(flag, bool)
        }
        failures: list[str] = []

        schema_ok = bool(contract_version) and str(row.get("evidence_schema_version") or "") == contract_version
        if not schema_ok:
            failures.append("evidence_schema_version")

        expected_route = route_key
        route_binding_ok = bool(route) and str(row.get("route_id") or "") == expected_route
        if contract.get("require_route_binding") is True and not route_binding_ok:
            failures.append("route_binding")

        expected_provider = str(route.get("provider") or "")
        provider_binding_ok = bool(expected_provider) and str(row.get("provider") or "") == expected_provider
        if contract.get("require_provider_binding") is True and not provider_binding_ok:
            failures.append("provider_binding")

        expected_model_family = str(route.get("model_family") or "")
        model_family_binding_ok = not expected_model_family or str(row.get("model_family") or "") == expected_model_family
        if contract.get("require_model_family_binding_when_configured") is True and not model_family_binding_ok:
            failures.append("model_family_binding")

        source = str(row.get("source") or "")
        expected_source = str(route.get("evidence_source") or "")
        source_ok = bool(source and expected_source) and source == expected_source and source in trusted_sources
        if contract.get("require_trusted_source") is True and not source_ok:
            failures.append("trusted_source")

        observed_at = _finite_number(row.get("observed_at_epoch"))
        age_seconds: float | None = None
        fresh = False
        if observed_at is not None and observed_at > 0:
            age_seconds = now - observed_at
            fresh = -max_future_skew <= age_seconds <= ttl_seconds
        if not fresh:
            failures.append("fresh_observation")

        normalized.update({
            "_evidence_contract_ready": not failures,
            "_evidence_failures": failures,
            "_evidence_schema_version": str(row.get("evidence_schema_version") or ""),
            "_source": source,
            "_route_binding_ok": route_binding_ok,
            "_provider_binding_ok": provider_binding_ok,
            "_model_family_binding_ok": model_family_binding_ok,
            "_fresh": fresh,
            "_observed_at_epoch": observed_at,
            "_observed_age_seconds": round(age_seconds, 3) if age_seconds is not None else None,
        })
        result[route_key] = normalized
    return result


def route_readiness(
    config: Mapping[str, Any],
    route_id: str,
    evidence: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    routes = config.get("routes") if isinstance(config.get("routes"), Mapping) else {}
    route = routes.get(route_id) if isinstance(routes.get(route_id), Mapping) else None
    if route is None:
        return {"route": route_id, "ready": False, "missing": ["route_config"], "kind": "unknown"}
    requirements = [str(v) for v in route.get("requires", [])]
    observed = evidence.get(route_id, {})
    missing = [name for name in requirements if observed.get(name) is not True]
    evidence_ready = observed.get("_evidence_contract_ready") is True
    if not evidence_ready:
        missing.append("evidence_contract")
    paid = route.get("paid") is True or str(route.get("cost_class") or "").upper().startswith("PAID")
    forbidden_paid_name = route_id.startswith(PAID_ROUTE_PREFIXES)
    return {
        "route": route_id,
        "kind": str(route.get("kind") or "unknown"),
        "provider": str(route.get("provider") or "unknown"),
        "cost_class": str(route.get("cost_class") or "UNKNOWN"),
        "ready": not missing and not paid and not forbidden_paid_name,
        "missing": missing,
        "evidence_contract_ready": evidence_ready,
        "evidence_failures": list(observed.get("_evidence_failures") or ()),
        "evidence_fresh": observed.get("_fresh") is True,
        "evidence_age_seconds": observed.get("_observed_age_seconds"),
        "paid": paid or forbidden_paid_name,
    }


def _role_route_order(config: Mapping[str, Any], role: str) -> list[str]:
    roles = config.get("roles") if isinstance(config.get("roles"), Mapping) else {}
    row = roles.get(role) if isinstance(roles.get(role), Mapping) else {}
    return [str(v) for v in row.get("route_order", [])]


def select_first_ready(
    config: Mapping[str, Any],
    route_order: list[str],
    evidence: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    attempts = [route_readiness(config, route_id, evidence) for route_id in route_order]
    for attempt in attempts:
        if attempt["ready"] is True:
            return {"selected": attempt["route"], "ready": True, "attempts": attempts}
    return {"selected": None, "ready": False, "attempts": attempts}


def select_analysis_route(config: Mapping[str, Any], evidence: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return select_first_ready(config, _role_route_order(config, "GOOGLE_MEDIA_ANALYST"), evidence)


def select_quality_review_route(config: Mapping[str, Any], evidence: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return select_first_ready(config, _role_route_order(config, "MEDIA_QUALITY_REVIEWER"), evidence)


def select_image_route(config: Mapping[str, Any], evidence: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return select_first_ready(config, _role_route_order(config, "IMAGE_GENERATION_LEAD"), evidence)


def select_video_route(config: Mapping[str, Any], evidence: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return select_first_ready(config, _role_route_order(config, "VIDEO_GENERATION_LEAD"), evidence)


def select_post_process_route(config: Mapping[str, Any], evidence: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
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
    now_epoch_seconds: float | None = None,
) -> dict[str, Any]:
    mesh = dict(config or load_config())
    normalized = normalize_evidence(evidence, config=mesh, now_epoch_seconds=now_epoch_seconds)
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
    evidence_contract = _evidence_contract(mesh)
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
            "stale_or_unbound_evidence_can_execute": False,
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
            "evidence_schema_version": str(evidence_contract.get("schema_version") or ""),
            "evidence_ttl_seconds": int(evidence_contract.get("ttl_seconds") or 0),
            "provenance_binding_required": True,
            "cryptographic_evidence_authentication": bool(evidence_contract.get("cryptographic_authentication_provided") is True),
            "trusted_internal_producer_required": bool(evidence_contract.get("trusted_internal_producer_required") is True),
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
        "stale_or_unbound_evidence_can_execute",
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
    execution = plan.get("execution_contract") if isinstance(plan.get("execution_contract"), Mapping) else {}
    if execution.get("planner_spends_money") is not False:
        raise ValueError("planner may not spend money")
    if execution.get("live_execution_requires_fresh_runtime_evidence") is not True:
        raise ValueError("fresh runtime evidence must be required")
    if execution.get("provenance_binding_required") is not True:
        raise ValueError("route evidence provenance binding must be required")
    if execution.get("trusted_internal_producer_required") is not True:
        raise ValueError("trusted internal evidence producer must be required")


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
