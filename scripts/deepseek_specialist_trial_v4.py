#!/usr/bin/env python3
"""Role-adaptive DeepSeek V4.1 Flash engineering collaboration runner.

V4 converts the role-mode benchmark evidence into the live specialist trial:
- CODING_DEEP uses direct mode because it was faster, cheaper and fully grounded.
- DEBUGGING and CODE_REVIEW use high-thinking mode because they benefit from deeper review.
- Other roles follow config and default to bounded, role-appropriate modes.

The runner remains staging-only and never writes repository files, deploys,
publishes, mutates secrets or enables generic paid fallback.
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
from scripts import deepseek_specialist_trial_v2 as v2
from scripts import deepseek_specialist_trial_v3 as v3


DIRECT_MAX_TOKENS = 4096
THINKING_MAX_TOKENS = 8192
MAX_GENERATION_TOKENS = THINKING_MAX_TOKENS
DIRECT_MODE_ALIASES = frozenset({"direct", "off", "disabled", "none"})
DEFAULT_ROLE_MODES: Mapping[str, str] = {
    "CODING_DEEP": "direct",
    "DEBUGGING": "high",
    "CODE_REVIEW": "high",
    "ARCHITECTURE": "high",
    "TEST_STRATEGY": "direct",
    "INTEGRATION_REVIEW": "direct",
}


def role_mode(config: Mapping[str, Any], role: str) -> dict[str, Any]:
    reasoning = config.get("reasoning") if isinstance(config.get("reasoning"), Mapping) else {}
    raw = str(reasoning.get(role) or DEFAULT_ROLE_MODES.get(role, "high")).strip().lower()
    if raw in DIRECT_MODE_ALIASES:
        return {
            "mode": "direct",
            "thinking": False,
            "reasoning_effort": None,
            "max_tokens": DIRECT_MAX_TOKENS,
        }
    effort = raw if raw in {"low", "medium", "high"} else "high"
    return {
        "mode": f"thinking_{effort}",
        "thinking": True,
        "reasoning_effort": effort,
        "max_tokens": THINKING_MAX_TOKENS,
    }


def run_task(*, config: Mapping[str, Any], api_key: str, task: Mapping[str, Any], shared_context: str) -> dict[str, Any]:
    role = str(task["role"])
    model = str(config["model"])
    base_url = str(config["base_url"]).rstrip("/")
    budget = config.get("trial_budget") if isinstance(config.get("trial_budget"), Mapping) else {}
    mode = role_mode(config, role)
    configured_cap = max(1, int(budget.get("max_output_tokens_per_call") or MAX_GENERATION_TOKENS))
    max_tokens = min(MAX_GENERATION_TOKENS, int(mode["max_tokens"]), configured_cap)
    user_prompt = f"ROLE={role}\nTASK={task['objective']}\nReturn the final compact JSON now."
    request_payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": base._system_prompt(shared_context)},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "enabled" if mode["thinking"] else "disabled"},
    }
    if mode["reasoning_effort"]:
        request_payload["reasoning_effort"] = mode["reasoning_effort"]

    try:
        response, latency_ms = base._request_json(
            base_url + "/chat/completions",
            api_key=api_key,
            method="POST",
            payload=request_payload,
            timeout=120.0,
        )
    except Exception as exc:
        error_class, http_status = base._normalized_error(exc)
        row = v2._failed_result(
            task=task,
            error_class=error_class,
            http_status=http_status,
            config=config,
        )
        row.update({
            "role_mode": mode["mode"],
            "thinking": bool(mode["thinking"]),
            "reasoning_effort": mode["reasoning_effort"],
            "max_tokens": max_tokens,
        })
        return row

    shape = v2._response_shape(response)
    usage = v2._usage(response)
    costs = v2._cost_fields(config, usage)
    try:
        first, message = v2._first_choice(response)
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise v2.SpecialistOutputError(
                "VISIBLE_CONTENT_MISSING",
                finish_reason=str(first.get("finish_reason") or ""),
            )
        parsed = v2.parse_json_object(content)
    except v2.SpecialistOutputError as exc:
        row = v2._failed_result(
            task=task,
            error_class=exc.code,
            latency_ms=latency_ms,
            response=response,
            config=config,
        )
        row.update({
            "role_mode": mode["mode"],
            "thinking": bool(mode["thinking"]),
            "reasoning_effort": mode["reasoning_effort"],
            "max_tokens": max_tokens,
        })
        return row

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
        "role_mode": mode["mode"],
        "thinking": bool(mode["thinking"]),
        "reasoning_effort": mode["reasoning_effort"],
        "max_tokens": max_tokens,
        "result": parsed,
    }


def run_trial(*, config: Mapping[str, Any], api_key: str, network: bool, confirm: str) -> dict[str, Any]:
    original_task = v2.run_task
    original_max = v2.MAX_GENERATION_TOKENS
    v2.run_task = run_task
    v2.MAX_GENERATION_TOKENS = MAX_GENERATION_TOKENS
    try:
        report = dict(v3.run_trial(config=config, api_key=api_key, network=network, confirm=confirm))
    finally:
        v2.run_task = original_task
        v2.MAX_GENERATION_TOKENS = original_max

    report["schema_version"] = "deepseek-specialist-trial-report-v4"
    report["role_adaptive_reasoning"] = True
    report["role_mode_policy"] = dict(DEFAULT_ROLE_MODES)
    report["generation_budget_policy"] = "ROLE_ADAPTIVE_DIRECT_4096_OR_THINKING_8192"
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
        "role_adaptive_reasoning": report.get("role_adaptive_reasoning", False),
        "generic_paid_fallback": report.get("generic_paid_fallback", False),
        "production_routing_changed": report.get("production_routing_changed", False),
    }, sort_keys=True))
    return 0 if report.get("status") in {"TRIAL_DRY_RUN", "TRIAL_READY", "TRIAL_PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
