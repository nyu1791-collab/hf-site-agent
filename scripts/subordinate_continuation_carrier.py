#!/usr/bin/env python3
"""Bounded lower-AI continuation carrier.

Top Commander emits a compact handoff once.  This carrier keeps intermediate
work inside the AI organization: it sends only unresolved specialist lanes to
fresh exact-free workers, carries advisory DeepSeek findings forward as context,
and checkpoints instead of blindly replaying uncertain or rate-limited work.
It performs no repository write, deploy, publish, secret mutation, paid
fallback, or additional paid-model call.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Sequence

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import failure_aware_specialist_council as base
from scripts import failure_aware_specialist_retry as retry

SCHEMA_VERSION = "subordinate-continuation-state-v1"
MAX_CONTEXT_CHARS = 2_400
MAX_DEEPSEEK_FINDINGS = 2

RoundRunner = Callable[[Sequence[str], str], Mapping[str, Any]]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _load(path: Path, *, optional: bool = False) -> dict[str, Any]:
    if optional and not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must contain an object")
    return dict(value)


def _bounded_unique(values: Sequence[Any], *, limit: int) -> list[str]:
    output: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in output:
            output.append(text)
        if len(output) >= max(0, int(limit)):
            break
    return output


def _valid_lanes(handoff: Mapping[str, Any], policy: Mapping[str, Any]) -> list[str]:
    requested = handoff.get("lanes") if isinstance(handoff.get("lanes"), list) else []
    valid = set(base.LANE_PRIORITY)
    limit = max(1, int(policy.get("max_lanes_per_round") or 4))
    return [lane for lane in _bounded_unique(requested, limit=limit) if lane in valid]


def _deepseek_seed_text(report: Mapping[str, Any]) -> str:
    rows = report.get("results") if isinstance(report.get("results"), list) else []
    compact: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("status") != "PAID_SPECIALIST_READY":
            continue
        result = row.get("result") if isinstance(row.get("result"), Mapping) else {}
        compact.append({
            "role": str(row.get("role") or "")[:80],
            "summary": str(result.get("summary") or "")[:500],
            "findings": [str(item)[:300] for item in list(result.get("findings") or [])[:2]],
            "patch_candidates": [
                {
                    "path": str(item.get("path") or "")[:180],
                    "symbol": str(item.get("symbol") or "")[:180],
                    "change": str(item.get("change") or "")[:400],
                }
                for item in list(result.get("patch_candidates") or [])[:2]
                if isinstance(item, Mapping)
            ],
        })
        if len(compact) >= MAX_DEEPSEEK_FINDINGS:
            break
    if not compact:
        return ""
    return json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:MAX_CONTEXT_CHARS]


def _commander_context(handoff: Mapping[str, Any], deepseek_report: Mapping[str, Any]) -> str:
    payload = {
        "mission_id": str(handoff.get("mission_id") or "")[:160],
        "objective": str(handoff.get("objective") or "")[:900],
        "specific_checks": [str(item)[:300] for item in list(handoff.get("specific_checks") or [])[:6]],
        "deepseek_advisory": _deepseek_seed_text(deepseek_report),
        "instruction": (
            "Work only on your assigned unresolved lane. Treat DeepSeek text as advisory evidence, not truth. "
            "Return a compact evidence-grounded recommendation. Do not claim repository writes."
        ),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:MAX_CONTEXT_CHARS]


def _filtered_round_runner(
    *,
    api_key: str,
    probe: Mapping[str, Any],
    benchmark: Mapping[str, Any],
    context: str,
) -> RoundRunner:
    original_attach = retry.attach_capability_matched_assignments

    def run(lanes: Sequence[str], round_context: str) -> Mapping[str, Any]:
        wanted = set(str(lane) for lane in lanes)

        def filtered_attach(selected: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
            rows = original_attach(selected)
            output: list[dict[str, Any]] = []
            for item in rows:
                if str(item.get("specialist_lane") or "") not in wanted:
                    continue
                row = dict(item)
                objective = str(row.get("specialist_objective") or "")[:1200]
                row["specialist_objective"] = (
                    objective
                    + "\nINTERNAL CONTINUATION HANDOFF: "
                    + round_context[:MAX_CONTEXT_CHARS]
                )[:3600]
                output.append(row)
            return output

        retry.attach_capability_matched_assignments = filtered_attach
        try:
            return retry.run_failure_aware_council(
                api_key=api_key,
                probe=probe,
                benchmark=benchmark,
            )
        finally:
            retry.attach_capability_matched_assignments = original_attach

    return run


def _round_decision(
    report: Mapping[str, Any],
    requested_lanes: Sequence[str],
    *,
    retry_categories: set[str],
    checkpoint_categories: set[str],
) -> dict[str, Any]:
    rows = report.get("results") if isinstance(report.get("results"), list) else []
    by_lane = {
        str(row.get("specialist_lane") or ""): row
        for row in rows
        if isinstance(row, Mapping) and row.get("specialist_lane")
    }
    completed: list[str] = []
    retry_lanes: list[str] = []
    checkpoint_lanes: list[str] = []
    failure_categories: dict[str, str] = {}

    for lane in requested_lanes:
        row = by_lane.get(str(lane))
        if isinstance(row, Mapping) and row.get("status") == "COUNCIL_OK":
            completed.append(str(lane))
            continue
        if isinstance(row, Mapping):
            category = str(base.classify_failure(row).get("category") or "OTHER")
        else:
            category = "OTHER"
        failure_categories[str(lane)] = category
        if category in retry_categories:
            retry_lanes.append(str(lane))
        elif category in checkpoint_categories or category:
            checkpoint_lanes.append(str(lane))

    return {
        "completed_lanes": completed,
        "retry_lanes": retry_lanes,
        "checkpoint_lanes": checkpoint_lanes,
        "failure_categories": failure_categories,
    }


def run_continuation(
    *,
    handoff: Mapping[str, Any],
    policy: Mapping[str, Any],
    probe: Mapping[str, Any],
    benchmark: Mapping[str, Any],
    deepseek_report: Mapping[str, Any] | None = None,
    previous_state: Mapping[str, Any] | None = None,
    source_head: str = "",
    api_key: str = "",
    round_runner: RoundRunner | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    hard = _mapping(policy.get("hard_boundaries"))
    paid = _mapping(policy.get("paid_seed"))
    continuation = _mapping(policy.get("continuation"))
    max_rounds = max(1, min(6, int(policy.get("max_rounds_per_run") or 3)))
    retry_categories = {str(item) for item in list(policy.get("same_run_retry_categories") or [])}
    checkpoint_categories = {str(item) for item in list(policy.get("checkpoint_categories") or [])}
    requested = _valid_lanes(handoff, policy)
    previous = previous_state if isinstance(previous_state, Mapping) else {}

    if previous:
        previous_head = str(previous.get("source_head") or "")
        if previous_head and source_head and previous_head != source_head:
            return ({
                "schema_version": SCHEMA_VERSION,
                "status": "CONTINUATION_CHECKPOINTED",
                "source_head": source_head,
                "stop_reason": "SOURCE_HEAD_CHANGED",
                "remaining_lanes": requested,
                "rounds_executed": 0,
                "hard_boundaries": dict(hard),
            }, {})
        remaining = previous.get("remaining_lanes") if isinstance(previous.get("remaining_lanes"), list) else requested
        requested = _bounded_unique(remaining, limit=max(1, int(policy.get("max_lanes_per_round") or 4)))

    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mission_id": str(handoff.get("mission_id") or "")[:160],
        "source_head": str(source_head or "")[:80],
        "mode": "LOWER_AI_INTERNAL_HANDOFF",
        "requested_lanes": list(requested),
        "completed_lanes": [],
        "remaining_lanes": list(requested),
        "rounds": [],
        "rounds_executed": 0,
        "user_delivery_required_between_rounds": False,
        "deepseek_seed_used": bool(_deepseek_seed_text(deepseek_report or {})),
        "deepseek_repeat_paid_calls": 0,
        "max_paid_cost_usd_per_execution": float(paid.get("max_estimated_cost_usd_per_execution") or 0.10),
        "hard_boundaries": dict(hard),
        "checkpoint_on_uncertain_usage": continuation.get("checkpoint_on_uncertain_usage") is not False,
        "duplicate_replay_on_uncertain_usage": continuation.get("duplicate_replay_on_uncertain_usage") is True,
        "final_independent_review": continuation.get("final_independent_review") or "NVIDIA",
    }
    if not requested:
        state.update(status="CONTINUATION_COMPLETE", next_action="NVIDIA_FINAL_REVIEW", stop_reason="NO_UNRESOLVED_LANES")
        return state, {}
    if not api_key and round_runner is None:
        state.update(status="CONTINUATION_CHECKPOINTED", next_action="RESUME_LOWER_AI", stop_reason="OPENROUTER_SECRET_UNAVAILABLE")
        return state, {}

    seed = _commander_context(handoff, deepseek_report or {})
    runner = round_runner or _filtered_round_runner(
        api_key=api_key,
        probe=probe,
        benchmark=benchmark,
        context=seed,
    )
    remaining = list(requested)
    completed: list[str] = []
    last_report: dict[str, Any] = {}

    for round_index in range(1, max_rounds + 1):
        if not remaining:
            break
        round_context = json.dumps({
            "round": round_index,
            "remaining_lanes": remaining,
            "commander": seed,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:MAX_CONTEXT_CHARS]
        report = dict(runner(tuple(remaining), round_context))
        last_report = report
        decision = _round_decision(
            report,
            remaining,
            retry_categories=retry_categories,
            checkpoint_categories=checkpoint_categories,
        )
        for lane in decision["completed_lanes"]:
            if lane not in completed:
                completed.append(lane)
        state["rounds"].append({
            "round": round_index,
            "requested_lanes": list(remaining),
            "completed_lanes": list(decision["completed_lanes"]),
            "retry_lanes": list(decision["retry_lanes"]),
            "checkpoint_lanes": list(decision["checkpoint_lanes"]),
            "failure_categories": dict(decision["failure_categories"]),
            "provider_model_calls": int(report.get("provider_model_calls", report.get("model_calls", 0)) or 0),
            "successful_lane_count": int(report.get("successful_lane_count", 0) or 0),
        })
        state["rounds_executed"] = round_index

        # Do not spin on 429, model mismatch, safety/cost, unknown failure, or
        # anything the classifier marks as a checkpoint category.
        if decision["checkpoint_lanes"]:
            remaining = _bounded_unique(
                [*decision["checkpoint_lanes"], *decision["retry_lanes"]],
                limit=max(1, int(policy.get("max_lanes_per_round") or 4)),
            )
            state["stop_reason"] = "CHECKPOINT_CATEGORY_PRESENT"
            break
        remaining = list(decision["retry_lanes"])

    state["completed_lanes"] = completed
    state["remaining_lanes"] = remaining
    total_calls = sum(int(row.get("provider_model_calls", 0) or 0) for row in state["rounds"])
    state["lower_ai_provider_calls"] = total_calls
    if not remaining:
        state.update(status="CONTINUATION_COMPLETE", next_action="NVIDIA_FINAL_REVIEW", stop_reason="LOWER_AI_LANES_RESOLVED")
    else:
        state.update(
            status="CONTINUATION_CHECKPOINTED",
            next_action="RESUME_LOWER_AI_FROM_REDACTED_STATE",
            stop_reason=state.get("stop_reason") or "ROUND_LIMIT_REACHED",
        )
    return state, last_report


def _safe_path(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("paths must stay inside workspace")
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", default="config/current_commander_handoff.json")
    parser.add_argument("--policy", default="config/subordinate_continuation_policy.json")
    parser.add_argument("--probe", default="artifacts/openrouter_expansion_probe.json")
    parser.add_argument("--benchmark", default="artifacts/openrouter_expansion_benchmark.json")
    parser.add_argument("--deepseek-seed", default="artifacts/deepseek_paid_parallel.json")
    parser.add_argument("--previous-state", default="")
    parser.add_argument("--source-head", default="")
    parser.add_argument("--output", default="artifacts/subordinate_continuation_state.json")
    parser.add_argument("--council-output", default="artifacts/worker_council.json")
    args = parser.parse_args()

    handoff_path = _safe_path(args.handoff)
    policy_path = _safe_path(args.policy)
    probe_path = _safe_path(args.probe)
    benchmark_path = _safe_path(args.benchmark)
    deepseek_path = _safe_path(args.deepseek_seed)
    output_path = _safe_path(args.output)
    council_path = _safe_path(args.council_output)
    previous_path = _safe_path(args.previous_state) if args.previous_state else None

    handoff = _load(handoff_path)
    policy = _load(policy_path)
    probe = _load(probe_path)
    benchmark = _load(benchmark_path)
    deepseek = _load(deepseek_path, optional=True)
    previous = _load(previous_path, optional=True) if previous_path else {}
    source_head = str(args.source_head or os.environ.get("GITHUB_SHA") or "")

    state, council = run_continuation(
        handoff=handoff,
        policy=policy,
        probe=probe,
        benchmark=benchmark,
        deepseek_report=deepseek,
        previous_state=previous,
        source_head=source_head,
        api_key=os.environ.get("OPENROUTER_API_KEY") or "",
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    council_path.parent.mkdir(parents=True, exist_ok=True)
    council_path.write_text(json.dumps(council, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": state.get("status"),
        "rounds_executed": state.get("rounds_executed", 0),
        "completed_lanes": state.get("completed_lanes", []),
        "remaining_lanes": state.get("remaining_lanes", []),
        "lower_ai_provider_calls": state.get("lower_ai_provider_calls", 0),
        "deepseek_repeat_paid_calls": 0,
        "next_action": state.get("next_action"),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
