#!/usr/bin/env python3
"""Bounded FREE-only editorial council for the news-video pilot.

The council discovers currently listed exact OpenRouter ``:free`` models at run
time, chooses at most three distinct model families, and reviews the same
source-grounded Japanese short-video brief in parallel. No paid route,
provider fallback, repository mutation, deploy, or publish authority exists.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation
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
CATALOG_URL = "https://openrouter.ai/api/v1/models"
MAX_REQUESTS = 3
MAX_PARALLEL = 3
TIMEOUT_SECONDS = 120
MAX_OUTPUT_TOKENS = 900
PREFERRED_FAMILIES = ("deepseek/", "nvidia/", "qwen/", "z-ai/", "inclusionai/")

SYSTEM = (
    "You are one member of a bounded editorial review board for a Japanese 60-120 second news short. "
    "Use only facts supplied in the mission JSON. Do not invent facts, names, numbers, dates, quotations, or sources. "
    "Review factual risk, pacing, clarity, full-narration subtitle readability, planned Zundamon character placement, "
    "planned credits, and visual-scene fit. The mission contains the planned Zundamon overlay and credit configuration; "
    "do not claim those elements are missing when they are explicitly present in the supplied mission. "
    "Distinguish official OpenAI statements from facts attributed to Reuters/Bloomberg reporting. "
    "Do not turn 'open to slowing' into a claim that OpenAI permanently stopped AI development. Return JSON only."
)


def _load_mission() -> dict[str, Any]:
    payload = json.loads(MISSION.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("mission must be an object")
    return payload


def _prompt(mission: Mapping[str, Any]) -> str:
    compact = {
        "title": mission.get("title"),
        "topic": mission.get("topic"),
        "sources": mission.get("sources"),
        "verified_facts": mission.get("verified_facts"),
        "seed_narration": mission.get("narration"),
        "scene_plan": mission.get("scenes"),
        "zundamon": mission.get("zundamon"),
        "voice": mission.get("voice"),
        "credits": mission.get("credits"),
        "format": mission.get("format"),
        "hard_rules": mission.get("hard_rules"),
        "deterministic_render_contract": {
            "full_spoken_text_subtitle_coverage_required": True,
            "subtitle_intervals_derived_from_real_voicevox_chunk_wav_duration": True,
            "zundamon_overlay_required": True,
            "zundamon_motion_required": True,
            "credits_required": ["VOICEVOX:ずんだもん", "official Zundamon project art source"],
        },
    }
    return (
        "Review the PLANNED mission, not a rendered video. Assess whether the supplied plan is factually safe and "
        "whether its subtitle/Zundamon/credit configuration satisfies the stated contract. Do not infer absence from "
        "anything that is explicitly present below. Return exactly one JSON object with keys: "
        "verdict (PASS|PASS_WITH_CHANGES|REJECT), factual_risks (array), pacing_notes (array), "
        "subtitle_notes (array), visual_notes (array), and one_line_recommendation (string). "
        "Keep every array to at most 4 short items.\nMISSION:\n"
        + json.dumps(compact, ensure_ascii=False, sort_keys=True)
    )


def _zero_price(value: Any) -> bool:
    try:
        return Decimal(str(value)) == 0
    except (InvalidOperation, ValueError, TypeError):
        return False


def _discover_free_models(api_key: str) -> tuple[list[str], dict[str, Any]]:
    request = urllib.request.Request(
        CATALOG_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "hf-site-agent-news-editorial/2.1",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # nosec B310
            payload = json.loads(response.read(5_000_000).decode("utf-8"))
    except Exception as exc:
        return [], {"status": "CATALOG_ERROR", "error": type(exc).__name__}

    rows = payload.get("data") if isinstance(payload, Mapping) and isinstance(payload.get("data"), list) else []
    eligible: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        model = str(row.get("id") or "").strip()
        pricing = row.get("pricing") if isinstance(row.get("pricing"), Mapping) else {}
        if not model.endswith(":free"):
            continue
        if not _zero_price(pricing.get("prompt")) or not _zero_price(pricing.get("completion")):
            continue
        eligible.append(model)

    eligible = sorted(set(eligible))
    selected: list[str] = []
    for family in PREFERRED_FAMILIES:
        hit = next((model for model in eligible if model.startswith(family) and model not in selected), None)
        if hit:
            selected.append(hit)
        if len(selected) >= MAX_REQUESTS:
            break
    if len(selected) < MAX_REQUESTS:
        for model in eligible:
            if model not in selected:
                selected.append(model)
            if len(selected) >= MAX_REQUESTS:
                break
    return selected, {
        "status": "CATALOG_READY" if selected else "NO_EXACT_ZERO_PRICE_FREE_MODELS",
        "eligible_count": len(eligible),
        "selected_count": len(selected),
    }


def _call(model: str, prompt: str, api_key: str, allowed: set[str]) -> dict[str, Any]:
    started = time.monotonic()
    if model not in allowed or not model.endswith(":free"):
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


def _write(result: Mapping[str, Any]) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    mission = _load_mission()
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    base_safety = {
        "repository_write": False,
        "publish": False,
        "deploy": False,
        "paid_fallback": False,
        "auto_top_up": False,
    }
    if not api_key:
        _write({
            "schema_version": "news-video-editorial-council-v2",
            "status": "BLOCKED_MISSING_CREDENTIAL",
            "free_only": True,
            "selected_models": [],
            "success_count": 0,
            "reviews": [],
            **base_safety,
        })
        return 0

    models, catalog = _discover_free_models(api_key)
    if not models:
        _write({
            "schema_version": "news-video-editorial-council-v2",
            "status": "COUNCIL_UNAVAILABLE",
            "catalog": catalog,
            "free_only": True,
            "selected_models": [],
            "success_count": 0,
            "reviews": [],
            **base_safety,
        })
        return 0

    prompt = _prompt(mission)
    allowed = set(models)
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(models))) as pool:
        future_map = {pool.submit(_call, model, prompt, api_key, allowed): model for model in models}
        for future in as_completed(future_map):
            model = future_map[future]
            try:
                results[model] = future.result()
            except Exception as exc:
                results[model] = {"model": model, "status": "ERROR", "error": type(exc).__name__}

    reviews = [results.get(model, {"model": model, "status": "MISSING"}) for model in models]
    success_count = sum(row.get("status") == "SUCCESS" for row in reviews)
    verdicts = [
        str((row.get("review") or {}).get("verdict") or "")
        for row in reviews
        if row.get("status") == "SUCCESS" and isinstance(row.get("review"), Mapping)
    ]
    result = {
        "schema_version": "news-video-editorial-council-v2",
        "status": "COUNCIL_COMPLETE" if success_count else "COUNCIL_UNAVAILABLE",
        "catalog": catalog,
        "free_only": True,
        "max_total_requests": MAX_REQUESTS,
        "selected_models": models,
        "success_count": success_count,
        "verdicts": verdicts,
        "reviews": reviews,
        **base_safety,
    }
    _write(result)
    print(json.dumps({"status": result["status"], "selected_models": models, "success_count": success_count, "verdicts": verdicts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
