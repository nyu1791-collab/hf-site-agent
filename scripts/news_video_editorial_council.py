#!/usr/bin/env python3
"""Bounded FREE-only editorial council for the news-video pilot.

Three exact OpenRouter :free models review the same source-grounded Japanese
short-video brief in parallel. The council cannot mutate the repository,
publish, deploy, spend money, or substitute a paid route.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MISSION = ROOT / "missions" / "news-video-pilot.json"
OUTPUT = ROOT / "artifacts" / "news_video_editorial_council.json"

MODELS = (
    "deepseek/deepseek-r1:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "qwen/qwen3.6-plus:free",
)
MAX_REQUESTS = 3
MAX_PARALLEL = 3
TIMEOUT_SECONDS = 120
MAX_OUTPUT_TOKENS = 900

SYSTEM = (
    "You are one member of a bounded editorial review board for a Japanese 45-75 second news short. "
    "Use only facts supplied in the mission JSON. Do not invent facts, names, numbers, dates, quotations, or sources. "
    "The seed narration is already fact-checked against NASA. Review it for factual risk, pacing, clarity, subtitle readability, "
    "and visual-scene fit. Do not add sensational certainty where the source says the object's nature is unknown. "
    "Return JSON only."
)


def _load_mission() -> dict[str, Any]:
    payload = json.loads(MISSION.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("mission must be an object")
    return payload


def _prompt(mission: Mapping[str, Any]) -> str:
    compact = {
        "topic": mission.get("topic"),
        "source": mission.get("primary_source"),
        "verified_facts": mission.get("verified_facts"),
        "seed_narration": mission.get("narration"),
        "scene_plan": mission.get("scenes"),
        "hard_rules": mission.get("hard_rules"),
    }
    return (
        "Review this mission. Return exactly one JSON object with keys: "
        "verdict (PASS|PASS_WITH_CHANGES|REJECT), factual_risks (array), pacing_notes (array), "
        "subtitle_notes (array), visual_notes (array), and one_line_recommendation (string). "
        "Keep every array to at most 4 short items.\nMISSION:\n"
        + json.dumps(compact, ensure_ascii=False, sort_keys=True)
    )


def _call(model: str, prompt: str, api_key: str) -> dict[str, Any]:
    started = time.monotonic()
    if model not in MODELS or not model.endswith(":free"):
        return {"model": model, "status": "BLOCKED_NONFREE_MODEL"}
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "provider": {"allow_fallbacks": False},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/nyu1791-collab/hf-site-agent",
            "X-Title": "AI Army News Video Editorial Council",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310
            payload = json.loads(response.read(1_000_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {
            "model": model,
            "status": "ERROR",
            "http_status": int(exc.code),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    except Exception as exc:
        return {
            "model": model,
            "status": "ERROR",
            "error": type(exc).__name__,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }

    actual_model = str(payload.get("model") or "").strip()
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    cost = usage.get("cost")
    if isinstance(cost, (int, float)) and cost > 0:
        return {
            "model": model,
            "actual_model": actual_model,
            "status": "BLOCKED_POSITIVE_COST",
            "reported_cost": cost,
        }
    if actual_model and actual_model != model:
        return {
            "model": model,
            "actual_model": actual_model,
            "status": "BLOCKED_MODEL_MISMATCH",
        }
    choices = payload.get("choices")
    first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else {}
    message = first.get("message") if isinstance(first.get("message"), Mapping) else {}
    content = str(message.get("content") or "").strip()
    parsed: Any = None
    if content:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = None
    return {
        "model": model,
        "actual_model": actual_model or model,
        "status": "SUCCESS" if content else "EMPTY",
        "json_valid": isinstance(parsed, Mapping),
        "review": parsed if isinstance(parsed, Mapping) else None,
        "raw_excerpt": content[:4000] if content else "",
        "reported_cost": cost if isinstance(cost, (int, float)) else None,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    mission = _load_mission()
    api_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    if not api_key:
        result = {
            "schema_version": "news-video-editorial-council-v1",
            "status": "BLOCKED_MISSING_CREDENTIAL",
            "free_only": True,
            "requested_models": list(MODELS),
            "success_count": 0,
            "reviews": [],
            "repository_write": False,
            "publish": False,
            "deploy": False,
            "paid_fallback": False,
            "auto_top_up": False,
        }
        OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 0

    prompt = _prompt(mission)
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(MODELS))) as pool:
        future_map = {pool.submit(_call, model, prompt, api_key): model for model in MODELS}
        for future in as_completed(future_map):
            model = future_map[future]
            try:
                results[model] = future.result()
            except Exception as exc:
                results[model] = {"model": model, "status": "ERROR", "error": type(exc).__name__}

    reviews = [results.get(model, {"model": model, "status": "MISSING"}) for model in MODELS]
    success_count = sum(row.get("status") == "SUCCESS" for row in reviews)
    verdicts = [
        str((row.get("review") or {}).get("verdict") or "")
        for row in reviews
        if row.get("status") == "SUCCESS" and isinstance(row.get("review"), Mapping)
    ]
    result = {
        "schema_version": "news-video-editorial-council-v1",
        "status": "COUNCIL_COMPLETE" if success_count else "COUNCIL_UNAVAILABLE",
        "free_only": True,
        "max_total_requests": MAX_REQUESTS,
        "requested_models": list(MODELS),
        "success_count": success_count,
        "verdicts": verdicts,
        "reviews": reviews,
        "repository_write": False,
        "publish": False,
        "deploy": False,
        "paid_fallback": False,
        "auto_top_up": False,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "success_count": success_count, "verdicts": verdicts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
