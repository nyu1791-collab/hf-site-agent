#!/usr/bin/env python3
"""Short-lived empirical health ranking for exact-free OpenRouter workers.

Evidence may contain global metrics plus optional domain_stats. Domain-specific
metrics win when present; otherwise global metrics are used. Evidence expires
automatically and can only reorder already-eligible exact-free candidates.
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


def load_recent_evidence(*, now: datetime | None = None, path: Path = EVIDENCE_PATH) -> dict[str, dict[str, Any]]:
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
        # Evidence without a valid expiry may be displayed for diagnosis but
        # must never influence a routing decision.  Treating it as fresh would
        # make an old or malformed record silently become a permanent ranking.
        expires = _parse_utc(raw.get("valid_until_utc"))
        if expires is None or expires <= current:
            continue
        active[str(model)] = dict(raw)
    return active


def domain_evidence(
    raw: Mapping[str, Any] | None,
    domain: str | None = None,
    *,
    allow_global_fallback: bool = True,
) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    name = str(domain or "").strip()
    stats = raw.get("domain_stats")
    if name and isinstance(stats, Mapping):
        scoped = stats.get(name)
        if isinstance(scoped, Mapping):
            merged = dict(raw)
            merged.update(dict(scoped))
            return merged
    return raw if allow_global_fallback else {}


def evidence_quality_summary(
    raw: Mapping[str, Any] | None,
    *,
    domain: str | None = None,
    allow_global_fallback: bool = True,
) -> dict[str, Any]:
    """Return a small, deterministic reliability summary for one domain.

    Latency is useful for ordering already-safe candidates, but it is not
    evidence that a worker is correct.  Routing code uses this summary to
    decide whether health is strong enough to remove a Jev judgment surface.
    """
    scoped = domain_evidence(
        raw,
        domain,
        allow_global_fallback=allow_global_fallback,
    )
    successes = max(0, int(scoped.get("successes", 0) or 0))
    quality_failures = max(0, int(scoped.get("quality_failures", 0) or 0))
    rate_limits = max(0, int(scoped.get("rate_limits", 0) or 0))
    observed_outcomes = successes + quality_failures
    quality_pass_rate = successes / observed_outcomes if observed_outcomes else None
    return {
        "successes": successes,
        "quality_failures": quality_failures,
        "rate_limits": rate_limits,
        "observed_outcomes": observed_outcomes,
        "quality_pass_rate": quality_pass_rate,
        "has_domain_evidence": bool(scoped),
    }


def evidence_penalty(raw: Mapping[str, Any], *, domain: str | None = None) -> tuple[int, int, int, int, float]:
    scoped = domain_evidence(raw, domain)
    has_evidence = bool(scoped)
    rate_limits = max(0, int(scoped.get("rate_limits", 0) or 0))
    quality_failures = max(0, int(scoped.get("quality_failures", 0) or 0))
    successes = max(0, int(scoped.get("successes", 0) or 0))
    try:
        latency = max(0.0, float(scoped.get("avg_latency_ms") or 0.0))
    except (TypeError, ValueError):
        latency = 0.0
    severe_slow = 1 if latency >= 30_000 else 0
    slow = 1 if latency >= 10_000 else 0
    latency_score = (latency - successes * 10_000.0) if has_evidence else 5_000.0
    return (rate_limits, quality_failures, severe_slow, slow, latency_score)


def rank_candidates(
    candidates: Sequence[str],
    *,
    evidence: Mapping[str, Mapping[str, Any]] | None = None,
    domain: str | None = None,
) -> list[str]:
    empirical = evidence or {}
    ranked: list[tuple[tuple[int, int, int, int, float], int, str]] = []
    for index, model in enumerate(candidates):
        raw = empirical.get(str(model)) if isinstance(empirical, Mapping) else None
        penalty = evidence_penalty(raw if isinstance(raw, Mapping) else {}, domain=domain)
        ranked.append((penalty, index, str(model)))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [model for _penalty, _index, model in ranked]


def proven_models(
    *,
    evidence: Mapping[str, Mapping[str, Any]] | None = None,
    domain: str | None = None,
) -> list[str]:
    empirical = evidence or {}
    good = []
    for model, raw in empirical.items():
        if not isinstance(raw, Mapping):
            continue
        scoped = domain_evidence(raw, domain)
        if int(scoped.get("successes", 0) or 0) <= 0:
            continue
        if int(scoped.get("rate_limits", 0) or 0) > 0:
            continue
        if int(scoped.get("quality_failures", 0) or 0) > 0:
            continue
        good.append(str(model))
    return rank_candidates(good, evidence=empirical, domain=domain)


def profile_suffix(
    model: str,
    evidence: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    domain: str | None = None,
) -> str:
    raw = (evidence or {}).get(model) if isinstance(evidence, Mapping) else None
    if not isinstance(raw, Mapping):
        return "recent_evidence=none"
    scoped = domain_evidence(raw, domain)
    quality = evidence_quality_summary(raw, domain=domain)
    pass_rate = quality["quality_pass_rate"]
    return (
        f"recent_domain={domain or 'global'}; "
        f"recent_successes={quality['successes']}; "
        f"recent_quality_failures={quality['quality_failures']}; "
        f"recent_rate_limits={quality['rate_limits']}; "
        f"recent_samples={quality['observed_outcomes']}; "
        f"recent_quality_pass_rate={'unknown' if pass_rate is None else f'{pass_rate:.2f}'}; "
        f"recent_avg_latency_ms={float(scoped.get('avg_latency_ms') or 0.0):.1f}"
    )


def merge_proven_into_candidates(
    base_candidates: Sequence[str],
    *,
    catalog_model_ids: set[str],
    evidence: Mapping[str, Mapping[str, Any]] | None = None,
    domain: str | None = None,
    max_candidates: int = 12,
) -> list[str]:
    empirical = evidence or {}
    base = [str(x) for x in base_candidates if str(x) in catalog_model_ids]
    # Evidence can reorder the prevalidated base pool only.  In particular it
    # must not promote a catalog entry (including a paid sibling) that was not
    # eligible for the current task before health ranking.
    base_set = set(base)
    proven = [
        x for x in proven_models(evidence=empirical, domain=domain)
        if x in catalog_model_ids and x in base_set
    ]
    merged: list[str] = []
    for model in [*base[:4], *proven, *base[4:]]:
        if model not in merged:
            merged.append(model)
    return rank_candidates(merged, evidence=empirical, domain=domain)[:max_candidates]


__all__ = [
    "EVIDENCE_PATH",
    "domain_evidence",
    "evidence_quality_summary",
    "evidence_penalty",
    "load_recent_evidence",
    "merge_proven_into_candidates",
    "profile_suffix",
    "proven_models",
    "rank_candidates",
]
