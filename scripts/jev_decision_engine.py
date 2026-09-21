#!/usr/bin/env python3
"""Bounded Jev decision engine for AI Army routing and triage.

Jev is used only for compact typed decisions. ChatGPT remains final authority.
The runtime prefers ~typesafe/jev-latest and automatically falls back once to
the last-known-good pinned Jev when the latest alias fails contract checks.
No other paid model fallback is allowed.
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
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


class JevDecisionError(RuntimeError):
    pass


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise JevDecisionError("invalid_jev_policy")
    return value


def _bounded_text(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) > max_chars:
        raise JevDecisionError("jev_prompt_too_large")
    return text


def _decision_schema(candidate_models: Sequence[str]) -> dict[str, Any]:
    candidates = [str(x) for x in candidate_models if str(x).strip()]
    if not candidates:
        candidates = ["NO_MODEL"]
    return {
        "name": "ai_army_jev_decision",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "task_class": {"type": "string"},
                "lane": {"type": "string"},
                "fanout": {"type": "integer", "minimum": 1, "maximum": 3},
                "execution_mode": {"type": "string", "enum": ["SINGLE", "PARALLEL", "SEQUENTIAL"]},
                "selected_models": {
                    "type": "array",
                    "items": {"type": "string", "enum": candidates},
                    "minItems": 1,
                    "maxItems": 3,
                    "uniqueItems": True,
                },
                "independent_verification": {"type": "boolean"},
                "action": {"type": "string", "enum": ["EXECUTE", "STOP", "RETRY", "ESCALATE"]},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
            "required": [
                "task_class",
                "lane",
                "fanout",
                "execution_mode",
                "selected_models",
                "independent_verification",
                "action",
                "confidence",
            ],
            "additionalProperties": False,
        },
    }


def build_request(
    *,
    model: str,
    task_summary: str,
    candidate_models: Sequence[str],
    remaining_free_quota: int,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    guard = policy.get("cost_guard") or {}
    max_chars = int(guard.get("max_prompt_chars", 24000))
    summary = _bounded_text(task_summary, max_chars)
    candidates = [str(x) for x in candidate_models if str(x).strip()][:12]
    if not candidates:
        raise JevDecisionError("no_candidate_models")
    prompt = {
        "task_summary": summary,
        "candidate_models": candidates,
        "remaining_free_quota": max(0, int(remaining_free_quota)),
        "instructions": [
            "Choose only from candidate_models.",
            "Use 1 to 3 models only when total expected quality/latency value justifies it.",
            "Do not authorize paid workers or side effects.",
            "Return the typed decision only.",
        ],
    }
    return {
        "model": model,
        "messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False, separators=(",", ":"))}],
        "response_format": {"type": "json_schema", "json_schema": _decision_schema(candidates)},
        "temperature": 0,
        "stream": False,
        "provider": {
            "allow_fallbacks": False,
            "max_price": {
                "prompt": float(guard.get("request_max_price_prompt_usd_per_million", 0.05)),
                "completion": float(guard.get("request_max_price_completion_usd_per_million", 0.0)),
            },
        },
    }


def _extract_content(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content")
    return content.strip() if isinstance(content, str) else ""


def validate_decision(
    decision: Mapping[str, Any],
    *,
    candidate_models: Sequence[str],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    allowed = {str(x) for x in candidate_models}
    selected = decision.get("selected_models")
    if not isinstance(selected, list) or not 1 <= len(selected) <= 3:
        raise JevDecisionError("invalid_selected_models")
    if any(str(x) not in allowed for x in selected):
        raise JevDecisionError("candidate_expansion_blocked")
    fanout = int(decision.get("fanout", 0) or 0)
    if not 1 <= fanout <= 3 or fanout != len(selected):
        raise JevDecisionError("fanout_contract_failed")
    confidence = float(decision.get("confidence", -1))
    if not 0.0 <= confidence <= 1.0:
        raise JevDecisionError("invalid_confidence")
    action = str(decision.get("action") or "")
    if action not in {"EXECUTE", "STOP", "RETRY", "ESCALATE"}:
        raise JevDecisionError("invalid_action")
    threshold = float(((policy.get("decision_contract") or {}).get("low_confidence_threshold", 0.65)))
    result = dict(decision)
    result["selected_models"] = [str(x) for x in selected]
    result["confidence"] = confidence
    result["low_confidence"] = confidence < threshold
    if result["low_confidence"]:
        result["action"] = "ESCALATE"
    return result


def _request_once(
    *,
    model: str,
    api_key: str,
    task_summary: str,
    candidate_models: Sequence[str],
    remaining_free_quota: int,
    policy: Mapping[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    body = build_request(
        model=model,
        task_summary=task_summary,
        candidate_models=candidate_models,
        remaining_free_quota=remaining_free_quota,
        policy=policy,
    )
    req = urllib.request.Request(
        CHAT_URL,
        data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/nyu1791-collab/hf-site-agent",
            "X-Title": "hf-site-agent-jev-decision-engine",
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read(1_000_000).decode("utf-8", errors="replace")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raise JevDecisionError(f"http_{int(exc.code)}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise JevDecisionError("network_or_timeout") from exc
    latency_ms = max(1.0, (time.perf_counter() - started) * 1000.0)
    if status != 200:
        raise JevDecisionError(f"http_{status}")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise JevDecisionError("invalid_json_response") from exc
    if not isinstance(payload, Mapping):
        raise JevDecisionError("invalid_response_object")
    content = _extract_content(payload)
    try:
        decision = json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise JevDecisionError("invalid_decision_json") from exc
    if not isinstance(decision, Mapping):
        raise JevDecisionError("invalid_decision_object")
    checked = validate_decision(decision, candidate_models=candidate_models, policy=policy)
    response_model = str(payload.get("model") or "")
    if response_model and "jev" not in response_model.lower():
        raise JevDecisionError("resolved_model_not_jev_family")
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    return {
        "status": "JEV_DECISION_OK",
        "requested_model": model,
        "resolved_model": response_model or None,
        "latency_ms": round(latency_ms, 3),
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "cost": usage.get("cost"),
        },
        "decision": checked,
        "paid_execution": True,
        "paid_fallback_to_other_family": False,
    }


def decide(
    *,
    task_summary: str,
    candidate_models: Sequence[str],
    remaining_free_quota: int,
    api_key: str | None = None,
    timeout_seconds: float = 10.0,
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
    errors: list[dict[str, str]] = []
    for model in [latest, pinned]:
        if model == pinned and latest == pinned:
            continue
        try:
            result = _request_once(
                model=model,
                api_key=key,
                task_summary=task_summary,
                candidate_models=candidate_models,
                remaining_free_quota=remaining_free_quota,
                policy=policy,
                timeout_seconds=timeout_seconds,
            )
            result["used_pinned_fallback"] = model == pinned
            result["prior_errors"] = errors
            return result
        except JevDecisionError as exc:
            errors.append({"model": model, "reason": str(exc)})
    return {
        "status": "JEV_UNAVAILABLE",
        "reason": "LATEST_AND_PINNED_FAILED",
        "errors": errors,
        "fallback": "DETERMINISTIC_OR_CHATGPT_ROUTING",
        "paid_execution": False,
    }


__all__ = ["JevDecisionError", "build_request", "decide", "load_policy", "validate_decision"]
