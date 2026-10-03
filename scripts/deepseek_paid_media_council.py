#!/usr/bin/env python3
"""Run a bounded paid DeepSeek editorial council for the current media mission.

The user has granted persistent permission to use the paid DeepSeek route when it
is materially useful. This runner keeps that permission bounded: exact model,
fixed call/parallel/cost ceilings, no provider fallback, no repository write,
no deploy/publish authority, and no secret material in artifacts.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "deepseek_paid_autonomy.json"
MISSION_PATH = ROOT / "missions" / "news-video-pilot.json"
OUTPUT_PATH = ROOT / "artifacts" / "deepseek_paid_media_council.json"
EXPECTED_BASE_URL = "https://api.deepseek.com"
EXPECTED_MODEL = "deepseek-flash"
MAX_HTTP_BODY_BYTES = 1_500_000

# Conservative peak guard retained from the previously verified DeepSeek path.
PROMPT_USD_PER_MILLION = 0.44
OUTPUT_USD_PER_MILLION = 1.32

TASKS: tuple[dict[str, str], ...] = (
    {
        "task_id": "chronology",
        "role": "RESEARCH_SYNTHESIS",
        "instruction": "Audit the chronology. Flag unsupported causal jumps, date confusion, or places where official OpenAI statements and Reuters/Bloomberg reporting are mixed together.",
    },
    {
        "task_id": "fact-risk",
        "role": "FACT_CRITIC",
        "instruction": "Find factual overstatement or missing uncertainty. Prioritize claims about the Hugging Face incident, Astra's Critical cyber threshold, the development slowdown, and Altman's reported comments.",
    },
    {
        "task_id": "longform-structure",
        "role": "LONGFORM_EDITOR",
        "instruction": "Review the 6-9 minute structure for a general Japanese audience. Identify sections that need context, repetition to remove, or transitions that would improve comprehension without padding runtime.",
    },
    {
        "task_id": "retention",
        "role": "AUDIENCE_RETENTION",
        "instruction": "Review hook, pacing, chapter order, and curiosity gaps. Suggest concrete ways to keep a 6-9 minute explainer engaging without sensationalizing safety claims.",
    },
    {
        "task_id": "narration",
        "role": "JAPANESE_NARRATION",
        "instruction": "Review Japanese narration for natural Zundamon delivery. Flag sentences that are too dense, repetitive, or difficult to hear, while preserving factual precision.",
    },
    {
        "task_id": "subtitle-design",
        "role": "SUBTITLE_DESIGN",
        "instruction": "Review subtitle chunks and text hierarchy. Check that title, chapter heading, subheading, and full narration subtitles can be visually distinct and readable on a 1080x1920 video.",
    },
    {
        "task_id": "visual-plan",
        "role": "VISUAL_EDITOR",
        "instruction": "Review the visual roles and Zundamon pose strategy. Ensure Zundamon can stay stationary while expressions change, and suggest where real web images should change to support comprehension.",
    },
    {
        "task_id": "final-critic",
        "role": "FINAL_CRITIC",
        "instruction": "Act as final independent critic. List the five highest-impact remaining problems, if any, and give a PASS, PASS_WITH_CHANGES, or REJECT verdict for rendering.",
    },
)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def rough_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def estimated_cost(prompt: str, max_output_tokens: int) -> float:
    return (
        rough_tokens(prompt) * PROMPT_USD_PER_MILLION
        + max_output_tokens * OUTPUT_USD_PER_MILLION
    ) / 1_000_000.0


def request_json(
    url: str,
    *,
    api_key: str,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    timeout: float = 150.0,
) -> tuple[dict[str, Any], int]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "hf-site-agent-deepseek-media-council/1",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    started = time.monotonic()
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_HTTP_BODY_BYTES).decode("utf-8")
    elapsed_ms = int((time.monotonic() - started) * 1000)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("provider response must be a JSON object")
    return value, elapsed_ms


def normalize_error(exc: BaseException) -> tuple[str, int | None]:
    if isinstance(exc, HTTPError):
        if exc.code == 401:
            return "AUTH_ERROR", exc.code
        if exc.code == 402:
            return "CREDIT_EXHAUSTED", exc.code
        if exc.code == 404:
            return "MODEL_UNAVAILABLE", exc.code
        if exc.code == 429:
            return "RATE_LIMITED", exc.code
        if 500 <= exc.code < 600:
            return "TEMPORARY_PROVIDER_ERROR", exc.code
        return "HTTP_ERROR", exc.code
    if isinstance(exc, (TimeoutError, URLError)):
        return "NETWORK_ERROR", None
    if isinstance(exc, json.JSONDecodeError):
        return "INVALID_JSON", None
    if isinstance(exc, ValueError):
        return "INVALID_RESPONSE", None
    return "PROVIDER_ERROR", None


def exact_model_listed(base_url: str, api_key: str, expected: str) -> bool:
    payload, _ = request_json(base_url + "/models", api_key=api_key, timeout=60.0)
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    ids = {
        str(row.get("id") or "")
        for row in rows
        if isinstance(row, Mapping)
    }
    return expected in ids


def compact_mission(mission: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "title": mission.get("title"),
        "topic": mission.get("topic"),
        "sources": mission.get("sources"),
        "verified_facts": mission.get("verified_facts"),
        "hard_rules": mission.get("hard_rules"),
        "narration": mission.get("narration"),
        "scenes": mission.get("scenes"),
        "zundamon": mission.get("zundamon"),
        "text_hierarchy": mission.get("text_hierarchy"),
        "format": mission.get("format"),
    }


def system_prompt(mission: Mapping[str, Any]) -> str:
    return (
        "You are a paid DeepSeek editorial specialist in a bounded staging-only media pipeline. "
        "You are advisory only: do not claim to edit files, deploy, publish, spend beyond the supplied bounded call, "
        "or access secrets. Use only the supplied mission facts. Distinguish official OpenAI statements from Reuters/Bloomberg reporting. "
        "The goal is a clear Japanese long-form explainer, not sensational content. Zundamon must remain stationary within a scene while expressions may change between scenes. "
        "Every spoken line must remain fully represented by timed subtitles. Return JSON only.\nMISSION:\n"
        + json.dumps(compact_mission(mission), ensure_ascii=False, sort_keys=True)
    )


def run_task(
    *,
    base_url: str,
    model: str,
    api_key: str,
    system: str,
    task: Mapping[str, str],
    max_tokens: int,
    reserved_cost: float,
) -> dict[str, Any]:
    user = (
        f"ROLE={task['role']}\nTASK={task['instruction']}\n"
        "Return exactly one JSON object with keys: verdict, findings, recommendations, confidence. "
        "verdict must be PASS, PASS_WITH_CHANGES, or REJECT. findings and recommendations must be arrays of at most 8 concise items."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    try:
        response, latency_ms = request_json(
            base_url + "/chat/completions",
            api_key=api_key,
            method="POST",
            payload=payload,
        )
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ValueError("choices missing")
        message = choices[0].get("message") if isinstance(choices[0].get("message"), Mapping) else {}
        content = str(message.get("content") or "").strip()
        if not content:
            raise ValueError("visible content missing")
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("specialist result must be object")
        response_model = str(response.get("model") or "").strip()
        if response_model and response_model != model:
            return {
                "task_id": task["task_id"],
                "role": task["role"],
                "status": "BLOCKED_MODEL_MISMATCH",
                "requested_model": model,
                "response_model": response_model,
                "reserved_cost_usd": round(reserved_cost, 8),
            }
        usage_raw = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
        usage = {
            "prompt_tokens": int(usage_raw.get("prompt_tokens") or 0),
            "completion_tokens": int(usage_raw.get("completion_tokens") or 0),
            "total_tokens": int(usage_raw.get("total_tokens") or 0),
        }
        return {
            "task_id": task["task_id"],
            "role": task["role"],
            "status": "SUCCESS",
            "requested_model": model,
            "response_model": response_model or model,
            "latency_ms": latency_ms,
            "reserved_cost_usd": round(reserved_cost, 8),
            "usage": usage,
            "review": parsed,
        }
    except Exception as exc:
        error_class, http_status = normalize_error(exc)
        return {
            "task_id": task["task_id"],
            "role": task["role"],
            "status": "ERROR",
            "error_class": error_class,
            "http_status": http_status,
            "requested_model": model,
            "reserved_cost_usd": round(reserved_cost, 8),
        }


def main() -> int:
    config = load_json(CONFIG_PATH)
    mission = load_json(MISSION_PATH)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    base_url = str(config.get("base_url") or "").rstrip("/")
    model = str(config.get("model") or "")
    api_key_env = str(config.get("api_key_env") or "DEEPSEEK_API_KEY")
    api_key = os.environ.get(api_key_env, "").strip()
    budget = config.get("budget") if isinstance(config.get("budget"), Mapping) else {}
    max_calls = min(len(TASKS), max(0, int(budget.get("max_calls_per_mission") or 0)))
    max_parallel = min(3, max(1, int(budget.get("max_parallel_calls") or 1)))
    max_tokens = min(3072, max(256, int(budget.get("max_output_tokens_per_call") or 2048)))
    max_cost = max(0.0, float(budget.get("max_estimated_cost_usd_per_mission") or 0.0))
    authorized = bool(config.get("authorized_by_user")) and not bool(config.get("per_run_confirmation_required"))

    report: dict[str, Any] = {
        "schema_version": "deepseek-paid-media-council-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": "deepseek",
        "requested_model": model,
        "authorized_by_user": authorized,
        "per_run_confirmation_required": bool(config.get("per_run_confirmation_required")),
        "secret_present": bool(api_key),
        "production_enabled": False,
        "repository_write": False,
        "deploy": False,
        "publish": False,
        "generic_paid_fallback": False,
        "auto_top_up": False,
        "max_calls": max_calls,
        "max_parallel_calls": max_parallel,
        "max_estimated_cost_usd": max_cost,
        "paid_calls": 0,
        "success_count": 0,
        "results": [],
    }

    if base_url != EXPECTED_BASE_URL or model != EXPECTED_MODEL or not authorized:
        report["status"] = "POLICY_BLOCKED"
        OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0
    if not api_key:
        report["status"] = "BLOCKED_MISSING_CREDENTIAL"
        OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0
    try:
        listed = exact_model_listed(base_url, api_key, model)
    except Exception as exc:
        report["status"] = "MODEL_CATALOG_CHECK_FAILED"
        report["catalog_error"] = normalize_error(exc)[0]
        OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0
    report["exact_model_listed"] = listed
    if not listed:
        report["status"] = "MODEL_NOT_LISTED"
        OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0

    system = system_prompt(mission)
    selected = list(TASKS[:max_calls])
    reservations: list[float] = []
    running_total = 0.0
    runnable: list[tuple[dict[str, str], float]] = []
    for task in selected:
        user_stub = task["instruction"] + task["role"]
        reserve = estimated_cost(system + user_stub, max_tokens)
        if running_total + reserve > max_cost + 1e-12:
            break
        running_total += reserve
        reservations.append(reserve)
        runnable.append((task, reserve))

    report["conservative_preflight_cost_usd"] = round(running_total, 8)
    if not runnable:
        report["status"] = "BUDGET_BLOCKED"
        OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0

    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(max_parallel, len(runnable))) as pool:
        future_map = {
            pool.submit(
                run_task,
                base_url=base_url,
                model=model,
                api_key=api_key,
                system=system,
                task=task,
                max_tokens=max_tokens,
                reserved_cost=reserve,
            ): task["task_id"]
            for task, reserve in runnable
        }
        for future in as_completed(future_map):
            task_id = future_map[future]
            try:
                results[task_id] = future.result()
            except Exception as exc:
                results[task_id] = {
                    "task_id": task_id,
                    "status": "ERROR",
                    "error_class": type(exc).__name__,
                }

    ordered = [results.get(task["task_id"], {"task_id": task["task_id"], "status": "MISSING"}) for task, _ in runnable]
    report["results"] = ordered
    report["paid_calls"] = sum(1 for row in ordered if row.get("status") in {"SUCCESS", "ERROR", "BLOCKED_MODEL_MISMATCH"})
    report["success_count"] = sum(1 for row in ordered if row.get("status") == "SUCCESS")
    report["status"] = "COUNCIL_COMPLETE" if report["success_count"] == len(ordered) else (
        "COUNCIL_PARTIAL" if report["success_count"] else "COUNCIL_FAILED"
    )
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "paid_calls": report["paid_calls"],
        "success_count": report["success_count"],
        "preflight_cost_usd": report["conservative_preflight_cost_usd"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
