#!/usr/bin/env python3
"""Merge one specialist-council run into bounded organization memory.

The workflow token remains read-only.  This module therefore writes a candidate
memory artifact for Work to inspect/adopt; it never edits the repository or
changes routing by itself.  Re-processing the same run id is idempotent.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "worker-organization-memory-v1"
MAX_SOURCE_RUNS = 12


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")} or number < 0:
        return None
    return number


def _empty_memory() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_scope": "SPECIALIST_COUNCIL_ATTEMPTS_ONLY",
        "models": {},
        "source_runs": [],
        "source_head": "",
        "updated_through_run": None,
        "notes": "Observed specialist-council behavior only; benchmark scores are intentionally separate.",
    }


def _validated_current(current: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(current, Mapping) or current.get("schema_version") != SCHEMA_VERSION or not isinstance(current.get("models"), Mapping):
        return _empty_memory()
    result = deepcopy(dict(current))
    result["models"] = deepcopy(dict(current.get("models") or {}))
    result["source_runs"] = list(current.get("source_runs") or [])[-MAX_SOURCE_RUNS:]
    return result


def _update_bucket(bucket: dict[str, Any], row: Mapping[str, Any]) -> None:
    previous_attempts = max(0, int(bucket.get("attempts", 0) or 0))
    previous_successes = max(0, int(bucket.get("successes", 0) or 0))
    previous_length = max(0, int(bucket.get("length_failures", 0) or 0))
    previous_rate = max(0, int(bucket.get("rate_limits", 0) or 0))
    previous_latency = _number(bucket.get("avg_latency_ms"))
    previous_latency_samples = max(0, int(bucket.get("latency_samples", previous_attempts if previous_latency is not None else 0) or 0))

    success = row.get("status") == "COUNCIL_OK"
    error = str(row.get("error") or "").strip().lower()
    finish_reason = str(row.get("finish_reason") or "").strip().lower()
    length_failure = error == "empty_visible_content" and finish_reason == "length"
    http_status = row.get("http_status")
    rate_limit = isinstance(http_status, int) and not isinstance(http_status, bool) and http_status == 429
    latency = _number(row.get("latency_ms"))

    attempts = previous_attempts + 1
    successes = previous_successes + int(success)
    bucket["attempts"] = attempts
    bucket["successes"] = successes
    bucket["success_rate"] = round(successes / attempts, 4)
    bucket["length_failures"] = previous_length + int(length_failure)
    bucket["rate_limits"] = previous_rate + int(rate_limit)
    if latency is not None:
        latency_samples = previous_latency_samples + 1
        latency_total = (previous_latency or 0.0) * previous_latency_samples + latency
        bucket["latency_samples"] = latency_samples
        bucket["avg_latency_ms"] = round(latency_total / latency_samples, 3)
    elif "avg_latency_ms" not in bucket:
        bucket["avg_latency_ms"] = None
        bucket["latency_samples"] = previous_latency_samples


def _attempt_rows(council: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    rows = council.get("all_attempts")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, Mapping)]
    rows = council.get("results")
    return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def merge_run_into_memory(
    current: Mapping[str, Any] | None,
    council: Mapping[str, Any],
    *,
    run_id: int,
    source_head: str,
) -> dict[str, Any]:
    memory = _validated_current(current)
    source_runs = [int(item) for item in memory.get("source_runs", []) if isinstance(item, int) and not isinstance(item, bool)]
    if int(run_id) in source_runs:
        memory["update_status"] = "MEMORY_ALREADY_CURRENT"
        return memory

    models = memory["models"]
    accepted_attempts = 0
    for row in _attempt_rows(council):
        model = str(row.get("model") or "").strip()
        lane = str(row.get("specialist_lane") or "").strip()
        if not model or not lane:
            continue
        model_bucket = models.setdefault(model, {})
        if not isinstance(model_bucket, dict):
            model_bucket = {}
            models[model] = model_bucket
        _update_bucket(model_bucket, row)
        lanes = model_bucket.setdefault("lanes", {})
        if not isinstance(lanes, dict):
            lanes = {}
            model_bucket["lanes"] = lanes
        lane_bucket = lanes.setdefault(lane, {})
        if not isinstance(lane_bucket, dict):
            lane_bucket = {}
            lanes[lane] = lane_bucket
        _update_bucket(lane_bucket, row)
        accepted_attempts += 1

    source_runs.append(int(run_id))
    memory["source_runs"] = source_runs[-MAX_SOURCE_RUNS:]
    memory["source_head"] = str(source_head or "")[:80]
    memory["updated_through_run"] = int(run_id)
    memory["last_update_attempt_count"] = accepted_attempts
    memory["update_status"] = "MEMORY_CANDIDATE_READY"
    memory["model_count"] = len(models)
    return memory


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", default="config/worker_organization_memory.json")
    parser.add_argument("--council", default="artifacts/worker_council.json")
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--source-head", default="")
    parser.add_argument("--output", default="artifacts/worker_organization_memory_next.json")
    args = parser.parse_args()
    current_path = Path(args.current)
    council_path = Path(args.council)
    output = Path(args.output)
    for path in (current_path, council_path, output):
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit("paths must stay inside workspace")

    def load(path: Path) -> Mapping[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, Mapping) else {}

    report = merge_run_into_memory(
        load(current_path),
        load(council_path),
        run_id=args.run_id,
        source_head=args.source_head,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("update_status"),
        "updated_through_run": report.get("updated_through_run"),
        "last_update_attempt_count": report.get("last_update_attempt_count", 0),
        "model_count": report.get("model_count", len(report.get("models", {}))),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
