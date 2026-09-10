#!/usr/bin/env python3
"""Turn one multi-agent run into deterministic next-run organization feedback."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return default
    return number


def _bottlenecks(council: Mapping[str, Any], commander: Mapping[str, Any], staging: Mapping[str, Any]) -> list[dict[str, Any]]:
    items: list[tuple[int, str, str]] = []
    selected = max(0, int(council.get("selected_model_count", 0) or 0))
    successful = max(0, int(council.get("successful_lane_count", council.get("successful_model_count", 0)) or 0))
    failed = max(0, int(council.get("failed_lane_count", max(0, selected - successful)) or 0))
    stolen = max(0, int(council.get("work_stealing_count", 0) or 0))
    recovered = max(0, int(council.get("recovered_lane_count", 0) or 0))
    length_exhaustion = max(0, int(council.get("length_exhaustion_count", 0) or 0))
    metrics = _mapping(council.get("parallel_metrics"))
    idle = max(0.0, min(1.0, _number(metrics.get("estimated_worker_idle_ratio"))))
    per_call = max(0.0, _number(metrics.get("successful_tasks_per_ai_call")))

    if commander and commander.get("result_complete") is not True:
        items.append((100, "COMMANDER_COMPLETION", "NVIDIA synthesis did not return a complete structured result."))
    if failed:
        items.append((90, "UNRESOLVED_SPECIALIST_LANES", f"{failed} specialist lane(s) remain unresolved."))
    if stolen and recovered == 0:
        items.append((85, "REDISPATCH_EFFECTIVENESS", "Work stealing ran but recovered no failed lanes."))
    if length_exhaustion:
        items.append((80, "OUTPUT_LENGTH_EXHAUSTION", f"{length_exhaustion} worker response(s) exhausted the visible output budget."))
    configured = int(staging.get("configured_subordinate_parallelism", 0) or 0)
    observed = int(staging.get("observed_parallelism", 0) or 0)
    if staging and (staging.get("status") != "STAGING_PARALLEL_READY" or (configured > 1 and observed < 2)):
        items.append((75, "SCHEDULER_PARALLELISM", "Staging scheduler did not prove useful subordinate parallel execution."))
    if idle >= 0.40:
        items.append((60, "WORKER_IDLE_TIME", f"Estimated worker idle ratio is {idle:.1%}."))
    if selected and per_call < 0.70:
        items.append((55, "CALL_EFFICIENCY", f"Successful tasks per AI call is {per_call:.2f}."))
    if not items:
        items.append((0, "NO_CRITICAL_BOTTLENECK", "Current measured organization has no critical deterministic bottleneck."))
    return [
        {"rank": index + 1, "priority": priority, "code": code, "evidence": evidence}
        for index, (priority, code, evidence) in enumerate(sorted(items, key=lambda item: (-item[0], item[1])))
    ]


def _next_goals(bottlenecks: list[Mapping[str, Any]]) -> list[str]:
    mapping = {
        "COMMANDER_COMPLETION": "Keep NVIDIA input compact and complete the Structured Patch Bundle without repeating worker analysis.",
        "UNRESOLVED_SPECIALIST_LANES": "Recover unresolved lanes with capability-matched standby workers and bounded partial retry.",
        "REDISPATCH_EFFECTIVENESS": "Improve standby selection and retry payload shape before increasing retry count.",
        "OUTPUT_LENGTH_EXHAUSTION": "Use the larger second-wave output budget only for finish_reason=length failures and shorten retry context where possible.",
        "SCHEDULER_PARALLELISM": "Exercise and measure the staging OpenRouter subordinate scheduler before changing base defaults.",
        "WORKER_IDLE_TIME": "Improve lane-to-worker matching and critical-path utilization before raising concurrency.",
        "CALL_EFFICIENCY": "Reduce low-value duplicate calls and prefer workers with same-run successful structured output.",
        "NO_CRITICAL_BOTTLENECK": "Advance to the next measured bottleneck while preserving the current execution path.",
    }
    goals: list[str] = []
    for item in bottlenecks:
        goal = mapping.get(str(item.get("code") or ""))
        if goal and goal not in goals:
            goals.append(goal)
        if len(goals) >= 5:
            break
    return goals


def _recommended_parallel_limit(council: Mapping[str, Any]) -> int:
    current = max(1, int(council.get("parallel_worker_limit", 4) or 4))
    failure_counts = _mapping(council.get("primary_failure_counts"))
    pressure = sum(int(failure_counts.get(key, 0) or 0) for key in ("RATE_LIMIT", "NETWORK", "PROVIDER_5XX"))
    metrics = _mapping(council.get("parallel_metrics"))
    idle = max(0.0, min(1.0, _number(metrics.get("estimated_worker_idle_ratio"))))
    selected = max(1, int(council.get("selected_model_count", 1) or 1))
    successful = max(0, int(council.get("successful_lane_count", council.get("successful_model_count", 0)) or 0))
    success_ratio = successful / selected
    if pressure:
        return max(2, current - 1)
    if success_ratio >= 0.875 and idle < 0.30:
        return min(6, current + 1)
    return current


def build_feedback(
    *,
    source_head: str,
    council: Mapping[str, Any],
    commander: Mapping[str, Any] | None = None,
    staging: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    commander = _mapping(commander)
    staging = _mapping(staging)
    bottlenecks = _bottlenecks(council, commander, staging)
    health_rows = council.get("worker_health") if isinstance(council.get("worker_health"), list) else []
    health_counts = Counter(
        str(row.get("health_state") or "UNKNOWN")
        for row in health_rows
        if isinstance(row, Mapping)
    )
    selected = max(0, int(council.get("selected_model_count", 0) or 0))
    successful = max(0, int(council.get("successful_lane_count", council.get("successful_model_count", 0)) or 0))
    fingerprint_payload = {
        "source_head": source_head,
        "selected": [
            [str(row.get("model") or ""), str(row.get("specialist_lane") or "")]
            for row in council.get("selected_models", [])
            if isinstance(row, Mapping)
        ],
        "results": [
            [str(row.get("model") or ""), str(row.get("specialist_lane") or ""), str(row.get("status") or ""), str(row.get("error") or "")]
            for row in council.get("results", [])
            if isinstance(row, Mapping)
        ],
    }
    fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]
    return {
        "schema_version": "ai-army-organization-feedback-v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source_head": source_head,
        "evidence_fingerprint": fingerprint,
        "status": "FEEDBACK_READY",
        "organization_metrics": {
            "selected_lane_count": selected,
            "successful_lane_count": successful,
            "lane_success_ratio": round(successful / max(1, selected), 4),
            "recovered_lane_count": int(council.get("recovered_lane_count", 0) or 0),
            "work_stealing_count": int(council.get("work_stealing_count", 0) or 0),
            "parallel_speedup": _number(_mapping(council.get("parallel_metrics")).get("parallel_speedup")),
            "worker_health_states": dict(sorted(health_counts.items())),
            "commander_complete": commander.get("result_complete") is True if commander else None,
            "staging_parallel_ready": staging.get("status") == "STAGING_PARALLEL_READY" if staging else None,
        },
        "recommended_parallel_worker_limit": _recommended_parallel_limit(council),
        "bottlenecks": bottlenecks,
        "next_goals": _next_goals(bottlenecks),
        "google_calls": 0,
        "paid_fallback": False,
        "production_routing_changed": False,
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--council", default="artifacts/worker_council.json")
    parser.add_argument("--commander", default="artifacts/result_inbox.json")
    parser.add_argument("--staging", default="artifacts/staging_parallel_scheduler_probe.json")
    parser.add_argument("--source-head", default="")
    parser.add_argument("--output", default="artifacts/organization_feedback.json")
    args = parser.parse_args()
    paths = [Path(args.council), Path(args.commander), Path(args.staging), Path(args.output)]
    for path in paths:
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit("paths must stay inside workspace")

    def load(path: Path) -> Mapping[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return value if isinstance(value, Mapping) else {}

    report = build_feedback(
        source_head=str(args.source_head or ""),
        council=load(paths[0]),
        commander=load(paths[1]),
        staging=load(paths[2]),
    )
    paths[3].parent.mkdir(parents=True, exist_ok=True)
    paths[3].write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "recommended_parallel_worker_limit": report["recommended_parallel_worker_limit"],
        "top_bottleneck": report["bottlenecks"][0]["code"],
        "next_goals": report["next_goals"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
