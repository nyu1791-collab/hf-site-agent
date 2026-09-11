#!/usr/bin/env python3
"""Run a bounded FREE-ONLY architecture council in true parallel.

DeepSeek, NVIDIA and Qwen receive the same redacted architecture brief at the
same time through exact OpenRouter ``:free`` model ids.  The council reviews
both the media pipeline and the replaceable framework-adapter layer.

Hard boundaries:
- exact ``:free`` ids only; no model substitution or paid fallback;
- maximum one request per reviewer and three requests total;
- no repository write, deploy, publish, payment, secret mutation or tool use;
- positive provider-reported cost is treated as a blocked result;
- model failures are isolated so one reviewer cannot cancel the others.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts" / "free_media_council_review.json"

ARCHITECTURE_FILES = (
    ROOT / "config" / "free_media_capability_pool.json",
    ROOT / "config" / "framework_adapter_layer.json",
    ROOT / "config" / "framework_plugin_registry.json",
)

# Exact ids are deliberately fixed. Never strip ``:free`` or substitute a
# same-name paid route when one of these endpoints is unavailable.
MODELS = [
    "deepseek/deepseek-r1:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "qwen/qwen3.6-plus:free",
]
MAX_PARALLEL_REVIEWERS = 3
MAX_TOTAL_REQUESTS = 3
REQUEST_TIMEOUT_SECONDS = 120

SYSTEM = (
    "You are an independent AI-organization and media-systems architect. "
    "Review only the supplied architecture. Optimize for zero-cost operation, "
    "quality, speed, factual accuracy, replaceability, and repeatability. "
    "The native AI Army scheduler remains the authority. External frameworks "
    "are execution engines only. Never recommend paid fallback, auto top-up, "
    "repository write access, publishing authority, payment authority, or secret access."
)

QUESTION = """Review this FREE-ONLY AI Army architecture.

Goal A — 45-60 second Japanese news shorts:
- Prefer licensed real-world photos and existing approved assets; no generated image/video by default.
- Pipeline: research -> script -> fact check -> edit plan -> external/app voice handoff or free TTS -> subtitle alignment -> audio QA -> FFmpeg edit -> independent multimodal QA.
- Agents may own multiple adjacent roles when this reduces handoffs, but final fact/quality audit should be independent from the primary producer when possible.

Goal B — replaceable orchestration frameworks:
- Keep the native V4 scheduler/routing/safety boundaries authoritative.
- Allow LangGraph, Microsoft Agent Framework, AutoGen, CrewAI and GitHub Copilot to plug in behind one canonical task/result contract.
- Frameworks may not increase authority, silently install packages, enable paid routes, mutate secrets, publish, deploy, merge, or write the repository.
- Prefer consolidation over micro-agent fragmentation and preserve Single Writer final integration.

Return concise JSON-like prose with exactly these sections:
1. KEEP: what is strong
2. CHANGE: top 7 changes ranked
3. FRAMEWORK_MAP: best use of native V4, LangGraph, AutoGen, CrewAI, Microsoft Agent Framework, Copilot
4. MODEL_MAP: best use of DeepSeek, NVIDIA, Qwen, Google-if-free, open-source audio/ASR, FFmpeg
5. PARALLELISM: what should run concurrently vs sequentially
6. SELF_REVIEW_RISK: where multi-role ownership becomes dangerous
7. MINIMUM_PIPELINE: smallest high-quality zero-cost architecture
8. FINAL_VERDICT: PASS / PASS_WITH_CHANGES / REJECT

Architecture files:
"""


def _architecture_text() -> str:
    chunks: list[str] = []
    total = 0
    for path in ARCHITECTURE_FILES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        remaining = max(0, 42000 - total)
        if remaining <= 0:
            break
        clipped = text[:remaining]
        chunks.append(f"\n--- {path.relative_to(ROOT)} ---\n{clipped}")
        total += len(clipped)
    return "".join(chunks)


def call(model: str, prompt: str, api_key: str) -> dict[str, Any]:
    started = time.monotonic()
    if model not in MODELS or not model.endswith(":free"):
        return {"model": model, "status": "BLOCKED_NONFREE_REQUEST", "elapsed_seconds": 0.0}

    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 1800,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/nyu1791-collab/hf-site-agent",
            "X-Title": "AI Army FREE Architecture Council",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")[:1000]
        return {
            "model": model,
            "status": "ERROR",
            "http_status": exc.code,
            "error": text,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    except Exception as exc:
        return {
            "model": model,
            "status": "ERROR",
            "error": type(exc).__name__,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }

    actual_model = str(payload.get("model") or model)
    choice = (payload.get("choices") or [{}])[0]
    content = ((choice.get("message") or {}).get("content") or "")
    usage = payload.get("usage") or {}
    cost = usage.get("cost")
    if isinstance(cost, (int, float)) and cost > 0:
        return {
            "model": model,
            "actual_model": actual_model,
            "status": "BLOCKED_POSITIVE_COST_REPORTED",
            "reported_cost": cost,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    return {
        "model": model,
        "actual_model": actual_model,
        "status": "SUCCESS" if content else "EMPTY",
        "content": content[:12000],
        "reported_cost": cost if isinstance(cost, (int, float)) else None,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def run_parallel_council(prompt: str, api_key: str) -> list[dict[str, Any]]:
    if len(MODELS) > MAX_TOTAL_REQUESTS:
        raise RuntimeError("council exceeds MAX_TOTAL_REQUESTS")
    results: dict[str, dict[str, Any]] = {}
    workers = min(MAX_PARALLEL_REVIEWERS, len(MODELS))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="free-council") as pool:
        futures = {pool.submit(call, model, prompt, api_key): model for model in MODELS}
        for future in as_completed(futures):
            model = futures[future]
            try:
                results[model] = future.result()
            except Exception as exc:  # isolate unexpected worker failures
                results[model] = {"model": model, "status": "ERROR", "error": type(exc).__name__}
    # Stable configured order keeps artifacts deterministic enough for diffing.
    return [results.get(model, {"model": model, "status": "ERROR", "error": "missing_result"}) for model in MODELS]


def main() -> int:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        print("OPENROUTER_API_KEY missing; fail closed", file=sys.stderr)
        return 2
    if not MODELS or len(MODELS) > MAX_TOTAL_REQUESTS or any(not m.endswith(":free") for m in MODELS):
        print("invalid FREE-only model set; fail closed", file=sys.stderr)
        return 2

    prompt = QUESTION + _architecture_text()
    council_started = time.monotonic()
    reviews = run_parallel_council(prompt, api_key)
    wall_seconds = round(time.monotonic() - council_started, 3)
    result = {
        "schema_version": "free-media-council-review-v2",
        "execution_mode": "PARALLEL",
        "max_parallel_reviewers": MAX_PARALLEL_REVIEWERS,
        "max_total_requests": MAX_TOTAL_REQUESTS,
        "wall_seconds": wall_seconds,
        "free_only": True,
        "generic_paid_fallback": False,
        "auto_top_up": False,
        "requested_models": MODELS,
        "review_count": len(reviews),
        "success_count": sum(1 for r in reviews if r.get("status") == "SUCCESS"),
        "reviews": reviews,
        "repository_write": False,
        "deploy": False,
        "publish": False,
        "secret_mutation": False,
        "payment_authority": False,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("review_count", "success_count", "free_only", "execution_mode", "wall_seconds")}, sort_keys=True))
    return 0 if result["success_count"] >= 2 else 3


if __name__ == "__main__":
    raise SystemExit(main())
