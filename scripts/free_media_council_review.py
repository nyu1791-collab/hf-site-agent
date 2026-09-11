#!/usr/bin/env python3
"""Run a bounded FREE-ONLY media-organization council via exact OpenRouter :free models.

No repository writes, no publishing, no paid fallback, no model substitution.
Each reviewer receives the same redacted architecture brief and returns text only.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "free_media_capability_pool.json"
OUTPUT = ROOT / "artifacts" / "free_media_council_review.json"

MODELS = [
    "deepseek/deepseek-r1:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "qwen/qwen3.6-plus:free",
]

SYSTEM = (
    "You are an independent media-systems architect. Review only the supplied architecture. "
    "Optimize for zero-cost production, quality, speed, factual accuracy, and repeatability. "
    "Never recommend paid fallback, auto top-up, repository write access, publishing, or secret access."
)

QUESTION = """Review this FREE-ONLY AI video-production organization for 45-60 second Japanese news shorts.
We reuse 5 fixed images plus properly licensed public photos; no generative video is required by default.
Target pipeline: research -> script -> fact check -> edit plan -> Japanese TTS -> subtitle draft/alignment -> audio QA -> FFmpeg edit -> independent multimodal QA.
Agents may hold multiple roles if efficient, but final audit should be independent from the primary producer when possible.

Return concise JSON-like prose with exactly these sections:
1. KEEP: what is strong
2. CHANGE: top 5 changes ranked
3. ROLE_MAP: best work for DeepSeek, NVIDIA, Qwen, Google-if-free, open-source TTS/ASR, FFmpeg
4. SELF_REVIEW_RISK: where multi-role assignment becomes dangerous
5. MINIMUM_PIPELINE: smallest high-quality zero-cost pipeline
6. FINAL_VERDICT: PASS / PASS_WITH_CHANGES / REJECT

Architecture:
"""


def call(model: str, prompt: str, api_key: str) -> dict:
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
            "X-Title": "AI Army Free Media Council",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")[:1000]
        return {"model": model, "status": "ERROR", "http_status": exc.code, "error": text}
    except Exception as exc:
        return {"model": model, "status": "ERROR", "error": type(exc).__name__}

    actual_model = str(payload.get("model") or model)
    if ":free" not in model:
        return {"model": model, "status": "BLOCKED_NONFREE_REQUEST"}
    choice = (payload.get("choices") or [{}])[0]
    content = ((choice.get("message") or {}).get("content") or "")
    usage = payload.get("usage") or {}
    cost = usage.get("cost")
    # Fail closed if provider reports a positive cost.
    if isinstance(cost, (int, float)) and cost > 0:
        return {"model": model, "status": "BLOCKED_POSITIVE_COST_REPORTED", "reported_cost": cost}
    return {
        "model": model,
        "actual_model": actual_model,
        "status": "SUCCESS" if content else "EMPTY",
        "content": content[:12000],
        "reported_cost": cost if isinstance(cost, (int, float)) else None,
    }


def main() -> int:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        print("OPENROUTER_API_KEY missing; fail closed", file=sys.stderr)
        return 2
    config_text = CONFIG.read_text(encoding="utf-8")
    prompt = QUESTION + config_text[:28000]
    reviews = [call(model, prompt, api_key) for model in MODELS]
    result = {
        "schema_version": "free-media-council-review-v1",
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
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("review_count", "success_count", "free_only", "generic_paid_fallback")}, sort_keys=True))
    return 0 if result["success_count"] >= 2 else 3


if __name__ == "__main__":
    raise SystemExit(main())
