#!/usr/bin/env python3
"""Compare single-controller and centralized multi-agent experiment records.

Input: JSONL rows with at least architecture, success, latency_ms, tokens,
requests, estimated_cost_usd. Optional coordination_tokens/retries/defects.
No external calls. No policy mutation. Output is descriptive evidence only.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


def _num(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    value = row.get(key, default)
    if isinstance(value, bool):
        return float(value)
    return float(value or 0.0)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    lat = [_num(r, "latency_ms") for r in rows]
    tokens = [_num(r, "tokens") for r in rows]
    reqs = [_num(r, "requests") for r in rows]
    costs = [_num(r, "estimated_cost_usd") for r in rows]
    retries = [_num(r, "retry_count") for r in rows]
    coord = [_num(r, "coordination_tokens") for r in rows]
    success = [1.0 if bool(r.get("success")) else 0.0 for r in rows]
    accepted = [1.0 if bool(r.get("acceptance_pass", r.get("success"))) else 0.0 for r in rows]
    escaped = [_num(r, "escaped_defects") for r in rows]
    total_tokens = sum(tokens)
    return {
        "n": n,
        "task_success_rate": sum(success) / n,
        "acceptance_criteria_pass_rate": sum(accepted) / n,
        "escaped_defects_total": sum(escaped),
        "p50_latency_ms": _percentile(lat, 0.50),
        "p95_latency_ms": _percentile(lat, 0.95),
        "mean_latency_ms": statistics.fmean(lat),
        "mean_tokens": statistics.fmean(tokens),
        "mean_requests": statistics.fmean(reqs),
        "mean_estimated_cost_usd": statistics.fmean(costs),
        "total_estimated_cost_usd": sum(costs),
        "mean_retry_count": statistics.fmean(retries),
        "coordination_overhead_ratio": (sum(coord) / total_tokens) if total_tokens > 0 else 0.0,
    }


def _relative(treatment: float, control: float, higher_is_better: bool) -> float | None:
    if control == 0:
        return None
    raw = (treatment - control) / abs(control)
    return raw if higher_is_better else -raw


def compare(control: dict[str, Any], treatment: dict[str, Any]) -> dict[str, Any]:
    if not control.get("n") or not treatment.get("n"):
        return {"status": "INSUFFICIENT_DATA"}
    gains = {
        "task_success_rate": _relative(treatment["task_success_rate"], control["task_success_rate"], True),
        "acceptance_criteria_pass_rate": _relative(treatment["acceptance_criteria_pass_rate"], control["acceptance_criteria_pass_rate"], True),
        "p95_latency_ms": _relative(treatment["p95_latency_ms"], control["p95_latency_ms"], False),
        "mean_tokens": _relative(treatment["mean_tokens"], control["mean_tokens"], False),
        "mean_requests": _relative(treatment["mean_requests"], control["mean_requests"], False),
        "mean_estimated_cost_usd": _relative(treatment["mean_estimated_cost_usd"], control["mean_estimated_cost_usd"], False),
        "mean_retry_count": _relative(treatment["mean_retry_count"], control["mean_retry_count"], False),
    }
    guardrails_non_worse = treatment["escaped_defects_total"] <= control["escaped_defects_total"]
    outcome_improved = (
        treatment["task_success_rate"] > control["task_success_rate"]
        or treatment["acceptance_criteria_pass_rate"] > control["acceptance_criteria_pass_rate"]
    )
    return {
        "status": "MEASURED",
        "relative_value_gains_positive_is_better": gains,
        "guardrails_non_worse": guardrails_non_worse,
        "primary_outcome_improved": outcome_improved,
        "promotion_candidate": bool(guardrails_non_worse and outcome_improved),
        "note": "No universal minimum lift is hardcoded; ChatGPT must adjudicate materiality against mission value and sample quality.",
    }


def read_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        obj = json.loads(line)
        if not isinstance(obj, dict):
            raise ValueError(f"line {idx}: object required")
        rows.append(obj)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--control", default="SINGLE_CONTROLLER")
    parser.add_argument("--treatment", default="CENTRALIZED_MANAGER_WITH_SPECIALISTS")
    args = parser.parse_args()

    rows = read_rows(args.input)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("architecture", "UNKNOWN"))].append(row)

    control = summarize(groups.get(args.control, []))
    treatment = summarize(groups.get(args.treatment, []))
    report = {
        "schema_version": "agent-efficiency-eval-v1",
        "control_architecture": args.control,
        "treatment_architecture": args.treatment,
        "control": control,
        "treatment": treatment,
        "comparison": compare(control, treatment),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
