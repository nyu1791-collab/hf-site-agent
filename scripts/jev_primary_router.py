#!/usr/bin/env python3
"""One-question Jev routing for code-determined execution shapes.

When Python can determine SINGLE/PARALLEL_PAIR/PARALLEL_TRIPLE/SEQUENTIAL_PAIR
from explicit task structure, Jev only chooses the best primary worker.
Secondary/tertiary workers come from the already prevalidated health-ranked
candidate order. This follows TypeSafe's workflow guidance: deterministic rules
and arithmetic stay in code; Jev handles the one fuzzy judgment.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from typing import Any, Mapping, Sequence

from scripts.jev_decision_engine import (
    Action,
    DECISIONS_URL,
    ExecutionMode,
    JevDecisionError,
    Lane,
    NormalizedRoutingDecision,
    _choice,
    _choice_answer,
    _chunks,
    _json_request,
    _prepare_fast_route_record,
    fetch_catalog,
    load_policy,
    price_guard_allows,
    quota_pressure_from_remaining,
)

LEGAL_SHAPES = {"SINGLE", "PARALLEL_PAIR", "PARALLEL_TRIPLE", "SEQUENTIAL_PAIR"}


def _validate_shape(record: Mapping[str, Any]) -> str:
    shape = str(record.get("route_shape") or "")
    if shape not in LEGAL_SHAPES:
        raise JevDecisionError("invalid_primary_route_shape")
    candidates = list(record.get("candidate_models") or [])
    shared = bool(record.get("shared_mutable_state"))
    if shape == "PARALLEL_PAIR" and (shared or len(candidates) < 2):
        raise JevDecisionError("illegal_parallel_pair")
    if shape == "PARALLEL_TRIPLE" and (shared or len(candidates) < 3):
        raise JevDecisionError("illegal_parallel_triple")
    if shape == "SEQUENTIAL_PAIR" and len(candidates) < 2:
        raise JevDecisionError("illegal_sequential_pair")
    return shape


def build_primary_route_batch_request(
    *,
    model: str,
    records: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    batch = policy.get("batch_execution") or {}
    max_records = int(batch.get("max_records_per_request", 20))
    if not 1 <= len(records) <= max_records:
        raise JevDecisionError("batch_size_out_of_bounds")

    prepared = [
        _prepare_fast_route_record(record, index=i + 1, policy=policy)
        for i, record in enumerate(records)
    ]
    seen_external: set[str] = set()
    seen_internal: set[str] = set()
    questions: dict[str, Any] = {}
    state_records: list[dict[str, str]] = []

    for i, record in enumerate(prepared, 1):
        external = str(record["external_id"])
        if external in seen_external:
            raise JevDecisionError("duplicate_external_record_id")
        seen_external.add(external)
        if record["id"] in seen_internal:
            record["id"] = f"r_{i:04d}"
        seen_internal.add(record["id"])

        # _prepare_fast_route_record intentionally retains only its known fields;
        # preserve the code-determined shape from the original input.
        source = records[i - 1]
        record["route_shape"] = str(source.get("route_shape") or "")
        shape = _validate_shape(record)
        rid = str(record["id"])
        candidates = list(record["candidate_models"])
        criteria = {
            model_id: (
                "Choose when this eligible candidate's stored profile best fits "
                "the task under the already code-determined execution shape."
            )
            for model_id in candidates
        }
        questions[f"{rid}__primary_worker"] = _choice(
            criteria,
            f'For record "{rid}", choose the single best primary eligible worker.',
        )
        state_records.append({
            "id": rid,
            "record": json.dumps({
                "task_summary": record["task_summary"],
                "lane": record["lane"],
                "route_shape": shape,
                "eligible_candidate_profiles": record["candidate_profiles"],
                "quota_pressure": record["quota_pressure"],
                "hard_rules": [
                    "Choose only from eligible candidates.",
                    "Execution shape is already fixed by deterministic code.",
                    "Do not count, compute fanout, or change execution shape.",
                    "Do not expand permissions or paid scope.",
                ],
            }, ensure_ascii=False, separators=(",", ":")),
        })

    return {
        "model": model,
        "state": {
            "description": "Prevalidated routing records whose execution shape is already determined by code.",
            "records": state_records,
        },
        "questions": questions,
    }, prepared


def parse_primary_route_response(
    payload: Mapping[str, Any],
    *,
    prepared_records: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    answers = payload.get("answers")
    if not isinstance(answers, Mapping):
        raise JevDecisionError("answers_missing")
    threshold = float((policy.get("decision_contract") or {}).get("low_confidence_threshold", 0.65))
    out: dict[str, dict[str, Any]] = {}

    for record in prepared_records:
        rid = str(record["id"])
        answer = answers.get(f"{rid}__primary_worker")
        if not isinstance(answer, Mapping):
            raise JevDecisionError(f"{rid}:primary_answer_missing")
        primary, confidence, probabilities = _choice_answer(answer)
        candidates = list(record["candidate_models"])
        allowed = set(candidates)
        if primary not in allowed:
            ranked = sorted(
                ((float(prob), model) for model, prob in probabilities.items() if model in allowed),
                reverse=True,
            )
            if not ranked:
                raise JevDecisionError(f"{rid}:candidate_expansion_blocked")
            primary = ranked[0][1]

        shape = _validate_shape(record)
        selected = [primary]
        remaining = [model for model in candidates if model != primary]
        if shape in {"PARALLEL_PAIR", "SEQUENTIAL_PAIR"}:
            selected.append(remaining[0])
        elif shape == "PARALLEL_TRIPLE":
            selected.extend(remaining[:2])

        mode = {
            "SINGLE": ExecutionMode.SINGLE,
            "PARALLEL_PAIR": ExecutionMode.PARALLEL,
            "PARALLEL_TRIPLE": ExecutionMode.PARALLEL,
            "SEQUENTIAL_PAIR": ExecutionMode.SEQUENTIAL,
        }[shape]

        low = confidence < threshold
        decision = NormalizedRoutingDecision(
            schema_version="jev-primary-routing-decision-v1",
            record_id=str(record.get("external_id") or rid),
            lane=str(record.get("lane") or Lane.GENERAL_REASONING.value),
            workers=tuple(selected),
            fanout=len(selected),
            parallel=mode == ExecutionMode.PARALLEL and len(selected) > 1,
            execution_mode=mode.value,
            independent_verification=len(selected) > 1,
            action=(Action.ESCALATE if low else Action.EXECUTE).value,
            confidence=round(confidence, 6),
            low_confidence=low,
        ).to_dict()
        decision["route_shape"] = shape
        decision["composition_source"] = "PYTHON_PRECOMPUTED_SHAPE_AND_HEALTH_RANKED_ORDER"
        out[decision["record_id"]] = decision
    return out


def _request_once(
    *,
    model: str,
    api_key: str,
    records: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    body, prepared = build_primary_route_batch_request(model=model, records=records, policy=policy)
    status, payload, latency_ms = _json_request(
        DECISIONS_URL,
        method="POST",
        api_key=api_key,
        body=body,
        timeout_seconds=timeout_seconds,
    )
    if status != 200:
        raise JevDecisionError(f"http_{status}")
    decisions = parse_primary_route_response(payload, prepared_records=prepared, policy=policy)
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    return {
        "status": "JEV_PRIMARY_BATCH_OK",
        "requested_model": model,
        "response_id": payload.get("id"),
        "latency_ms": round(latency_ms, 3),
        "record_count": len(prepared),
        "question_count": len(body["questions"]),
        "questions_per_record": round(len(body["questions"]) / max(1, len(prepared)), 3),
        "decisions": decisions,
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cost": usage.get("cost"),
        },
        "paid_execution": True,
        "paid_fallback_to_other_family": False,
    }


def decide_primary_batch(
    *,
    records: Sequence[Mapping[str, Any]],
    api_key: str | None = None,
    timeout_seconds: float = 10.0,
    catalog_entries: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    policy = load_policy()
    key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return {"status": "JEV_UNAVAILABLE", "reason": "OPENROUTER_API_KEY_MISSING", "decisions": {}}
    provider = policy.get("provider") or {}
    latest = str(provider.get("canonical_model_alias") or "~typesafe/jev-latest")
    pinned = str(provider.get("last_known_good_model") or "typesafe/jev-1.13")
    try:
        catalog = list(catalog_entries) if catalog_entries is not None else fetch_catalog()
    except JevDecisionError as exc:
        return {"status": "JEV_UNAVAILABLE", "reason": str(exc), "decisions": {}}

    errors: list[dict[str, Any]] = []
    for model in [latest, pinned]:
        if model == pinned and latest == pinned:
            continue
        ok, price = price_guard_allows(model, policy=policy, entries=catalog)
        if not ok:
            errors.append({"model": model, "reason": "EMERGENCY_PRICE_GUARD_BLOCK", "price_evidence": price})
            continue
        try:
            result = _request_once(
                model=model,
                api_key=key,
                records=records,
                policy=policy,
                timeout_seconds=timeout_seconds,
            )
            result["price_evidence"] = price
            result["used_pinned_fallback"] = model == pinned
            result["prior_errors"] = errors
            return result
        except JevDecisionError as exc:
            errors.append({"model": model, "reason": str(exc), "price_evidence": price})
    return {
        "status": "JEV_UNAVAILABLE",
        "reason": "PRIMARY_LATEST_AND_PINNED_FAILED",
        "errors": errors,
        "decisions": {},
    }


def decide_many_primary(
    *,
    records: Sequence[Mapping[str, Any]],
    api_key: str | None = None,
    timeout_seconds: float = 10.0,
    catalog_entries: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if not records:
        return {"status": "JEV_PRIMARY_MANY_OK", "record_count": 0, "batch_count": 0, "decisions": {}}
    policy = load_policy()
    batch = policy.get("batch_execution") or {}
    max_records = int(batch.get("max_records_per_request", 20))
    max_parallel = max(1, int(batch.get("max_parallel_batches", 5)))
    catalog = list(catalog_entries) if catalog_entries is not None else None
    if catalog is None:
        try:
            catalog = fetch_catalog()
        except JevDecisionError as exc:
            return {"status": "JEV_UNAVAILABLE", "reason": str(exc), "decisions": {}}

    chunks = _chunks(records, max_records)
    decisions: dict[str, Any] = {}
    failures: list[dict[str, Any]] = []
    latencies: list[float] = []
    costs = 0.0
    question_count = 0

    def run(chunk: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return decide_primary_batch(
            records=chunk,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            catalog_entries=catalog,
        )

    with ThreadPoolExecutor(max_workers=min(max_parallel, len(chunks))) as pool:
        future_map = {pool.submit(run, chunk): i for i, chunk in enumerate(chunks)}
        for future in as_completed(future_map):
            i = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                failures.append({"batch_index": i, "reason": type(exc).__name__})
                continue
            if result.get("status") == "JEV_PRIMARY_BATCH_OK":
                decisions.update(result.get("decisions") or {})
                latencies.append(float(result.get("latency_ms") or 0.0))
                question_count += int(result.get("question_count") or 0)
                usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
                try:
                    costs += float(usage.get("cost") or 0.0)
                except (TypeError, ValueError):
                    pass
            else:
                failures.append({"batch_index": i, "reason": result.get("reason") or result.get("status")})

    return {
        "status": "JEV_PRIMARY_MANY_OK" if not failures else ("JEV_PRIMARY_MANY_PARTIAL" if decisions else "JEV_UNAVAILABLE"),
        "record_count": len(records),
        "batch_count": len(chunks),
        "parallel_batch_count": min(max_parallel, len(chunks)),
        "question_count": question_count,
        "questions_per_record": round(question_count / max(1, len(records)), 3),
        "decisions": decisions,
        "failed_batches": failures,
        "max_batch_latency_ms": max(latencies) if latencies else None,
        "estimated_total_cost": costs,
    }


def decide_primary(
    *,
    task_summary: str,
    candidate_models: Sequence[str],
    remaining_free_quota: int,
    route_shape: str,
    candidate_profiles: Mapping[str, str] | None = None,
    lane: str = Lane.GENERAL_REASONING.value,
    shared_mutable_state: bool = False,
    api_key: str | None = None,
    timeout_seconds: float = 10.0,
    catalog_entries: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    result = decide_primary_batch(
        records=[{
            "id": "r_0001",
            "task_summary": task_summary,
            "candidate_models": list(candidate_models),
            "candidate_profiles": dict(candidate_profiles or {}),
            "quota_pressure": quota_pressure_from_remaining(remaining_free_quota).value,
            "lane": lane,
            "allow_third": route_shape == "PARALLEL_TRIPLE",
            "shared_mutable_state": shared_mutable_state,
            "high_risk": False,
            "route_shape": route_shape,
        }],
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        catalog_entries=catalog_entries,
    )
    if result.get("status") != "JEV_PRIMARY_BATCH_OK":
        return result
    return {
        **result,
        "status": "JEV_PRIMARY_DECISION_OK",
        "decision": (result.get("decisions") or {}).get("r_0001"),
    }


__all__ = [
    "LEGAL_SHAPES",
    "build_primary_route_batch_request",
    "decide_many_primary",
    "decide_primary",
    "decide_primary_batch",
    "parse_primary_route_response",
]
