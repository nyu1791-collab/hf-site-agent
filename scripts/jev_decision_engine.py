#!/usr/bin/env python3
"""Bounded Jev System-One decision engine for AI Army routing and triage.

Uses OpenRouter's dedicated Alpha Decisions endpoint, not chat completions.
Jev only makes narrow typed decisions; ChatGPT remains final authority.

Default model is ~typesafe/jev-latest. Before a paid request, the runtime checks
the public model catalog and refuses a latest target whose observed input/output
price exceeds the Jev policy ceiling. On latest contract/provider failure, it
may try the last-known-good pinned Jev once. It never falls back to a different
paid model family.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "jev_decision_engine_policy.json"
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
CATALOG_URL = "https://openrouter.ai/api/v1/models"


class JevDecisionError(RuntimeError):
    pass


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise JevDecisionError("invalid_jev_policy")
    return value


def _bounded_text(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise JevDecisionError("empty_task_summary")
    if len(text) > max_chars:
        raise JevDecisionError("jev_prompt_too_large")
    return text


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
        "User-Agent": "hf-site-agent-jev-decision-engine/1.0",
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
            raw = response.read(2_000_000).decode("utf-8", errors="replace")
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
    max_prompt = float(guard.get("request_max_price_prompt_usd_per_million", 0.05))
    max_completion = float(guard.get("request_max_price_completion_usd_per_million", 0.0))
    entry = _catalog_entry_for_model(entries, model)
    if entry is None and model.startswith("~"):
        # OpenRouter latest aliases may not appear identically in /models.
        # Only the explicitly authorized Jev latest alias can inherit the
        # observed family price from the current pinned Jev when available.
        pinned = str((policy.get("provider") or {}).get("last_known_good_model") or "")
        entry = _catalog_entry_for_model(entries, pinned)
    pricing = entry.get("pricing") if isinstance(entry, Mapping) else None
    if not isinstance(pricing, Mapping):
        return False, {"reason": "PRICE_EVIDENCE_UNAVAILABLE", "model": model}
    prompt = _price_per_million(pricing.get("prompt"))
    completion = _price_per_million(pricing.get("completion"))
    if prompt is None or completion is None:
        return False, {"reason": "PRICE_PARSE_FAILED", "model": model}
    allowed = prompt <= max_prompt and completion <= max_completion
    return allowed, {
        "model": model,
        "observed_prompt_usd_per_million": prompt,
        "observed_completion_usd_per_million": completion,
        "max_prompt_usd_per_million": max_prompt,
        "max_completion_usd_per_million": max_completion,
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


def build_decisions_request(
    *,
    model: str,
    task_summary: str,
    candidate_models: Sequence[str],
    remaining_free_quota: int,
    policy: Mapping[str, Any],
    candidate_profiles: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    guard = policy.get("cost_guard") or {}
    summary = _bounded_text(task_summary, int(guard.get("max_prompt_chars", 24000)))
    candidates = [str(x) for x in candidate_models if str(x).strip()][:12]
    if not candidates:
        raise JevDecisionError("no_candidate_models")
    profiles = {m: str((candidate_profiles or {}).get(m) or "Eligible specialist candidate.") for m in candidates}
    lane_criteria = {
        "GENERAL_REASONING": "General reasoning, planning, synthesis, or research task.",
        "CODING_ENGINEERING": "Coding, debugging, software engineering, implementation, or testing task.",
        "FAST_CLASSIFICATION_EXTRACTION": "Narrow classification, extraction, tagging, or fast structured work.",
        "VISION_AND_MEDIA_UNDERSTANDING": "Task requires image, video, or multimodal understanding.",
        "LONG_CONTEXT_ORCHESTRATION": "Long-context planning, orchestration, or large-context synthesis.",
        "FINANCE_DATA": "Finance-oriented or structured data-analysis task.",
    }
    model_criteria = {
        model_id: f"Choose {model_id} when it is the best fit among eligible candidates. Profile: {profiles[model_id]}"
        for model_id in candidates
    }
    state = {
        "task_summary": summary,
        "eligible_candidate_profiles": profiles,
        "remaining_free_request_budget": max(0, int(remaining_free_quota)),
        "hard_rules": [
            "Only eligible_candidate_profiles may be selected.",
            "Use the smallest team that maximizes total expected quality and speed after coordination cost.",
            "Parallelize only independent work.",
            "Do not authorize a paid worker, side effect, deployment, publication, merge, payment, or secret action.",
        ],
    }
    questions = {
        "lane": _choice(lane_criteria, "Which specialist lane best matches task_summary?"),
        "primary_model": _choice(model_criteria, "Which eligible model should be the primary worker?"),
        "use_second_model": _noul(
            "Would adding a second eligible model materially improve total system value?",
            "There is independent parallel work, complementary specialization, or meaningful verification value that exceeds coordination and quota cost.",
            "One model is sufficient, work is sequential/shared-state, or extra coordination/quota cost exceeds the expected gain.",
        ),
        "secondary_model": _choice(model_criteria, "If a second model is useful, which eligible candidate is the best complement?"),
        "use_third_model": _noul(
            "Would adding a third eligible model materially improve total system value beyond two?",
            "There are at least three genuinely independent or complementary workstreams and a third model materially improves wall-clock time, coverage, or verification.",
            "A third model would duplicate work, add coordination overhead, or consume quota without enough extra value.",
        ),
        "tertiary_model": _choice(model_criteria, "If a third model is useful, which eligible candidate is the best additional complement?"),
        "parallelize": _noul(
            "Should the selected models execute in parallel?",
            "Their workstreams are independent/read-only and parallelism materially reduces latency or increases coverage.",
            "Tasks are sequential, share a mutable target, or must wait on dependencies.",
        ),
        "independent_verification": _noul(
            "Does this task warrant an independent verification lane?",
            "Output is high impact/high uncertainty or a distinct verifier materially reduces the risk of an escaped defect.",
            "Routine output has a reliable machine oracle or additional review would add little value.",
        ),
        "action": _choice(
            {
                "EXECUTE": "The candidate set and task state are sufficient to proceed.",
                "STOP": "The work should stop because constraints or budget make execution inappropriate.",
                "RETRY": "A prior failed attempt should retry only if a changed method is justified.",
                "ESCALATE": "The decision is too ambiguous or risky and should return to ChatGPT for adjudication.",
            },
            "What should the routing control plane do next?",
        ),
    }
    return {"model": model, "state": state, "questions": questions}


def _answer_choice(answer: Mapping[str, Any]) -> tuple[str, float]:
    if str(answer.get("type") or "") != "choice":
        raise JevDecisionError("expected_choice_answer")
    choice = str(answer.get("choice") or "")
    confidence = float(answer.get("confidence", 0.0) or 0.0)
    if not choice or not 0.0 <= confidence <= 1.0:
        raise JevDecisionError("invalid_choice_answer")
    return choice, confidence


def _answer_noul(answer: Mapping[str, Any]) -> tuple[bool, float]:
    if str(answer.get("type") or "") != "noul":
        raise JevDecisionError("expected_noul_answer")
    probability = float(answer.get("noul", answer.get("probability", -1.0)))
    if not 0.0 <= probability <= 1.0:
        raise JevDecisionError("invalid_noul_answer")
    confidence = abs(probability - 0.5) * 2.0
    return probability >= 0.5, confidence


def parse_decisions_response(
    payload: Mapping[str, Any],
    *,
    candidate_models: Sequence[str],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    answers = payload.get("answers")
    if not isinstance(answers, Mapping):
        raise JevDecisionError("answers_missing")
    required = {
        "lane", "primary_model", "use_second_model", "secondary_model",
        "use_third_model", "tertiary_model", "parallelize",
        "independent_verification", "action",
    }
    if not required <= set(answers):
        raise JevDecisionError("answers_incomplete")
    lane, lane_conf = _answer_choice(answers["lane"])
    primary, primary_conf = _answer_choice(answers["primary_model"])
    use_second, second_need_conf = _answer_noul(answers["use_second_model"])
    secondary, secondary_conf = _answer_choice(answers["secondary_model"])
    use_third, third_need_conf = _answer_noul(answers["use_third_model"])
    tertiary, tertiary_conf = _answer_choice(answers["tertiary_model"])
    parallelize, parallel_conf = _answer_noul(answers["parallelize"])
    verify, verify_conf = _answer_noul(answers["independent_verification"])
    action, action_conf = _answer_choice(answers["action"])

    allowed = {str(x) for x in candidate_models}
    for model in (primary, secondary, tertiary):
        if model not in allowed:
            raise JevDecisionError("candidate_expansion_blocked")

    selected = [primary]
    if use_second and secondary not in selected:
        selected.append(secondary)
    if use_third and len(selected) >= 2 and tertiary not in selected:
        selected.append(tertiary)
    selected = selected[:3]

    confidences = [lane_conf, primary_conf, action_conf, second_need_conf, parallel_conf]
    if len(selected) >= 2:
        confidences.append(secondary_conf)
    if len(selected) >= 3:
        confidences.extend([third_need_conf, tertiary_conf])
    if verify:
        confidences.append(verify_conf)
    confidence = min(confidences) if confidences else 0.0
    threshold = float(((policy.get("decision_contract") or {}).get("low_confidence_threshold", 0.65)))
    low_confidence = confidence < threshold
    if low_confidence:
        action = "ESCALATE"

    return {
        "lane": lane,
        "selected_models": selected,
        "fanout": len(selected),
        "execution_mode": "PARALLEL" if len(selected) > 1 and parallelize else ("SEQUENTIAL" if len(selected) > 1 else "SINGLE"),
        "independent_verification": verify,
        "action": action,
        "confidence": round(confidence, 6),
        "low_confidence": low_confidence,
    }


def _request_once(
    *,
    model: str,
    api_key: str,
    task_summary: str,
    candidate_models: Sequence[str],
    remaining_free_quota: int,
    policy: Mapping[str, Any],
    candidate_profiles: Mapping[str, str] | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    body = build_decisions_request(
        model=model,
        task_summary=task_summary,
        candidate_models=candidate_models,
        remaining_free_quota=remaining_free_quota,
        policy=policy,
        candidate_profiles=candidate_profiles,
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
    decision = parse_decisions_response(payload, candidate_models=candidate_models, policy=policy)
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    return {
        "status": "JEV_DECISION_OK",
        "requested_model": model,
        "response_id": payload.get("id"),
        "latency_ms": round(latency_ms, 3),
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cost": usage.get("cost"),
        },
        "decision": decision,
        "paid_execution": True,
        "paid_fallback_to_other_family": False,
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
    policy = load_policy()
    key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return {
            "status": "JEV_UNAVAILABLE",
            "reason": "OPENROUTER_API_KEY_MISSING",
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
            "fallback": "DETERMINISTIC_OR_CHATGPT_ROUTING",
            "paid_execution": False,
        }

    errors: list[dict[str, Any]] = []
    for model in [latest, pinned]:
        if model == pinned and latest == pinned:
            continue
        price_ok, price_evidence = price_guard_allows(model, policy=policy, entries=catalog)
        if not price_ok:
            errors.append({"model": model, "reason": "PRICE_GUARD_BLOCK", "price_evidence": price_evidence})
            continue
        try:
            result = _request_once(
                model=model,
                api_key=key,
                task_summary=task_summary,
                candidate_models=candidate_models,
                remaining_free_quota=remaining_free_quota,
                policy=policy,
                candidate_profiles=candidate_profiles,
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
        "reason": "LATEST_AND_PINNED_FAILED_OR_PRICE_BLOCKED",
        "errors": errors,
        "fallback": "DETERMINISTIC_OR_CHATGPT_ROUTING",
        "paid_execution": False,
    }


__all__ = [
    "DECISIONS_URL",
    "JevDecisionError",
    "build_decisions_request",
    "decide",
    "fetch_catalog",
    "load_policy",
    "parse_decisions_response",
    "price_guard_allows",
]
