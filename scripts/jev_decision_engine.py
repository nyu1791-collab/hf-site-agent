#!/usr/bin/env python3
"""Typed, batch-first Jev System-One decision engine.

Jev is a machine decision plane, not a text generator. This runtime uses only
OpenRouter's dedicated Decisions API and only typed Choice/Noul/Score answers.
The model never emits the final routing JSON. Python validates the typed
answers, resolves duplicates, performs all counting/arithmetic, and constructs
the final immutable routing object.

Independent routing records are batched up to 20 per Decisions request. Multiple
20-record chunks may run concurrently. This minimizes routing wall-clock time
before downstream workers start.

ChatGPT remains final authority. Jev cannot expand candidate models, permissions,
paid-worker scope, deployment, publication, merge, payment, or secret access.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from enum import Enum
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "jev_decision_engine_policy.json"
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
CATALOG_URL = "https://openrouter.ai/api/v1/models"
_RECORD_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


class JevDecisionError(RuntimeError):
    pass


class Lane(str, Enum):
    GENERAL_REASONING = "GENERAL_REASONING"
    CODING_ENGINEERING = "CODING_ENGINEERING"
    FAST_CLASSIFICATION_EXTRACTION = "FAST_CLASSIFICATION_EXTRACTION"
    VISION_AND_MEDIA_UNDERSTANDING = "VISION_AND_MEDIA_UNDERSTANDING"
    LONG_CONTEXT_ORCHESTRATION = "LONG_CONTEXT_ORCHESTRATION"
    FINANCE_DATA = "FINANCE_DATA"


class Action(str, Enum):
    EXECUTE = "EXECUTE"
    STOP = "STOP"
    RETRY = "RETRY"
    ESCALATE = "ESCALATE"


class ExecutionMode(str, Enum):
    SINGLE = "SINGLE"
    PARALLEL = "PARALLEL"
    SEQUENTIAL = "SEQUENTIAL"


class QuotaPressure(str, Enum):
    AMPLE = "AMPLE"
    LIMITED = "LIMITED"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class NormalizedRoutingDecision:
    schema_version: str
    record_id: str
    lane: str
    workers: tuple[str, ...]
    fanout: int
    parallel: bool
    execution_mode: str
    independent_verification: bool
    action: str
    confidence: float
    low_confidence: bool

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["workers"] = list(self.workers)
        return value


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise JevDecisionError("invalid_jev_policy")
    return value


def quota_pressure_from_remaining(remaining_free_quota: int) -> QuotaPressure:
    remaining = max(0, int(remaining_free_quota))
    if remaining <= 5:
        return QuotaPressure.CRITICAL
    if remaining <= 30:
        return QuotaPressure.LIMITED
    return QuotaPressure.AMPLE


def _bounded_text(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise JevDecisionError("empty_task_summary")
    if len(text) > max_chars:
        raise JevDecisionError("jev_prompt_too_large")
    return text


def _safe_record_id(value: Any, index: int) -> str:
    raw = str(value or "").strip().lower()
    raw = re.sub(r"[^a-z0-9_]+", "_", raw).strip("_")
    if not raw or not raw[0].isalpha():
        raw = f"r_{raw}" if raw else f"r_{index:04d}"
    raw = raw[:48]
    if not _RECORD_ID_RE.fullmatch(raw):
        return f"r_{index:04d}"
    return raw


def _json_request(
    url: str,
    *,
    method: str = "GET",
    api_key: str = "",
    body: Mapping[str, Any] | None = None,
    timeout_seconds: float = 10.0,
) -> tuple[int, dict[str, Any], float]:
    data = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "hf-site-agent-jev-decision-engine/2.0",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["HTTP-Referer"] = "https://github.com/nyu1791-collab/hf-site-agent"
        headers["X-Title"] = "hf-site-agent-jev-decision-engine"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(4_000_000).decode("utf-8", errors="replace")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read(200_000).decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"error": raw[:1000]}
        return int(exc.code), payload if isinstance(payload, dict) else {}, (time.perf_counter() - started) * 1000.0
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise JevDecisionError("network_or_timeout") from exc
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise JevDecisionError("invalid_json_response") from exc
    if not isinstance(payload, dict):
        raise JevDecisionError("invalid_response_object")
    return status, payload, (time.perf_counter() - started) * 1000.0


def _price_per_million(raw_per_token: Any) -> float | None:
    try:
        return float(raw_per_token) * 1_000_000.0
    except (TypeError, ValueError):
        return None


def fetch_catalog(timeout_seconds: float = 8.0) -> list[dict[str, Any]]:
    status, payload, _ = _json_request(CATALOG_URL, timeout_seconds=timeout_seconds)
    if status != 200:
        raise JevDecisionError(f"catalog_http_{status}")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise JevDecisionError("catalog_missing_data")
    return [dict(x) for x in rows if isinstance(x, Mapping)]


def _catalog_entry_for_model(entries: Sequence[Mapping[str, Any]], model: str) -> Mapping[str, Any] | None:
    for entry in entries:
        if str(entry.get("id") or "") == model:
            return entry
    return None


def price_guard_allows(
    model: str,
    *,
    policy: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
) -> tuple[bool, dict[str, Any]]:
    guard = policy.get("cost_guard") or {}
    hard_prompt = float(guard.get("hard_emergency_price_ceiling_prompt_usd_per_million", 1.0))
    hard_completion = float(guard.get("hard_emergency_price_ceiling_completion_usd_per_million", 1.0))
    soft_prompt = float(guard.get("soft_price_observation_prompt_usd_per_million", 0.10))
    entry = _catalog_entry_for_model(entries, model)
    if entry is None and model.startswith("~"):
        pinned = str((policy.get("provider") or {}).get("last_known_good_model") or "")
        entry = _catalog_entry_for_model(entries, pinned)
    pricing = entry.get("pricing") if isinstance(entry, Mapping) else None
    evidence_source = "NORMAL_MODELS_CATALOG"
    if isinstance(pricing, Mapping):
        prompt = _price_per_million(pricing.get("prompt"))
        completion = _price_per_million(pricing.get("completion"))
    else:
        prompt = completion = None

    if prompt is None or completion is None:
        provider = policy.get("provider") or {}
        authorized_models = {
            str(provider.get("canonical_model_alias") or ""),
            str(provider.get("last_known_good_model") or ""),
        }
        if model not in authorized_models:
            return False, {"reason": "PRICE_EVIDENCE_UNAVAILABLE", "model": model}
        prompt = float(guard.get("current_observed_prompt_usd_per_million", 999.0))
        completion = float(guard.get("current_observed_completion_usd_per_million", 999.0))
        evidence_source = "AUTHORIZED_JEV_POLICY_OBSERVATION"

    allowed = prompt <= hard_prompt and completion <= hard_completion
    return allowed, {
        "model": model,
        "evidence_source": evidence_source,
        "observed_prompt_usd_per_million": prompt,
        "observed_completion_usd_per_million": completion,
        "soft_price_observation_prompt_usd_per_million": soft_prompt,
        "soft_price_warning": prompt > soft_prompt,
        "hard_prompt_usd_per_million": hard_prompt,
        "hard_completion_usd_per_million": hard_completion,
        "allowed": allowed,
    }


def _choice(criteria: Mapping[str, str], instructions: str) -> dict[str, Any]:
    return {"type": "choice", "instructions": instructions, "criteria": dict(criteria)}


def _noul(instructions: str, true_when: str, false_when: str) -> dict[str, Any]:
    return {
        "type": "noul",
        "instructions": instructions,
        "criteria": {"true": true_when, "false": false_when},
    }


def _lane_criteria() -> dict[str, str]:
    return {
        Lane.GENERAL_REASONING.value: "General reasoning, planning, synthesis, or research.",
        Lane.CODING_ENGINEERING.value: "Coding, debugging, software engineering, implementation, or testing.",
        Lane.FAST_CLASSIFICATION_EXTRACTION.value: "Narrow classification, extraction, tagging, or fast structured work.",
        Lane.VISION_AND_MEDIA_UNDERSTANDING.value: "Image, video, or multimodal understanding.",
        Lane.LONG_CONTEXT_ORCHESTRATION.value: "Long-context planning, orchestration, or large-context synthesis.",
        Lane.FINANCE_DATA.value: "Finance-oriented or structured data-analysis work.",
    }


def _action_criteria() -> dict[str, str]:
    return {
        Action.EXECUTE.value: "Typed candidate set and task state are sufficient to proceed.",
        Action.STOP.value: "Execution should stop because constraints make the route inappropriate.",
        Action.RETRY.value: "Retry only with a materially changed method after a prior failure.",
        Action.ESCALATE.value: "Decision is ambiguous or risky enough to require ChatGPT adjudication.",
    }


def _prepare_record(
    record: Mapping[str, Any],
    *,
    index: int,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    guard = policy.get("cost_guard") or {}
    max_chars = int(guard.get("max_prompt_chars_per_record", 12000))
    external_id = str(record.get("id") or f"task_{index:04d}")
    record_id = _safe_record_id(external_id, index)
    summary = _bounded_text(record.get("task_summary") or record.get("objective"), max_chars)
    candidates = [str(x) for x in (record.get("candidate_models") or []) if str(x).strip()][:12]
    if not candidates:
        raise JevDecisionError(f"{record_id}:no_candidate_models")
    profiles_in = record.get("candidate_profiles")
    profiles_in = profiles_in if isinstance(profiles_in, Mapping) else {}
    profiles = {m: str(profiles_in.get(m) or "Eligible specialist candidate.")[:1200] for m in candidates}
    pressure_raw = str(record.get("quota_pressure") or QuotaPressure.AMPLE.value).upper()
    try:
        pressure = QuotaPressure(pressure_raw)
    except ValueError as exc:
        raise JevDecisionError(f"{record_id}:invalid_quota_pressure") from exc
    return {
        "id": record_id,
        "external_id": external_id,
        "task_summary": summary,
        "candidate_models": candidates,
        "candidate_profiles": profiles,
        "quota_pressure": pressure.value,
    }


def _questions_for_record(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    record_id = str(record["id"])
    candidates = list(record["candidate_models"])
    profiles = record["candidate_profiles"]
    model_criteria = {
        model_id: f"Best fit when this candidate's verified profile matches the work: {profiles[model_id]}"
        for model_id in candidates
    }
    prefix = f"{record_id}__"
    return {
        prefix + "lane": _choice(_lane_criteria(), f'For record "{record_id}", choose the best specialist lane.'),
        prefix + "primary_model": _choice(model_criteria, f'For record "{record_id}", choose the best primary eligible model.'),
        prefix + "use_second_model": _noul(
            f'For record "{record_id}", would a second eligible model materially improve total system value?',
            "Independent parallel work, complementary specialization, or meaningful verification benefit exceeds coordination overhead.",
            "One model is sufficient, work is sequential/shared-state, or an extra model adds little value.",
        ),
        prefix + "secondary_model": _choice(
            model_criteria,
            f'For record "{record_id}", choose the strongest complementary eligible model if a second model is useful.',
        ),
        prefix + "use_third_model": _noul(
            f'For record "{record_id}", would a third eligible model add material value beyond two?',
            "A distinct third workstream or specialty materially improves speed, coverage, or verification.",
            "A third model duplicates work or adds coordination without enough benefit.",
        ),
        prefix + "tertiary_model": _choice(
            model_criteria,
            f'For record "{record_id}", choose the strongest additional complementary eligible model if a third is useful.',
        ),
        prefix + "parallelize": _noul(
            f'For record "{record_id}", should selected models execute in parallel?',
            "Selected workstreams are independent/read-only and parallel execution materially reduces latency or improves coverage.",
            "There are dependencies, shared mutable state, or sequential ordering requirements.",
        ),
        prefix + "independent_verification": _noul(
            f'For record "{record_id}", is an independent verification lane valuable?',
            "The output is high impact/high uncertainty or a distinct verifier materially reduces escaped-defect risk.",
            "A machine oracle exists or another reviewer adds little value.",
        ),
        prefix + "action": _choice(_action_criteria(), f'For record "{record_id}", choose the routing control-plane action.'),
    }


def build_batch_decisions_request(
    *,
    model: str,
    records: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    batch = policy.get("batch_execution") or {}
    max_records = int(batch.get("max_records_per_request", 20))
    if not 1 <= len(records) <= max_records:
        raise JevDecisionError("batch_size_out_of_bounds")
    prepared = [_prepare_record(record, index=i + 1, policy=policy) for i, record in enumerate(records)]
    external_ids = [str(x["external_id"]) for x in prepared]
    if len(set(external_ids)) != len(external_ids):
        raise JevDecisionError("duplicate_external_record_id")
    seen_ids: set[str] = set()
    for index, record in enumerate(prepared, 1):
        if record["id"] in seen_ids:
            record["id"] = f"r_{index:04d}"
        seen_ids.add(record["id"])
    questions: dict[str, Any] = {}
    state_records: list[dict[str, Any]] = []
    for record in prepared:
        questions.update(_questions_for_record(record))
        state_records.append({
            "id": record["id"],
            "record": json.dumps({
                "task_summary": record["task_summary"],
                "eligible_candidate_profiles": record["candidate_profiles"],
                "quota_pressure": record["quota_pressure"],
                "hard_rules": [
                    "Choose only from eligible_candidate_profiles.",
                    "Prefer the smallest team that maximizes quality and wall-clock efficiency.",
                    "Parallelize only independent work.",
                    "Do not authorize any paid worker or permission expansion.",
                ],
            }, ensure_ascii=False, separators=(",", ":")),
        })
    return {
        "model": model,
        "state": {
            "description": "One AI Army routing record with prevalidated eligible workers and precomputed quota pressure.",
            "records": state_records,
        },
        "questions": questions,
    }, prepared


def build_decisions_request(
    *,
    model: str,
    task_summary: str,
    candidate_models: Sequence[str],
    remaining_free_quota: int,
    policy: Mapping[str, Any],
    candidate_profiles: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    body, _ = build_batch_decisions_request(
        model=model,
        policy=policy,
        records=[{
            "id": "r_0001",
            "task_summary": task_summary,
            "candidate_models": list(candidate_models),
            "candidate_profiles": dict(candidate_profiles or {}),
            "quota_pressure": quota_pressure_from_remaining(remaining_free_quota).value,
        }],
    )
    return body


def _choice_answer(answer: Mapping[str, Any]) -> tuple[str, float, dict[str, float]]:
    if str(answer.get("type") or "") != "choice":
        raise JevDecisionError("expected_choice_answer")
    choice = str(answer.get("choice") or "")
    confidence = float(answer.get("confidence", 0.0) or 0.0)
    probabilities_raw = answer.get("probabilities")
    probabilities: dict[str, float] = {}
    if isinstance(probabilities_raw, Mapping):
        for key, value in probabilities_raw.items():
            try:
                prob = float(value)
            except (TypeError, ValueError):
                continue
            if 0.0 <= prob <= 1.0:
                probabilities[str(key)] = prob
    if not choice or not 0.0 <= confidence <= 1.0:
        raise JevDecisionError("invalid_choice_answer")
    return choice, confidence, probabilities


def _noul_answer(answer: Mapping[str, Any]) -> tuple[float, float]:
    if str(answer.get("type") or "") != "noul":
        raise JevDecisionError("expected_noul_answer")
    probability = float(answer.get("noul", answer.get("probability", -1.0)))
    if not 0.0 <= probability <= 1.0:
        raise JevDecisionError("invalid_noul_answer")
    confidence = abs(probability - 0.5) * 2.0
    return probability, confidence


def _best_distinct(
    chosen: str,
    probabilities: Mapping[str, float],
    *,
    used: set[str],
    allowed: set[str],
) -> tuple[str | None, float]:
    if chosen in allowed and chosen not in used:
        return chosen, float(probabilities.get(chosen, 1.0))
    ranked = sorted(
        ((float(prob), model) for model, prob in probabilities.items() if model in allowed and model not in used),
        reverse=True,
    )
    if ranked:
        prob, model = ranked[0]
        return model, prob
    return None, 0.0


def _parse_record(
    answers: Mapping[str, Any],
    record: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> NormalizedRoutingDecision:
    rid = str(record["id"])
    prefix = f"{rid}__"
    required = [
        "lane", "primary_model", "use_second_model", "secondary_model",
        "use_third_model", "tertiary_model", "parallelize",
        "independent_verification", "action",
    ]
    if any(prefix + key not in answers for key in required):
        raise JevDecisionError(f"{rid}:answers_incomplete")

    lane, lane_conf, _ = _choice_answer(answers[prefix + "lane"])
    primary, primary_conf, primary_probs = _choice_answer(answers[prefix + "primary_model"])
    second_prob, second_need_conf = _noul_answer(answers[prefix + "use_second_model"])
    secondary, secondary_conf, secondary_probs = _choice_answer(answers[prefix + "secondary_model"])
    third_prob, third_need_conf = _noul_answer(answers[prefix + "use_third_model"])
    tertiary, tertiary_conf, tertiary_probs = _choice_answer(answers[prefix + "tertiary_model"])
    parallel_prob, parallel_conf = _noul_answer(answers[prefix + "parallelize"])
    verify_prob, verify_conf = _noul_answer(answers[prefix + "independent_verification"])
    action, action_conf, _ = _choice_answer(answers[prefix + "action"])

    try:
        lane_enum = Lane(lane)
        action_enum = Action(action)
    except ValueError as exc:
        raise JevDecisionError(f"{rid}:enum_contract_failed") from exc

    allowed = set(str(x) for x in record["candidate_models"])
    if primary not in allowed:
        primary_alt, _ = _best_distinct(primary, primary_probs, used=set(), allowed=allowed)
        if primary_alt is None:
            raise JevDecisionError(f"{rid}:candidate_expansion_blocked")
        primary = primary_alt

    contract = policy.get("decision_contract") or {}
    second_threshold = float(contract.get("second_worker_probability_threshold", 0.55))
    third_threshold = float(contract.get("third_worker_probability_threshold", 0.65))
    parallel_threshold = float(contract.get("parallel_probability_threshold", 0.55))
    verify_threshold = float(contract.get("independent_verification_probability_threshold", 0.60))

    selected = [primary]
    used = {primary}
    selected_confidences = [primary_conf]

    if second_prob >= second_threshold and len(allowed) >= 2:
        model, prob = _best_distinct(secondary, secondary_probs, used=used, allowed=allowed)
        if model:
            selected.append(model)
            used.add(model)
            selected_confidences.append(min(secondary_conf, prob if prob > 0 else secondary_conf))

    if third_prob >= third_threshold and len(selected) >= 2 and len(allowed) >= 3:
        model, prob = _best_distinct(tertiary, tertiary_probs, used=used, allowed=allowed)
        if model:
            selected.append(model)
            used.add(model)
            selected_confidences.append(min(tertiary_conf, prob if prob > 0 else tertiary_conf))

    parallel = len(selected) > 1 and parallel_prob >= parallel_threshold
    mode = (
        ExecutionMode.SINGLE if len(selected) == 1
        else ExecutionMode.PARALLEL if parallel
        else ExecutionMode.SEQUENTIAL
    )
    verify = verify_prob >= verify_threshold

    # Only route-critical ambiguity escalates. Fanout/parallel uncertainty is
    # intentionally resolved by Python thresholds so Jev stays a fast lane.
    critical_confidence = min(lane_conf, primary_conf, action_conf)
    quality_confidence = min([critical_confidence, *selected_confidences]) if selected_confidences else critical_confidence
    low_threshold = float(contract.get("low_confidence_threshold", 0.65))
    low_confidence = critical_confidence < low_threshold
    if low_confidence:
        action_enum = Action.ESCALATE

    return NormalizedRoutingDecision(
        schema_version="jev-routing-decision-v3",
        record_id=str(record.get("external_id") or rid),
        lane=lane_enum.value,
        workers=tuple(selected),
        fanout=len(selected),
        parallel=parallel,
        execution_mode=mode.value,
        independent_verification=verify,
        action=action_enum.value,
        confidence=round(quality_confidence, 6),
        low_confidence=low_confidence,
    )


def parse_batch_decisions_response(
    payload: Mapping[str, Any],
    *,
    prepared_records: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    answers = payload.get("answers")
    if not isinstance(answers, Mapping):
        raise JevDecisionError("answers_missing")
    output: dict[str, dict[str, Any]] = {}
    for record in prepared_records:
        decision = _parse_record(answers, record, policy)
        output[decision.record_id] = decision.to_dict()
    return output


def parse_decisions_response(
    payload: Mapping[str, Any],
    *,
    candidate_models: Sequence[str],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    prepared = [_prepare_record({
        "id": "r_0001",
        "task_summary": "compatibility record",
        "candidate_models": list(candidate_models),
        "candidate_profiles": {},
        "quota_pressure": QuotaPressure.AMPLE.value,
    }, index=1, policy=policy)]
    return parse_batch_decisions_response(
        payload,
        prepared_records=prepared,
        policy=policy,
    )["r_0001"]


def _request_batch_once(
    *,
    model: str,
    api_key: str,
    records: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    body, prepared = build_batch_decisions_request(model=model, records=records, policy=policy)
    status, payload, latency_ms = _json_request(
        DECISIONS_URL,
        method="POST",
        api_key=api_key,
        body=body,
        timeout_seconds=timeout_seconds,
    )
    if status != 200:
        error = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
        code = str(error.get("code") or payload.get("code") or "")[:80]
        message = str(error.get("message") or payload.get("message") or "")[:180]
        detail = ":".join(x for x in (code, message) if x)
        raise JevDecisionError(f"http_{status}" + (f":{detail}" if detail else ""))
    decisions = parse_batch_decisions_response(payload, prepared_records=prepared, policy=policy)
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    observed_cost = usage.get("cost")
    return {
        "status": "JEV_BATCH_OK",
        "requested_model": model,
        "response_id": payload.get("id"),
        "latency_ms": round(latency_ms, 3),
        "record_count": len(prepared),
        "decisions": decisions,
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cost": observed_cost,
        },
        "cost_audit": {
            "post_request_usage_cost_present": observed_cost is not None,
            "hard_emergency_guard_is_family_price_based": True,
        },
        "paid_execution": True,
        "paid_fallback_to_other_family": False,
    }


def decide_batch(
    *,
    records: Sequence[Mapping[str, Any]],
    api_key: str | None = None,
    timeout_seconds: float = 10.0,
    catalog_entries: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    policy = load_policy()
    key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return {
            "status": "JEV_UNAVAILABLE",
            "reason": "OPENROUTER_API_KEY_MISSING",
            "record_ids": [str(x.get("id") or "") for x in records],
            "fallback": "DETERMINISTIC_OR_CHATGPT_ROUTING",
            "paid_execution": False,
        }
    provider = policy.get("provider") or {}
    latest = str(provider.get("canonical_model_alias") or "~typesafe/jev-latest")
    pinned = str(provider.get("last_known_good_model") or "typesafe/jev-1.13")
    try:
        catalog = list(catalog_entries) if catalog_entries is not None else fetch_catalog()
    except JevDecisionError as exc:
        return {
            "status": "JEV_UNAVAILABLE",
            "reason": str(exc),
            "record_ids": [str(x.get("id") or "") for x in records],
            "fallback": "DETERMINISTIC_OR_CHATGPT_ROUTING",
            "paid_execution": False,
        }

    errors: list[dict[str, Any]] = []
    for model in [latest, pinned]:
        if model == pinned and latest == pinned:
            continue
        price_ok, price_evidence = price_guard_allows(model, policy=policy, entries=catalog)
        if not price_ok:
            errors.append({"model": model, "reason": "EMERGENCY_PRICE_GUARD_BLOCK", "price_evidence": price_evidence})
            continue
        try:
            result = _request_batch_once(
                model=model,
                api_key=key,
                records=records,
                policy=policy,
                timeout_seconds=timeout_seconds,
            )
            result["price_evidence"] = price_evidence
            result["used_pinned_fallback"] = model == pinned
            result["prior_errors"] = errors
            return result
        except JevDecisionError as exc:
            errors.append({"model": model, "reason": str(exc), "price_evidence": price_evidence})
    return {
        "status": "JEV_UNAVAILABLE",
        "reason": "LATEST_AND_PINNED_FAILED_OR_EMERGENCY_PRICE_BLOCKED",
        "record_ids": [str(x.get("id") or "") for x in records],
        "errors": errors,
        "fallback": "DETERMINISTIC_OR_CHATGPT_ROUTING",
        "paid_execution": False,
    }


def _chunks(values: Sequence[Mapping[str, Any]], size: int) -> list[list[Mapping[str, Any]]]:
    return [list(values[i:i + size]) for i in range(0, len(values), size)]


def decide_many(
    *,
    records: Sequence[Mapping[str, Any]],
    api_key: str | None = None,
    timeout_seconds: float = 10.0,
    catalog_entries: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Route many independent jobs with up to 20 records/request and parallel chunks."""
    if not records:
        return {"status": "JEV_MANY_OK", "record_count": 0, "batch_count": 0, "decisions": {}}
    policy = load_policy()
    batch = policy.get("batch_execution") or {}
    max_records = int(batch.get("max_records_per_request", 20))
    max_parallel = max(1, int(batch.get("max_parallel_batches", 5)))
    catalog = list(catalog_entries) if catalog_entries is not None else None
    if catalog is None:
        try:
            catalog = fetch_catalog()
        except JevDecisionError as exc:
            return {
                "status": "JEV_UNAVAILABLE",
                "reason": str(exc),
                "record_count": len(records),
                "fallback": "DETERMINISTIC_OR_CHATGPT_ROUTING",
                "decisions": {},
            }

    chunks = _chunks(records, max_records)
    decisions: dict[str, Any] = {}
    failures: list[dict[str, Any]] = []
    latencies: list[float] = []
    total_cost = 0.0

    def run(chunk: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return decide_batch(
            records=chunk,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            catalog_entries=catalog,
        )

    with ThreadPoolExecutor(max_workers=min(max_parallel, len(chunks))) as pool:
        future_map = {pool.submit(run, chunk): idx for idx, chunk in enumerate(chunks)}
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                result = future.result()
            except Exception as exc:  # fail one batch, preserve healthy batches
                failures.append({"batch_index": idx, "reason": type(exc).__name__})
                continue
            if result.get("status") == "JEV_BATCH_OK":
                decisions.update(result.get("decisions") or {})
                latencies.append(float(result.get("latency_ms") or 0.0))
                usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
                try:
                    total_cost += float(usage.get("cost") or 0.0)
                except (TypeError, ValueError):
                    pass
            else:
                failures.append({
                    "batch_index": idx,
                    "reason": result.get("reason") or result.get("status"),
                    "record_ids": result.get("record_ids") or [],
                })

    return {
        "status": "JEV_MANY_OK" if not failures else ("JEV_MANY_PARTIAL" if decisions else "JEV_UNAVAILABLE"),
        "record_count": len(records),
        "batch_count": len(chunks),
        "parallel_batch_count": min(max_parallel, len(chunks)),
        "decisions": decisions,
        "failed_batches": failures,
        "max_batch_latency_ms": max(latencies) if latencies else None,
        "estimated_total_cost": total_cost,
        "fallback_for_failed_records": "DETERMINISTIC_OR_CHATGPT_ROUTING",
    }


def decide(
    *,
    task_summary: str,
    candidate_models: Sequence[str],
    remaining_free_quota: int,
    candidate_profiles: Mapping[str, str] | None = None,
    api_key: str | None = None,
    timeout_seconds: float = 10.0,
    catalog_entries: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    record = {
        "id": "r_0001",
        "task_summary": task_summary,
        "candidate_models": list(candidate_models),
        "candidate_profiles": dict(candidate_profiles or {}),
        "quota_pressure": quota_pressure_from_remaining(remaining_free_quota).value,
    }
    result = decide_batch(
        records=[record],
        api_key=api_key,
        timeout_seconds=timeout_seconds,
        catalog_entries=catalog_entries,
    )
    if result.get("status") != "JEV_BATCH_OK":
        return result
    decision = (result.get("decisions") or {}).get("r_0001")
    return {
        **result,
        "status": "JEV_DECISION_OK",
        "decision": decision,
    }



# ---------------------------------------------------------------------------
# Manual-aligned fast routing surface
# ---------------------------------------------------------------------------

def _fast_route_shapes(allow_third: bool) -> dict[str, str]:
    shapes = {
        "SINGLE": "One eligible worker is sufficient.",
        "PARALLEL_PAIR": "Two distinct eligible workers should run concurrently because their work is independent or a fast hedge materially improves reliability.",
        "SEQUENTIAL_PAIR": "Two distinct eligible workers are useful, but the second should follow the first because of dependency or shared mutable state.",
        "ESCALATE": "The routing decision is too ambiguous or high-risk for autonomous worker activation.",
    }
    if allow_third:
        shapes["PARALLEL_TRIPLE"] = "Three distinct eligible workers should run concurrently because there are at least three genuinely independent or complementary workstreams."
    return shapes


def _prepare_fast_route_record(
    record: Mapping[str, Any],
    *,
    index: int,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    prepared = _prepare_record(record, index=index, policy=policy)
    lane = str(record.get("lane") or Lane.GENERAL_REASONING.value)
    try:
        lane = Lane(lane).value
    except ValueError:
        lane = Lane.GENERAL_REASONING.value
    prepared["lane"] = lane
    prepared["allow_third"] = bool(record.get("allow_third"))
    prepared["shared_mutable_state"] = bool(record.get("shared_mutable_state"))
    prepared["high_risk"] = bool(record.get("high_risk"))
    return prepared


def _fast_questions_for_record(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    rid = str(record["id"])
    prefix = f"{rid}__"
    candidates = list(record["candidate_models"])
    profiles = record["candidate_profiles"]
    model_criteria = {
        model: f"Choose {model} when it is the strongest fit. {profiles[model]}"
        for model in candidates
    }
    route_shapes = _fast_route_shapes(bool(record.get("allow_third")))
    if record.get("shared_mutable_state"):
        route_shapes.pop("PARALLEL_PAIR", None)
        route_shapes.pop("PARALLEL_TRIPLE", None)
    if record.get("high_risk"):
        route_shapes["ESCALATE"] = "Prefer this when autonomous routing could create high-impact risk or needs commander adjudication."
    questions = {
        prefix + "primary_worker": _choice(
            model_criteria,
            f'For record "{rid}", choose the single best primary eligible worker.',
        ),
        prefix + "route_shape": _choice(
            route_shapes,
            f'For record "{rid}", choose the smallest execution shape that preserves quality and minimizes wall-clock time.',
        ),
        prefix + "secondary_worker": _choice(
            model_criteria,
            f'For record "{rid}", choose the best distinct complement to the primary if the route shape uses two or more workers.',
        ),
    }
    if record.get("allow_third"):
        questions[prefix + "tertiary_worker"] = _choice(
            model_criteria,
            f'For record "{rid}", choose the best distinct third worker if PARALLEL_TRIPLE is selected.',
        )
    return questions


def build_fast_route_batch_request(
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
    external_ids = [str(x["external_id"]) for x in prepared]
    if len(set(external_ids)) != len(external_ids):
        raise JevDecisionError("duplicate_external_record_id")
    seen: set[str] = set()
    for i, record in enumerate(prepared, 1):
        if record["id"] in seen:
            record["id"] = f"r_{i:04d}"
        seen.add(record["id"])

    questions: dict[str, Any] = {}
    state_records: list[dict[str, str]] = []
    for record in prepared:
        questions.update(_fast_questions_for_record(record))
        state_records.append({
            "id": record["id"],
            "record": json.dumps({
                "task_summary": record["task_summary"],
                "lane": record["lane"],
                "eligible_candidate_profiles": record["candidate_profiles"],
                "quota_pressure": record["quota_pressure"],
                "shared_mutable_state": record["shared_mutable_state"],
                "high_risk": record["high_risk"],
                "allow_third": record["allow_third"],
                "hard_rules": [
                    "Choose only from eligible_candidate_profiles.",
                    "Use the smallest route shape that preserves quality.",
                    "Do not expand permissions or authorize paid workers.",
                    "Do not perform arithmetic; quota and fanout are computed by code.",
                ],
            }, ensure_ascii=False, separators=(",", ":")),
        })
    return {
        "model": model,
        "state": {
            "description": "Prevalidated AI Army routing records. Code has already handled deterministic constraints and arithmetic.",
            "records": state_records,
        },
        "questions": questions,
    }, prepared


def _parse_fast_record(
    answers: Mapping[str, Any],
    record: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> NormalizedRoutingDecision:
    rid = str(record["id"])
    prefix = f"{rid}__"
    required = ["primary_worker", "route_shape", "secondary_worker"]
    if record.get("allow_third"):
        required.append("tertiary_worker")
    if any(prefix + key not in answers for key in required):
        raise JevDecisionError(f"{rid}:fast_answers_incomplete")

    primary, primary_conf, primary_probs = _choice_answer(answers[prefix + "primary_worker"])
    route_shape, route_conf, _ = _choice_answer(answers[prefix + "route_shape"])
    secondary, secondary_conf, secondary_probs = _choice_answer(answers[prefix + "secondary_worker"])
    tertiary = ""
    tertiary_conf = 1.0
    tertiary_probs: dict[str, float] = {}
    if record.get("allow_third"):
        tertiary, tertiary_conf, tertiary_probs = _choice_answer(answers[prefix + "tertiary_worker"])

    allowed = set(str(x) for x in record["candidate_models"])
    primary_selected, primary_prob = _best_distinct(primary, primary_probs, used=set(), allowed=allowed)
    if primary_selected is None:
        raise JevDecisionError(f"{rid}:candidate_expansion_blocked")

    valid_shapes = set(_fast_route_shapes(bool(record.get("allow_third"))))
    if record.get("shared_mutable_state"):
        valid_shapes.discard("PARALLEL_PAIR")
        valid_shapes.discard("PARALLEL_TRIPLE")
    if route_shape not in valid_shapes:
        raise JevDecisionError(f"{rid}:invalid_route_shape")

    selected = [primary_selected]
    used = {primary_selected}
    selected_conf = [min(primary_conf, primary_prob if primary_prob > 0 else primary_conf)]

    if route_shape in {"PARALLEL_PAIR", "SEQUENTIAL_PAIR", "PARALLEL_TRIPLE"} and len(allowed) >= 2:
        second_selected, second_prob = _best_distinct(secondary, secondary_probs, used=used, allowed=allowed)
        if second_selected is not None:
            selected.append(second_selected)
            used.add(second_selected)
            selected_conf.append(min(secondary_conf, second_prob if second_prob > 0 else secondary_conf))

    if route_shape == "PARALLEL_TRIPLE" and len(allowed) >= 3:
        third_selected, third_prob = _best_distinct(tertiary, tertiary_probs, used=used, allowed=allowed)
        if third_selected is not None:
            selected.append(third_selected)
            used.add(third_selected)
            selected_conf.append(min(tertiary_conf, third_prob if third_prob > 0 else tertiary_conf))

    if route_shape == "ESCALATE":
        action = Action.ESCALATE
        selected = selected[:1]
        mode = ExecutionMode.SINGLE
        parallel = False
    elif len(selected) == 1:
        action = Action.EXECUTE
        mode = ExecutionMode.SINGLE
        parallel = False
    elif route_shape == "SEQUENTIAL_PAIR":
        action = Action.EXECUTE
        mode = ExecutionMode.SEQUENTIAL
        parallel = False
    else:
        action = Action.EXECUTE
        mode = ExecutionMode.PARALLEL
        parallel = True

    critical_conf = min(primary_conf, route_conf)
    threshold = float((policy.get("decision_contract") or {}).get("low_confidence_threshold", 0.65))
    low_confidence = critical_conf < threshold
    if low_confidence:
        action = Action.ESCALATE

    return NormalizedRoutingDecision(
        schema_version="jev-fast-routing-decision-v1",
        record_id=str(record.get("external_id") or rid),
        lane=str(record.get("lane") or Lane.GENERAL_REASONING.value),
        workers=tuple(selected),
        fanout=len(selected),
        parallel=parallel,
        execution_mode=mode.value,
        independent_verification=len(selected) > 1,
        action=action.value,
        confidence=round(min([critical_conf, *selected_conf]), 6),
        low_confidence=low_confidence,
    )


def parse_fast_route_response(
    payload: Mapping[str, Any],
    *,
    prepared_records: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    answers = payload.get("answers")
    if not isinstance(answers, Mapping):
        raise JevDecisionError("answers_missing")
    out: dict[str, dict[str, Any]] = {}
    for record in prepared_records:
        decision = _parse_fast_record(answers, record, policy)
        out[decision.record_id] = decision.to_dict()
    return out


def _request_fast_route_once(
    *,
    model: str,
    api_key: str,
    records: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    body, prepared = build_fast_route_batch_request(model=model, records=records, policy=policy)
    status, payload, latency_ms = _json_request(
        DECISIONS_URL,
        method="POST",
        api_key=api_key,
        body=body,
        timeout_seconds=timeout_seconds,
    )
    if status != 200:
        error = payload.get("error") if isinstance(payload.get("error"), Mapping) else {}
        message = str(error.get("message") or payload.get("message") or "")[:180]
        raise JevDecisionError(f"http_{status}" + (f":{message}" if message else ""))
    decisions = parse_fast_route_response(payload, prepared_records=prepared, policy=policy)
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    return {
        "status": "JEV_FAST_BATCH_OK",
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


def decide_fast_batch(
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
            result = _request_fast_route_once(
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
        "reason": "FAST_LATEST_AND_PINNED_FAILED",
        "errors": errors,
        "decisions": {},
    }


def decide_many_fast(
    *,
    records: Sequence[Mapping[str, Any]],
    api_key: str | None = None,
    timeout_seconds: float = 10.0,
    catalog_entries: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if not records:
        return {"status": "JEV_FAST_MANY_OK", "record_count": 0, "batch_count": 0, "decisions": {}}
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
        return decide_fast_batch(
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
            if result.get("status") == "JEV_FAST_BATCH_OK":
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
        "status": "JEV_FAST_MANY_OK" if not failures else ("JEV_FAST_MANY_PARTIAL" if decisions else "JEV_UNAVAILABLE"),
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


def decide_fast(
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
    result = decide_fast_batch(
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
    if result.get("status") != "JEV_FAST_BATCH_OK":
        return result
    return {
        **result,
        "status": "JEV_FAST_DECISION_OK",
        "decision": (result.get("decisions") or {}).get("r_0001"),
    }



# ---------------------------------------------------------------------------
# Experimental minimum-question route: one Choice over prevalidated portfolios.
# ---------------------------------------------------------------------------

def _portfolio_options(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    candidates = list(record["candidate_models"])[:4]
    if not candidates:
        return {}
    allow_third = bool(record.get("allow_third"))
    shared = bool(record.get("shared_mutable_state"))
    options: dict[str, dict[str, Any]] = {}

    def add(key: str, workers: list[str], mode: str, rationale: str) -> None:
        options[key] = {
            "workers": workers,
            "mode": mode,
            "description": rationale,
        }

    add("single_primary", [candidates[0]], "SINGLE", f"Use only {candidates[0]} for minimum latency and coordination.")
    if len(candidates) >= 2:
        add("single_alternative", [candidates[1]], "SINGLE", f"Use only {candidates[1]} when its specialist profile better matches the task.")
        if shared:
            add("sequential_pair_01", [candidates[0], candidates[1]], "SEQUENTIAL", f"Use {candidates[0]} then {candidates[1]} because shared state makes parallel work unsafe.")
        else:
            add("parallel_pair_01", [candidates[0], candidates[1]], "PARALLEL", f"Run {candidates[0]} and {candidates[1]} concurrently when complementary coverage or hedging justifies two workers.")
    if len(candidates) >= 3 and not shared:
        add("parallel_pair_02", [candidates[0], candidates[2]], "PARALLEL", f"Run {candidates[0]} and {candidates[2]} concurrently when the third-ranked specialist provides a better complement.")
        if allow_third:
            add("parallel_triple_012", [candidates[0], candidates[1], candidates[2]], "PARALLEL", "Run three workers only when three independent or complementary workstreams materially improve total value.")
    add("escalate", [candidates[0]], "SINGLE", "Escalate to ChatGPT when autonomous routing is too ambiguous or high-risk.")
    return options


def build_portfolio_route_batch_request(
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

        options = _portfolio_options(record)
        record["portfolio_options"] = options
        criteria = {
            key: (
                value["description"]
                + " Profiles: "
                + " | ".join(
                    f"{model_id}: {record['candidate_profiles'].get(model_id, '')}"
                    for model_id in value["workers"]
                )
            )
            for key, value in options.items()
        }
        questions[f"{record['id']}__route_portfolio"] = _choice(
            criteria,
            f'For record "{record["id"]}", choose the smallest safe execution portfolio that best balances quality and wall-clock speed.',
        )
        state_records.append({
            "id": record["id"],
            "record": json.dumps({
                "task_summary": record["task_summary"],
                "lane": record["lane"],
                "quota_pressure": record["quota_pressure"],
                "high_risk": record["high_risk"],
                "shared_mutable_state": record["shared_mutable_state"],
                "hard_rules": [
                    "Choose exactly one provided portfolio.",
                    "Prefer the smallest portfolio unless another materially improves quality or latency.",
                    "Do not expand workers, permissions, or paid scope.",
                ],
            }, ensure_ascii=False, separators=(",", ":")),
        })

    return {
        "model": model,
        "state": {
            "description": "Prevalidated routing records with code-generated safe execution portfolios.",
            "records": state_records,
        },
        "questions": questions,
    }, prepared


def parse_portfolio_route_response(
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
        answer = answers.get(f"{rid}__route_portfolio")
        if not isinstance(answer, Mapping):
            raise JevDecisionError(f"{rid}:portfolio_answer_missing")
        choice_key, confidence, probabilities = _choice_answer(answer)
        options = record.get("portfolio_options") if isinstance(record.get("portfolio_options"), Mapping) else {}
        if choice_key not in options:
            ranked = sorted(
                ((float(prob), key) for key, prob in probabilities.items() if key in options),
                reverse=True,
            )
            if not ranked:
                raise JevDecisionError(f"{rid}:portfolio_choice_invalid")
            choice_key = ranked[0][1]
        option = options[choice_key]
        workers = tuple(str(x) for x in option["workers"])
        mode = str(option["mode"])
        low = confidence < threshold
        action = Action.ESCALATE if choice_key == "escalate" or low else Action.EXECUTE
        out[str(record.get("external_id") or rid)] = NormalizedRoutingDecision(
            schema_version="jev-portfolio-routing-decision-v1",
            record_id=str(record.get("external_id") or rid),
            lane=str(record.get("lane") or Lane.GENERAL_REASONING.value),
            workers=workers,
            fanout=len(workers),
            parallel=mode == "PARALLEL" and len(workers) > 1,
            execution_mode=mode,
            independent_verification=len(workers) > 1,
            action=action.value,
            confidence=round(confidence, 6),
            low_confidence=low,
        ).to_dict()
    return out


def decide_portfolio_batch(
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
            errors.append({"model": model, "reason": "EMERGENCY_PRICE_GUARD_BLOCK"})
            continue
        body, prepared = build_portfolio_route_batch_request(model=model, records=records, policy=policy)
        status, payload, latency_ms = _json_request(
            DECISIONS_URL,
            method="POST",
            api_key=key,
            body=body,
            timeout_seconds=timeout_seconds,
        )
        if status != 200:
            errors.append({"model": model, "reason": f"http_{status}"})
            continue
        try:
            decisions = parse_portfolio_route_response(payload, prepared_records=prepared, policy=policy)
        except JevDecisionError as exc:
            errors.append({"model": model, "reason": str(exc)})
            continue
        usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
        return {
            "status": "JEV_PORTFOLIO_BATCH_OK",
            "requested_model": model,
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
            "prior_errors": errors,
        }
    return {"status": "JEV_UNAVAILABLE", "reason": "PORTFOLIO_LATEST_AND_PINNED_FAILED", "errors": errors, "decisions": {}}


__all__ = [
    "Action",
    "DECISIONS_URL",
    "ExecutionMode",
    "JevDecisionError",
    "Lane",
    "NormalizedRoutingDecision",
    "QuotaPressure",
    "build_batch_decisions_request",
    "build_decisions_request",
    "decide",
    "decide_batch",
    "decide_many",
    "fetch_catalog",
    "load_policy",
    "parse_batch_decisions_response",
    "parse_decisions_response",
    "price_guard_allows",
    "quota_pressure_from_remaining",    "build_fast_route_batch_request",
    "parse_fast_route_response",
    "decide_fast",
    "decide_fast_batch",
    "decide_many_fast",
    "build_portfolio_route_batch_request",
    "parse_portfolio_route_response",
    "decide_portfolio_batch",

]
