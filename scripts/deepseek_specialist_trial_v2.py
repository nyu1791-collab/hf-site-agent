#!/usr/bin/env python3
"""Second-stage DeepSeek V4.1 Flash trial adapter.

The first paid trial proved auth and exact model discovery but showed that a
1024-token generation budget can be consumed by thinking before visible JSON is
emitted.  This adapter keeps the same six-call staging boundary while allowing
up to 4096 generated tokens, parsing JSON defensively, and preserving usage,
finish reason, cache counters, and estimated cost even when visible output
validation fails.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import deepseek_specialist_trial as base


MAX_GENERATION_TOKENS = 4096
MAX_SAFE_ERROR_DETAIL_CHARS = 80


class SpecialistOutputError(ValueError):
    def __init__(self, code: str, *, finish_reason: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.finish_reason = finish_reason


def _first_choice(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise SpecialistOutputError("CHOICES_MISSING")
    first = choices[0]
    message = first.get("message") if isinstance(first.get("message"), Mapping) else {}
    return first, message


def parse_json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```json"):
        value = value[7:].lstrip()
        if value.endswith("```"):
            value = value[:-3].rstrip()
    elif value.startswith("```"):
        value = value[3:].lstrip()
        if value.endswith("```"):
            value = value[:-3].rstrip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, char in enumerate(value):
            if char != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(value[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                parsed = candidate
                break
        else:
            raise SpecialistOutputError("STRUCTURED_OUTPUT_INVALID") from None
    if not isinstance(parsed, dict):
        raise SpecialistOutputError("STRUCTURED_OUTPUT_NOT_OBJECT")
    return dict(parsed)


def _response_shape(payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        first, message = _first_choice(payload)
    except SpecialistOutputError as exc:
        return {
            "finish_reason": "",
            "response_model": str(payload.get("model") or "") or None,
            "content_chars": 0,
            "reasoning_content_chars": 0,
            "shape_error": exc.code,
        }
    content = message.get("content")
    reasoning = message.get("reasoning_content")
    return {
        "finish_reason": str(first.get("finish_reason") or ""),
        "response_model": str(payload.get("model") or "") or None,
        "content_chars": len(content) if isinstance(content, str) else 0,
        "reasoning_content_chars": len(reasoning) if isinstance(reasoning, str) else 0,
        "shape_error": None,
    }


def _usage(payload: Mapping[str, Any]) -> dict[str, int]:
    raw = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    details = raw.get("completion_tokens_details") if isinstance(raw.get("completion_tokens_details"), Mapping) else {}
    return {
        "prompt_tokens": int(raw.get("prompt_tokens") or 0),
        "prompt_cache_hit_tokens": int(raw.get("prompt_cache_hit_tokens") or 0),
        "prompt_cache_miss_tokens": int(raw.get("prompt_cache_miss_tokens") or 0),
        "completion_tokens": int(raw.get("completion_tokens") or 0),
        "reasoning_tokens": int(details.get("reasoning_tokens") or 0),
        "total_tokens": int(raw.get("total_tokens") or 0),
    }


def _cost_fields(config: Mapping[str, Any], usage: Mapping[str, Any]) -> dict[str, float]:
    now = base.datetime.now(base.timezone.utc)
    current = base.estimate_cost_usd(usage, base._rate_table(config, conservative=False, now=now))
    conservative = base.estimate_cost_usd(usage, base._rate_table(config, conservative=True, now=now))
    return {
        "estimated_current_cost_usd": round(current, 8),
        "conservative_cost_usd": round(conservative, 8),
    }


def _failed_result(*, task: Mapping[str, Any], error_class: str, http_status: int | None = None,
                   latency_ms: int | None = None, response: Mapping[str, Any] | None = None,
                   config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload = response if isinstance(response, Mapping) else {}
    shape = _response_shape(payload)
    usage = _usage(payload)
    costs = _cost_fields(config or {}, usage) if config else {
        "estimated_current_cost_usd": 0.0,
        "conservative_cost_usd": 0.0,
    }
    return {
        "task_id": task["task_id"],
        "role": task["role"],
        "status": "TRIAL_TASK_FAILED",
        "error_class": error_class[:MAX_SAFE_ERROR_DETAIL_CHARS],
        "http_status": http_status,
        "latency_ms": latency_ms,
        "requested_model": str((config or {}).get("model") or ""),
        **shape,
        "usage": usage,
        **costs,
        "quality_score": 0.0,
    }


def run_task(*, config: Mapping[str, Any], api_key: str, task: Mapping[str, Any], shared_context: str) -> dict[str, Any]:
    role = str(task["role"])
    model = str(config["model"])
    base_url = str(config["base_url"]).rstrip("/")
    budget = config.get("trial_budget") if isinstance(config.get("trial_budget"), Mapping) else {}
    reasoning = config.get("reasoning") if isinstance(config.get("reasoning"), Mapping) else {}
    max_tokens = min(MAX_GENERATION_TOKENS, max(1, int(budget.get("max_output_tokens_per_call") or MAX_GENERATION_TOKENS)))
    user_prompt = f"ROLE={role}\nTASK={task['objective']}\nReturn the final compact JSON now."
    request_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": base._system_prompt(shared_context)},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
        "reasoning_effort": str(reasoning.get(role) or "low"),
        "thinking": {"type": "enabled"},
    }
    try:
        response, latency_ms = base._request_json(
            base_url + "/chat/completions",
            api_key=api_key,
            method="POST",
            payload=request_payload,
        )
    except Exception as exc:
        error_class, http_status = base._normalized_error(exc)
        return _failed_result(
            task=task,
            error_class=error_class,
            http_status=http_status,
            config=config,
        )

    shape = _response_shape(response)
    usage = _usage(response)
    costs = _cost_fields(config, usage)
    try:
        first, message = _first_choice(response)
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise SpecialistOutputError("VISIBLE_CONTENT_MISSING", finish_reason=str(first.get("finish_reason") or ""))
        parsed = parse_json_object(content)
    except SpecialistOutputError as exc:
        return _failed_result(
            task=task,
            error_class=exc.code,
            latency_ms=latency_ms,
            response=response,
            config=config,
        )

    return {
        "task_id": task["task_id"],
        "role": role,
        "status": "TRIAL_TASK_OK",
        "requested_model": model,
        **shape,
        "latency_ms": latency_ms,
        "usage": usage,
        **costs,
        "quality_score": base._quality_score(parsed, role),
        "result": parsed,
    }


def conservative_preflight_cost(config: Mapping[str, Any], prompt_chars: int, _legacy_max_output_tokens: int) -> float:
    budget = config.get("trial_budget") if isinstance(config.get("trial_budget"), Mapping) else {}
    configured = min(MAX_GENERATION_TOKENS, max(1, int(budget.get("max_output_tokens_per_call") or MAX_GENERATION_TOKENS)))
    return base._preflight_estimated_call_cost(config, prompt_chars, configured)


def run_trial(*, config: Mapping[str, Any], api_key: str, network: bool, confirm: str) -> dict[str, Any]:
    original_task = base._run_task
    original_preflight = base._preflight_estimated_call_cost
    base._run_task = run_task
    base._preflight_estimated_call_cost = conservative_preflight_cost
    try:
        report = dict(base.run_trial(config=config, api_key=api_key, network=network, confirm=confirm))
    finally:
        base._run_task = original_task
        base._preflight_estimated_call_cost = original_preflight
    report["schema_version"] = "deepseek-specialist-trial-report-v2"
    report["generation_budget_policy"] = "THINKING_AND_VISIBLE_OUTPUT_SHARE_4096_TOKEN_BUDGET"
    report["failure_usage_preserved"] = True
    report["robust_json_parser"] = True
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(base.DEFAULT_CONFIG))
    parser.add_argument("--output", default="artifacts/deepseek_specialist_trial.json")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    config_path = Path(args.config)
    output_path = Path(args.output)
    if config_path.is_absolute() and config_path != base.DEFAULT_CONFIG:
        raise SystemExit("config must be the repository DeepSeek trial config")
    if output_path.is_absolute() or ".." in output_path.parts:
        raise SystemExit("output must stay inside workspace")
    config = base._load_json(config_path)
    api_key = os.environ.get(str(config.get("api_key_env") or "DEEPSEEK_API_KEY"), "")
    report = run_trial(config=config, api_key=api_key, network=args.network, confirm=args.confirm)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "requested_model": report.get("requested_model"),
        "exact_model_listed": report.get("exact_model_listed", False),
        "successful_task_count": report.get("successful_task_count", 0),
        "selected_task_count": report.get("selected_task_count", 0),
        "average_quality_score": report.get("average_quality_score", 0),
        "parallel_speedup": report.get("parallel_speedup", 0),
        "prompt_cache_hit_rate": report.get("prompt_cache_hit_rate", 0),
        "estimated_current_cost_usd": report.get("estimated_current_cost_usd", 0),
        "conservative_cost_usd": report.get("conservative_cost_usd", 0),
        "placement_recommendation": report.get("placement_recommendation"),
        "generic_paid_fallback": report.get("generic_paid_fallback", False),
        "production_routing_changed": report.get("production_routing_changed", False),
    }, sort_keys=True))
    return 0 if report.get("status") in {"TRIAL_DRY_RUN", "TRIAL_READY", "TRIAL_PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
