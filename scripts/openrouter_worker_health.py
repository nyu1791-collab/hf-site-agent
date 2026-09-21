#!/usr/bin/env python3
"""Short-lived empirical health ranking for exact-free OpenRouter workers.

This layer never makes a paid model eligible and never overrides task capability
filters. It only reorders already-eligible exact-free candidates using recent
live evidence. Evidence expires automatically.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / "config" / "openrouter_worker_recent_evidence.json"


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_recent_evidence(
    *,
    now: datetime | None = None,
    path: Path = EVIDENCE_PATH,
) -> dict[str, dict[str, Any]]:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    models = payload.get("models") if isinstance(payload, Mapping) else None
    if not isinstance(models, Mapping):
        return {}
    active: dict[str, dict[str, Any]] = {}
    for model, raw in models.items():
        if not isinstance(raw, Mapping):
            continue
        expires = _parse_utc(raw.get("valid_until_utc"))
        if expires is not None and expires <= current:
            continue
        active[str(model)] = dict(raw)
    return active


def evidence_penalty(raw: Mapping[str, Any]) -> tuple[int, int, int, int, float]:
    rate_limits = max(0, int(raw.get("rate_limits", 0) or 0))
    quality_failures = max(0, int(raw.get("quality_failures", 0) or 0))
    successes = max(0, int(raw.get("successes", 0) or 0))
    try:
        latency = max(0.0, float(raw.get("avg_latency_ms") or 0.0))
    except (TypeError, ValueError):
        latency = 0.0
    severe_slow = 1 if latency >= 30_000 else 0
    slow = 1 if latency >= 10_000 else 0
    # Lower tuple is better. 429 and quality failures dominate; proven success
    # then improves ranking, while high latency prevents slow successful models
    # from beating fast successful ones.
    return (rate_limits, quality_failures, severe_slow, slow, latency - successes * 2_000.0)


def rank_candidates(
    candidates: Sequence[str],
    *,
    evidence: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    empirical = evidence or {}
    ranked: list[tuple[tuple[int, int, int, int, float], int, str]] = []
    for index, model in enumerate(candidates):
        raw = empirical.get(str(model)) if isinstance(empirical, Mapping) else None
        penalty = evidence_penalty(raw if isinstance(raw, Mapping) else {})
        ranked.append((penalty, index, str(model)))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [model for _penalty, _index, model in ranked]


def proven_models(
    *,
    evidence: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    empirical = evidence or {}
    good = []
    for model, raw in empirical.items():
        if not isinstance(raw, Mapping):
            continue
        if int(raw.get("successes", 0) or 0) <= 0:
            continue
        if int(raw.get("rate_limits", 0) or 0) > 0:
            continue
        if int(raw.get("quality_failures", 0) or 0) > 0:
            continue
        good.append(str(model))
    return rank_candidates(good, evidence=empirical)


def profile_suffix(model: str, evidence: Mapping[str, Mapping[str, Any]] | None = None) -> str:
    raw = (evidence or {}).get(model) if isinstance(evidence, Mapping) else None
    if not isinstance(raw, Mapping):
        return "recent_evidence=none"
    return (
        f"recent_successes={int(raw.get('successes', 0) or 0)}; "
        f"recent_quality_failures={int(raw.get('quality_failures', 0) or 0)}; "
        f"recent_rate_limits={int(raw.get('rate_limits', 0) or 0)}; "
        f"recent_avg_latency_ms={float(raw.get('avg_latency_ms') or 0.0):.1f}"
    )


def merge_proven_into_candidates(
    base_candidates: Sequence[str],
    *,
    catalog_model_ids: set[str],
    evidence: Mapping[str, Mapping[str, Any]] | None = None,
    max_candidates: int = 12,
) -> list[str]:
    empirical = evidence or {}
    base = [str(x) for x in base_candidates if str(x) in catalog_model_ids]
    proven = [x for x in proven_models(evidence=empirical) if x in catalog_model_ids]
    merged: list[str] = []
    for model in [*base[:4], *proven, *base[4:]]:
        if model not in merged:
            merged.append(model)
    return rank_candidates(merged, evidence=empirical)[:max_candidates]


__all__ = [
    "EVIDENCE_PATH",
    "evidence_penalty",
    "load_recent_evidence",
    "merge_proven_into_candidates",
    "profile_suffix",
    "proven_models",
    "rank_candidates",
]
