#!/usr/bin/env python3
"""Run a bounded FREE-ONLY media architecture council in true parallel.

Independent zero-price reviewers inspect the same redacted media brief. The
council has no repository-write, deploy, publish, payment, secret-mutation, or
paid-fallback authority. ChatGPT remains the integrator and only evidence-backed
recommendations are promoted.
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
    ROOT / "docs" / "MEDIA_PIPELINE.md",
    ROOT / "docs" / "LONGFORM_VIDEO_RELIABILITY_RESEARCH.md",
    ROOT / "scripts" / "render_zundamon_news_longform.py",
    ROOT / "scripts" / "render_zundamon_news_longform_robust.py",
    ROOT / "scripts" / "resume_zundamon_news_longform.py",
    ROOT / "config" / "free_media_capability_pool.json",
    ROOT / "config" / "framework_adapter_layer.json",
    ROOT / "config" / "framework_plugin_registry.json",
)

# Exact :free endpoints verified as zero-price on OpenRouter on 2026-09-12.
# The previous DeepSeek R1 and Qwen3.6 Plus free endpoints returned 404 with a
# paid-transition message. They are deliberately NOT replaced by paid siblings.
MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "dots-studio/dots-3-note-preview:free",
    "inclusionai/ling-3.0-flash:free",
]
MAX_PARALLEL_REVIEWERS = 3
MAX_TOTAL_REQUESTS = 3
REQUEST_TIMEOUT_SECONDS = 150
MAX_ARCHITECTURE_CHARS = 60000

SYSTEM = (
    "You are an independent long-form AI-video reliability architect. "
    "Audit only the supplied evidence and code. Optimize for deterministic, "
    "zero-video-SaaS-cost production, recoverability, bounded resource use, "
    "audio/video/subtitle synchronization, and fail-closed behavior. "
    "VOICEVOX Zundamon plus Python/FFmpeg is the standard production path. "
    "Do not recommend Runway, Fal, Descript, VEED, HeyGen, Higgsfield, paid "
    "video-generation/editing SaaS, paid fallback, auto top-up, repository "
    "write access, secret access, publishing authority, deployment authority, "
    "or PR merge authority. Treat official docs and reproducible code behavior "
    "as stronger evidence than opinions. Be explicit when a claim is uncertain."
)

QUESTION = """Independently audit this long-form Zundamon video system.

Primary goal — reliable 4-10 minute Japanese explainers/news videos:
- Do NOT render a real video in this review.
- Build scenes independently, validate each scene, then concat.
- Preserve healthy assets/audio/scenes across failures.
- Narration is local VOICEVOX Zundamon; duration comes from actual WAV probes.
- Final scene contract is 1080x1920, 30 fps, normalized codec/pixel/audio format.
- External paid/freemium video editing/generation SaaS is prohibited.
- The final creative review belongs to the user; machine QA should not become a delivery bottleneck.

Observed code risks that must be checked, not blindly accepted:
A. The base renderer's reset_workdirs() appears to delete reusable audio/images/poses/composites/clips at run start.
B. The resume script appears to call base.make_scene()/base.write_ass(), while the current base renderer exposes render_scene()/write_scene_ass()/write_global_ass(). Determine whether this is a stale-contract defect.
C. Resume reuse is largely based on file existence/size rather than content hashes and a full media contract.
D. The robust workflow uses voicevox/voicevox_engine:cpu-latest, reducing toolchain reproducibility.
E. Resume workflow is tied to a specific historical artifact/run, reducing generic checkpoint recovery.
F. A job-level timeout exists, but per-scene/process watchdogs are limited.

Candidate improvements from official FFmpeg/VOICEVOX/GitHub Actions docs and OSS field reports:
- content-addressed caches for voice/assets/subtitles/scenes;
- atomic .partial -> ffprobe validate -> rename commit;
- strict preflight for actual ffmpeg/ffprobe path/version, libx264, aac, libass, fonts, disk, VOICEVOX version/style;
- exact scene media-contract validation before concat copy;
- explicit A/V drift budget and subtitle timeline invariants;
- declarative timeline_manifest.json as editing source of truth;
- bounded retries classified into retryable/method-change/permanent;
- disk/RAM/CPU parallelism governor;
- version/digest pinning and toolchain provenance;
- generic checkpoint restore rather than hardcoded run id;
- structured error packets and failure artifact preservation.

Return concise JSON-like prose with EXACTLY these sections:
1. VERIFIED_BUGS: classify A-F as CONFIRMED / LIKELY / NOT_PROVEN with code-based reasoning
2. TOP_FAILURE_MODES: top 12 long-form failure modes ranked by severity x likelihood
3. ACCEPT: candidate controls that should become permanent rules now
4. MODIFY: useful controls that need changed thresholds/design before adoption
5. REJECT: ideas that add complexity without enough reliability gain
6. TOP10_IMPLEMENTATION: ordered first 10 changes, each with validation method
7. CACHE_KEY_REVIEW: fields required for voice/visual/subtitle/scene cache identity
8. MEDIA_CONTRACT: ffprobe fields required before concat copy and drift checks
9. CI_STRATEGY: cache vs artifact, concurrency, fail-fast, per-scene timeout, recovery
10. SLO_KPI: measurable reliability and efficiency targets; flag any speculative threshold
11. MINIMUM_ARCHITECTURE: smallest robust zero-video-SaaS-cost long-form system
12. FINAL_VERDICT: PASS / PASS_WITH_CHANGES / REJECT

Do not invent test results. Do not claim a provider/tool ran if the supplied evidence does not show it.

Architecture and evidence files:
"""


def _architecture_text() -> str:
    chunks: list[str] = []
    total = 0
    for path in ARCHITECTURE_FILES:
        if not path.exists():
            continue
        remaining = MAX_ARCHITECTURE_CHARS - total
        if remaining <= 0:
            break
        text = path.read_text(encoding="utf-8", errors="replace")
        clipped = text[:remaining]
        chunks.append(f"\n--- {path.relative_to(ROOT)} ---\n{clipped}")
        total += len(clipped)
    return "".join(chunks)


def call(model: str, prompt: str, api_key: str) -> dict[str, Any]:
    started = time.monotonic()
    if model not in MODELS or not model.endswith(":free"):
        return {"model": model, "status": "BLOCKED_NONFREE_REQUEST", "elapsed_seconds": 0.0}

    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.15,
            "max_tokens": 2400,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/nyu1791-collab/hf-site-agent",
            "X-Title": "AI Army FREE Longform Reliability Council",
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
        "content": content[:16000],
        "reported_cost": cost if isinstance(cost, (int, float)) else None,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def run_parallel_council(prompt: str, api_key: str) -> list[dict[str, Any]]:
    if len(MODELS) > MAX_TOTAL_REQUESTS:
        raise RuntimeError("council exceeds MAX_TOTAL_REQUESTS")
    results: dict[str, dict[str, Any]] = {}
    workers = min(MAX_PARALLEL_REVIEWERS, len(MODELS))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="free-longform-council") as pool:
        futures = {pool.submit(call, model, prompt, api_key): model for model in MODELS}
        for future in as_completed(futures):
            model = futures[future]
            try:
                results[model] = future.result()
            except Exception as exc:
                results[model] = {"model": model, "status": "ERROR", "error": type(exc).__name__}
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
    started = time.monotonic()
    reviews = run_parallel_council(prompt, api_key)
    result = {
        "schema_version": "free-media-council-longform-v4",
        "mission": "LONGFORM_RELIABILITY_REVIEW",
        "execution_mode": "PARALLEL",
        "max_parallel_reviewers": MAX_PARALLEL_REVIEWERS,
        "max_total_requests": MAX_TOTAL_REQUESTS,
        "wall_seconds": round(time.monotonic() - started, 3),
        "free_only": True,
        "generic_paid_fallback": False,
        "auto_top_up": False,
        "requested_models": MODELS,
        "review_count": len(reviews),
        "success_count": sum(1 for row in reviews if row.get("status") == "SUCCESS"),
        "reviews": reviews,
        "repository_write": False,
        "deploy": False,
        "publish": False,
        "secret_mutation": False,
        "payment_authority": False,
        "video_rendered": False,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("mission", "review_count", "success_count", "free_only", "execution_mode", "wall_seconds")}, sort_keys=True))
    return 0 if result["success_count"] >= 2 else 3


if __name__ == "__main__":
    raise SystemExit(main())
