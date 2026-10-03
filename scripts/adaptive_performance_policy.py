#!/usr/bin/env python3
"""Adaptive quality/performance policy for the NVIDIA + Google AI army.

The policy spends extra inference only when task importance justifies it.
It is provider-call agnostic and contains no credentials or execution rights.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class PerformanceProfile:
    name: str
    attempts: int
    output_tokens: int
    prompt_chars: int
    response_chars: int
    max_revisions: int
    max_iterations: int
    mission_token_budget: int
    mission_request_budget: int


NORMAL = PerformanceProfile(
    name="NORMAL",
    attempts=1,
    output_tokens=4_096,
    prompt_chars=48_000,
    response_chars=64_000,
    max_revisions=2,
    max_iterations=4,
    mission_token_budget=24_576,
    mission_request_budget=10,
)

IMPORTANT = PerformanceProfile(
    name="IMPORTANT",
    attempts=2,
    output_tokens=8_192,
    prompt_chars=80_000,
    response_chars=96_000,
    max_revisions=3,
    max_iterations=5,
    mission_token_budget=49_152,
    mission_request_budget=16,
)

CRITICAL = PerformanceProfile(
    name="CRITICAL",
    attempts=3,
    output_tokens=12_288,
    prompt_chars=120_000,
    response_chars=144_000,
    max_revisions=4,
    max_iterations=6,
    mission_token_budget=81_920,
    mission_request_budget=24,
)


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def classify_importance(*, role: str = "", risk_level: str = "", complexity_level: int = 1, metadata: Mapping[str, Any] | None = None) -> str:
    """Return NORMAL / IMPORTANT / CRITICAL using deterministic task facts.

    Explicit ``performance_importance`` metadata wins. Otherwise repository,
    migration, concurrency, resume, security-boundary and release-critical
    work receive extra independent attempts.
    """
    metadata = metadata if isinstance(metadata, Mapping) else {}
    explicit = str(metadata.get("performance_importance") or "").upper()
    if explicit in {"NORMAL", "IMPORTANT", "CRITICAL"}:
        return explicit

    haystack = " ".join(
        [
            _text(role),
            _text(metadata.get("objective")),
            _text(metadata.get("change_type")),
            _text(metadata.get("impact")),
        ]
    )
    critical_terms = (
        "production", "migration", "concurrency", "race", "resume", "ledger",
        "release", "data loss", "auth", "billing", "secret", "rollback",
        "schema migration", "orchestrator", "mission integrity",
    )
    important_terms = (
        "implementation", "repository", "adapter", "workflow", "integration",
        "regression", "performance", "refactor", "provider", "api", "staging",
    )
    risk = str(risk_level or "").upper()
    complexity = complexity_level if isinstance(complexity_level, int) and not isinstance(complexity_level, bool) else 1

    if risk in {"HIGH", "CRITICAL"} or complexity >= 4 or any(term in haystack for term in critical_terms):
        return "CRITICAL"
    if risk == "MEDIUM" or complexity >= 2 or any(term in haystack for term in important_terms):
        return "IMPORTANT"
    return "NORMAL"


def profile_for_task(*, role: str = "", risk_level: str = "", complexity_level: int = 1, metadata: Mapping[str, Any] | None = None) -> PerformanceProfile:
    level = classify_importance(
        role=role,
        risk_level=risk_level,
        complexity_level=complexity_level,
        metadata=metadata,
    )
    return {"NORMAL": NORMAL, "IMPORTANT": IMPORTANT, "CRITICAL": CRITICAL}[level]


def profile_metadata(profile: PerformanceProfile) -> dict[str, Any]:
    return {
        "performance_profile": profile.name,
        "performance_attempts": profile.attempts,
        "performance_output_tokens": profile.output_tokens,
        "performance_prompt_chars": profile.prompt_chars,
        "performance_response_chars": profile.response_chars,
        "performance_max_revisions": profile.max_revisions,
        "performance_max_iterations": profile.max_iterations,
        "performance_mission_token_budget": profile.mission_token_budget,
        "performance_mission_request_budget": profile.mission_request_budget,
    }


__all__ = [
    "PerformanceProfile",
    "NORMAL",
    "IMPORTANT",
    "CRITICAL",
    "classify_importance",
    "profile_for_task",
    "profile_metadata",
]
