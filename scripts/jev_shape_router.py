#!/usr/bin/env python3
"""One-question Jev routing for health-ranked primary workers.

When deterministic task-fit + recent worker health makes the primary worker
unambiguous, Jev decides only the fuzzy execution shape. Python keeps the
health-ranked primary at position zero and composes complements from the
already-prevalidated candidate order.

This follows TypeSafe's System One workflow guidance: deterministic ranking,
counting, arithmetic and safety constraints remain in code; Jev answers one
narrow typed Choice.
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
    _fast_route_shapes,
    _json_request,
    _prepare_fast_route_record,
    fetch_catalog,
    load_policy,
    price_guard_allows,
    quota_pressure_from_remaining,
)


def build_shape_route_batch_request(
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

        rid = str(record["id"])
        candidates = list(record["candidate_models"])
        if not candidates:
            raise JevDecisionError("no_candidate_models")

        shapes = _fast_route_shapes(bool(record.get("allow_third")))
        if record.get("shared_mutable_state"):
            shapes.pop("PARALLEL_PAIR", None)
            shapes.pop("PARALLEL_TRIPLE", None)
        if record.get("high_risk"):
            shapes["ESCALATE"] = (
                "Return to the commander when autonomous execution is too "
                "ambiguous or risky."
            )

        questions[f"{rid}__route_shape"] = _choice(
            shapes,
            f'For record "{rid}", choose the smallest safe execution shape that preserves verified correctness; use wall-clock time only after reliability.',
        )
        state_records.append({
            "id": rid,
            "record": json.dumps({
                "task_summary": record["task_summary"],
                "lane": record["lane"],
                "primary_worker": candidates[0],
                "eligible_candidate_order": candidates,
                "primary_selection_basis": (
                    "The first worker is already selected by deterministic task-fit "
                    "and short-lived empirical worker health."
                ),
                "quota_pressure": record["quota_pressure"],
                "shared_mutable_state": record["shared_mutable_state"],
                "high_risk": record["high_risk"],
                "hard_rules": [
                    "Do not change primary_worker.",
                    "Choose only the execution shape.",
                    "Prefer verified correctness and decision stability over routing latency.",
                    "Code chooses complements from eligible_candidate_order.",
                    "Do not count workers or perform arithmetic.",
                    "Do not expand permissions or paid scope.",
                ],
            }, ensure_ascii=False, separators=(",", ":")),
        })

    return {
        "model": model,
        "state": {
            "description": (
                "Prevalidated AI Army routing records with a code-selected "
                "health-ranked primary worker. Jev decides only execution shape."
            ),
            "records": state_records,
        },
        "questions": questions,
    }, prepared


def parse_shape_route_response(
    payload: Mapping[str, Any],
    *,
    prepared_records: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    answers = payload.get("answers")
    if not isinstance(answers, Mapping):
        raise JevDecisionError("answers_missing")
    threshold = float(
        (policy.get("decision_contract") or {}).get("low_confidence_threshold", 0.65)
    )
    out: dict[str, dict[str, Any]] = {}

    for record in prepared_records:
        rid = str(record["id"])
        answer = answers.get(f"{rid}__route_shape")
        if not isinstance(answer, Mapping):
            raise JevDecisionError(f"{rid}:shape_answer_missing")
        shape, confidence, probabilities = _choice_answer(answer)

        valid_shapes = set(_fast_route_shapes(bool(record.get("allow_third"))))
        if record.get("shared_mutable_state"):
            valid_shapes.discard("PARALLEL_PAIR")
            valid_shapes.discard("PARALLEL_TRIPLE")
        if record.get("high_risk"):
            valid_shapes.add("ESCALATE")

        if shape not in valid_shapes:
            ranked = sorted(
                (
                    (float(prob), candidate)
                    for candidate, prob in probabilities.items()
                    if candidate in valid_shapes
                ),
                reverse=True,
            )
            if not ranked:
                raise JevDecisionError(f"{rid}:invalid_route_shape")
            shape = ranked[0][1]

        candidates = [str(x) for x in record["candidate_models"]]
        if not candidates:
            raise JevDecisionError(f"{rid}:no_candidate_models")
        selected = [candidates[0]]
        if shape in {"PARALLEL_PAIR", "SEQUENTIAL_PAIR", "PARALLEL_TRIPLE"} and len(candidates) >= 2:
            selected.append(candidates[1])
        if shape == "PARALLEL_TRIPLE" and len(candidates) >= 3:
            selected.append(candidates[2])

        if shape == "ESCALATE":
            action = Action.ESCALATE
            selected = selected[:1]
            mode = ExecutionMode.SINGLE
            parallel = False
        elif shape == "SEQUENTIAL_PAIR":
            action = Action.EXECUTE
            mode = ExecutionMode.SEQUENTIAL
            parallel = False
        elif shape in {"PARALLEL_PAIR", "PARALLEL_TRIPLE"} and len(selected) > 1:
            action = Action.EXECUTE
            mode = ExecutionMode.PARALLEL
            parallel = True
        else:
            action = Action.EXECUTE
            mode = ExecutionMode.SINGLE
            parallel = False

        low = confidence < threshold
        if low:
            action = Action.ESCALATE

        decision = NormalizedRoutingDecision(
            schema_version="jev-shape-routing-decision-v1",
            record_id=str(record.get("external_id") or rid),
            lane=str(record.get("lane") or Lane.GENERAL_REASONING.value),
            workers=tuple(selected),
            fanout=len(selected),
            parallel=parallel,
            execution_mode=mode.value,
            independent_verification=len(selected) > 1,
            action=action.value,
            confidence=round(confidence, 6),
            low_confidence=low,
        ).to_dict()
        decision["route_shape"] = shape
        decision["composition_source"] = (
            "PYTHON_HEALTH_RANKED_PRIMARY_AND_COMPLEMENTS__JEV_SHAPE_ONLY"
        )
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
    body, prepared = build_shape_route_batch_request(
        model=model,
        records=records,
        policy=policy,
    )
    status, payload, latency_ms = _json_request(
        DECISIONS_URL,
        method="POST",
        api_key=api_key,
        body=body,
        timeout_seconds=timeout_seconds,
    )
    if status != 200:
        raise JevDecisionError(f"http_{status}")
    decisions = parse_shape_route_response(
        payload,
        prepared_records=prepared,
        policy=policy,
    )
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    return {
        "status": "JEV_SHAPE_BATCH_OK",
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


def decide_shape_batch(
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
            errors.append({
                "model": model,
                "reason": "EMERGENCY_PRICE_GUARD_BLOCK",
                "price_evidence": price,
            })
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
            errors.append({
                "model": model,
                "reason": str(exc),
                "price_evidence": price,
            })

    return {
        "status": "JEV_UNAVAILABLE",
        "reason": "SHAPE_LATEST_AND_PINNED_FAILED",
        "errors": errors,
        "decisions": {},
    }


def decide_many_shape(
    *,
    records: Sequence[Mapping[str, Any]],
    api_key: str | None = None,
    timeout_seconds: float = 10.0,
    catalog_entries: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if not records:
        return {
            "status": "JEV_SHAPE_MANY_OK",
            "record_count": 0,
            "batch_count": 0,
            "decisions": {},
        }

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
    total_cost = 0.0
    total_questions = 0

    def run(chunk: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return decide_shape_batch(
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
            if result.get("status") == "JEV_SHAPE_BATCH_OK":
                decisions.update(result.get("decisions") or {})
                latencies.append(float(result.get("latency_ms") or 0.0))
                total_questions += int(result.get("question_count") or 0)
                usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
                try:
                    total_cost += float(usage.get("cost") or 0.0)
                except (TypeError, ValueError):
                    pass
            else:
                failures.append({
                    "batch_index": i,
                    "reason": result.get("reason") or result.get("status"),
                })

    return {
        "status": (
            "JEV_SHAPE_MANY_OK"
            if not failures
            else ("JEV_SHAPE_MANY_PARTIAL" if decisions else "JEV_UNAVAILABLE")
        ),
        "record_count": len(records),
        "batch_count": len(chunks),
        "parallel_batch_count": min(max_parallel, len(chunks)),
        "question_count": total_questions,
        "questions_per_record": round(total_questions / max(1, len(records)), 3),
        "decisions": decisions,
        "failed_batches": failures,
        "max_batch_latency_ms": max(latencies) if latencies else None,
        "estimated_total_cost": total_cost,
    }


def decide_shape(
    *,
    task_summary: str,
    candidate_models: Sequence[str],
    remaining_free_quota: int,
    candidate_profiles: Mapping[str, str] | None = None,
    lane: str = Lane.GENERAL_REASONING.value,
    allow_third: bool = False,
    shared_mutable_state: bool = False,
    high_risk: bool = False,
    api_key: str | None = None,
    timeout_seconds: float = 10.0,
    catalog_entries: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    result = decide_shape_batch(
        records=[{
            "id": "r_0001",
            "task_summary": task_summary,
            "candidate_models": list(candidate_models),
            "candidate_profiles": dict(candidate_profiles or {}),
            "quota_pressure": quota_pressure_from_remaining(remaining_free_quota).value,
            "lane": lane,
            "allow_third": allow_third,
            "shared_mutable_state": shared_mutable_state,
            "high_risk": high_risk,
        }],
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        catalog_entries=catalog_entries,
    )
    if result.get("status") != "JEV_SHAPE_BATCH_OK":
        return result
    return {
        **result,
        "status": "JEV_SHAPE_DECISION_OK",
        "decision": (result.get("decisions") or {}).get("r_0001"),
    }


__all__ = [
    "build_shape_route_batch_request",
    "decide_many_shape",
    "decide_shape",
    "decide_shape_batch",
    "parse_shape_route_response",
]
