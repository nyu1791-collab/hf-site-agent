#!/usr/bin/env python3
"""Deterministic control primitives for the AI Army V4 scheduler.

The module is deliberately provider-agnostic and side-effect free.  It contains
no network calls, credentials, repository writes, deploy/publish actions, paid
fallback, or model invocation.  The V4 scheduler consumes these primitives to
implement the agreed sequence:

P0-A dependency result version/hash guards
P0-B adaptive exact-model concurrency
P0-C semantic work deduplication
P1-B concurrency hysteresis
P1-C bounded fairness aging
P1-D result confidence contracts

P1-A mission-local memory freshness is implemented in the V4 agent-registry
adapter because it needs role-session state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence


RCC_VERSION = "ai-army-rcc-v1"
DEPENDENCY_CONTRACT_VERSION = "dependency-version-guard-v1"
OBJECTIVE_FINGERPRINT_VERSION = "objective-fingerprint-v1"
TRANSIENT_PRESSURE_ERRORS = frozenset({
    "RATE_LIMIT",
    "RATE_LIMITED",
    "HTTP_429",
    "PROVIDER_5XX",
    "SERVER_5XX",
    "NETWORK",
    "NETWORK_ERROR",
    "TIMEOUT",
    "EMPTY_RESPONSE",
})
_WS_RE = re.compile(r"\s+")


def _bounded_text(value: Any, limit: int = 5000) -> str:
    return str(value or "")[: max(1, int(limit))]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_digest(value: Any, *, length: int = 24) -> str:
    """Return the same compact SHA-256 shape used by mission_integrity.result_hash."""
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return digest[: max(8, min(64, int(length)))]


def normalize_objective_text(value: Any) -> str:
    """Normalize task text without fuzzy matching or semantic model calls."""
    return _WS_RE.sub(" ", _bounded_text(value, 5000).strip().lower())


def objective_fingerprint(task: Any) -> str:
    """Fingerprint equivalent work while preserving dependency/write semantics.

    ``AgentTask`` exposes depends_on/read_set/write_set as first-class fields.
    Different write scopes therefore never join, even when objective text is
    otherwise identical.
    """
    payload = {
        "contract": OBJECTIVE_FINGERPRINT_VERSION,
        "slot": _bounded_text(getattr(task, "slot", ""), 160).strip().upper(),
        "objective": normalize_objective_text(getattr(task, "objective", "")),
        "depends_on": sorted({_bounded_text(item, 128) for item in getattr(task, "depends_on", ())}),
        "read_set": sorted({_bounded_text(item, 500) for item in getattr(task, "read_set", ())}),
        "write_set": sorted({_bounded_text(item, 500) for item in getattr(task, "write_set", ())}),
    }
    return stable_digest(payload, length=32)


def result_identity(
    *,
    task_id: str,
    revision: int,
    status: str,
    binding: Mapping[str, Any],
    output: Any,
    summary: str = "",
    quality_score: float | None = None,
    error_class: str | None = None,
) -> str:
    """Bind one result generation to task identity, execution body and output."""
    payload = {
        "task_id": _bounded_text(task_id, 128),
        "revision": max(0, int(revision)),
        "status": _bounded_text(status, 32).upper(),
        "binding": {
            "provider": _bounded_text(binding.get("provider"), 80),
            "model": _bounded_text(binding.get("model"), 180),
        },
        "summary": _bounded_text(summary, 1400),
        "quality_score": quality_score,
        "error_class": _bounded_text(error_class, 120) if error_class else None,
        "output": output,
    }
    return stable_digest(payload)


def dependency_snapshot_id(dependencies: Mapping[str, Mapping[str, Any]]) -> str:
    """Hash only immutable dependency identities; dictionary order is irrelevant."""
    rows = []
    for task_id in sorted(str(key) for key in dependencies):
        row = dependencies.get(task_id) or {}
        rows.append({
            "task_id": task_id,
            "revision": int(row.get("revision", 0) or 0),
            "result_hash": _bounded_text(row.get("result_hash"), 64),
        })
    return stable_digest({"contract": DEPENDENCY_CONTRACT_VERSION, "dependencies": rows})


def dependency_snapshot_matches(
    handoff: Mapping[str, Any],
    current_results: Mapping[str, Mapping[str, Any]],
    dependency_ids: Sequence[str],
) -> bool:
    """Fail closed when any expected dependency identity differs or is absent."""
    packet = handoff.get("dependencies") if isinstance(handoff.get("dependencies"), Mapping) else {}
    expected_ids = sorted(str(item) for item in dependency_ids)
    if sorted(str(key) for key in packet) != expected_ids:
        return False
    for task_id in expected_ids:
        handoff_row = packet.get(task_id) if isinstance(packet.get(task_id), Mapping) else {}
        current = current_results.get(task_id) if isinstance(current_results.get(task_id), Mapping) else {}
        if current.get("status") != "COMPLETED":
            return False
        if int(handoff_row.get("revision", -1) or -1) != int(current.get("revision", -2) or -2):
            return False
        if _bounded_text(handoff_row.get("result_hash"), 64) != _bounded_text(current.get("result_hash"), 64):
            return False
    packet_id = _bounded_text(handoff.get("dependency_snapshot_id"), 64)
    return bool(packet_id) and packet_id == dependency_snapshot_id(packet) 


def clamp_confidence(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return max(0.0, min(1.0, number))


def build_result_confidence_contract(
    *,
    task_id: str,
    revision: int,
    status: str,
    binding: Mapping[str, Any],
    output: Any,
    summary: str = "",
    quality_score: float | None = None,
    error_class: str | None = None,
    reported_confidence: Any = None,
    validation_status: str = "PASS",
    validation_evidence: Mapping[str, Any] | None = None,
    inbox_cursor: int | None = None,
) -> dict[str, Any]:
    """Build a machine-owned confidence envelope.

    Model-reported confidence is retained as evidence but never trusted as the
    effective value.  Validation FAIL forces zero.  UNAVAILABLE/UNVALIDATED is
    capped conservatively and may be rejected by consumers that require PASS.
    """
    reported = clamp_confidence(reported_confidence)
    quality = clamp_confidence(quality_score)
    validation = _bounded_text(validation_status, 32).upper() or "UNAVAILABLE"
    if validation not in {"PASS", "FAIL", "UNAVAILABLE", "UNVALIDATED"}:
        validation = "UNVALIDATED"
    if status != "COMPLETED" or validation == "FAIL":
        effective = 0.0
    elif validation == "PASS":
        effective = quality if quality is not None else (reported if reported is not None else 0.75)
    else:
        base = quality if quality is not None else (reported if reported is not None else 0.5)
        effective = min(base, 0.5)
    digest = result_identity(
        task_id=task_id,
        revision=revision,
        status=status,
        binding=binding,
        output=output,
        summary=summary,
        quality_score=quality_score,
        error_class=error_class,
    )
    return {
        "contract_version": RCC_VERSION,
        "reported_confidence": reported,
        "effective_confidence": round(float(effective), 6),
        "validation_status": validation,
        "evidence": dict(validation_evidence or {}),
        "revision_index": max(0, int(revision)),
        "result_hash": digest,
        "inbox_cursor": max(0, int(inbox_cursor)) if isinstance(inbox_cursor, int) and not isinstance(inbox_cursor, bool) else None,
    }


def result_is_acceptable(row: Mapping[str, Any], *, require_validation_pass: bool = False) -> bool:
    if row.get("status") != "COMPLETED":
        return False
    rcc = row.get("rcc") if isinstance(row.get("rcc"), Mapping) else {}
    if not rcc:
        return not require_validation_pass
    if rcc.get("contract_version") != RCC_VERSION:
        return False
    if _bounded_text(rcc.get("result_hash"), 64) != _bounded_text(row.get("result_hash"), 64):
        return False
    validation = str(rcc.get("validation_status") or "").upper()
    if validation == "FAIL":
        return False
    if require_validation_pass and validation != "PASS":
        return False
    confidence = clamp_confidence(rcc.get("effective_confidence"))
    return confidence is not None and confidence > 0.0


@dataclass
class ExactModelGateState:
    limit: int = 1
    healthy_streak: int = 0
    pressure_events: int = 0
    promotions: int = 0
    demotions: int = 0
    recovery_hold_windows: int = 0
    degraded: bool = False


@dataclass
class AdaptiveExactModelConcurrency:
    """Per-exact-model concurrency with conservative promotion and fast shrink.

    P0-B behavior: start at one, promote after consecutive healthy completions,
    clamp to provider/config caps, shrink immediately on pressure.
    P1-B behavior: pressure enters a bounded recovery hold so a single healthy
    completion cannot immediately re-promote and oscillate.
    """

    configured_cap: int
    provider_limits: Mapping[str, int]
    promote_after: int = 3
    recovery_promote_after: int = 5
    recovery_hold_windows: int = 2
    states: dict[tuple[str, str], ExactModelGateState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.configured_cap = max(1, min(8, int(self.configured_cap)))
        self.promote_after = max(2, min(20, int(self.promote_after)))
        self.recovery_promote_after = max(self.promote_after, min(40, int(self.recovery_promote_after)))
        self.recovery_hold_windows = max(0, min(20, int(self.recovery_hold_windows)))

    @staticmethod
    def key(provider: Any, model: Any) -> tuple[str, str]:
        return _bounded_text(provider, 80), _bounded_text(model, 180)

    def state(self, provider: Any, model: Any) -> ExactModelGateState:
        return self.states.setdefault(self.key(provider, model), ExactModelGateState())

    def cap(self, provider: Any) -> int:
        return max(1, min(self.configured_cap, int(self.provider_limits.get(str(provider), 1) or 1)))

    def effective_limit(self, provider: Any, model: Any) -> int:
        return max(1, min(self.state(provider, model).limit, self.cap(provider)))

    def on_success(self, provider: Any, model: Any) -> None:
        state = self.state(provider, model)
        if state.recovery_hold_windows > 0:
            state.recovery_hold_windows -= 1
            state.healthy_streak = 0
            return
        state.healthy_streak += 1
        threshold = self.recovery_promote_after if state.degraded else self.promote_after
        cap = self.cap(provider)
        if state.healthy_streak >= threshold and state.limit < cap:
            state.limit += 1
            state.promotions += 1
            state.healthy_streak = 0
            if state.limit >= cap:
                state.degraded = False
        elif state.limit >= cap and state.healthy_streak >= threshold:
            state.healthy_streak = 0
            state.degraded = False

    def on_pressure(self, provider: Any, model: Any, error_class: Any) -> bool:
        error = _bounded_text(error_class, 120).upper()
        if error not in TRANSIENT_PRESSURE_ERRORS:
            return False
        state = self.state(provider, model)
        old = max(1, state.limit)
        state.limit = max(1, old // 2)
        state.healthy_streak = 0
        state.pressure_events += 1
        state.degraded = True
        state.recovery_hold_windows = self.recovery_hold_windows
        if state.limit < old:
            state.demotions += 1
        return True

    def snapshot(self) -> dict[str, Any]:
        rows = []
        for (provider, model), state in sorted(self.states.items()):
            rows.append({
                "provider": provider,
                "model": model,
                "limit": state.limit,
                "effective_limit": self.effective_limit(provider, model),
                "healthy_streak": state.healthy_streak,
                "pressure_events": state.pressure_events,
                "promotions": state.promotions,
                "demotions": state.demotions,
                "degraded": state.degraded,
                "recovery_hold_windows": state.recovery_hold_windows,
            })
        return {
            "configured_cap": self.configured_cap,
            "promote_after": self.promote_after,
            "recovery_promote_after": self.recovery_promote_after,
            "recovery_hold_windows": self.recovery_hold_windows,
            "models": rows,
        }


def aged_priority(
    *,
    base_priority: int,
    waited_seconds: float,
    aging_seconds: float = 60.0,
    max_promotions: int = 2,
    safety_floor: int = 1,
    critical: bool = False,
) -> float:
    """Age waiting work upward without ever outranking critical/safety work."""
    base = max(0, int(base_priority))
    if critical or base == 0:
        return 0.0
    interval = max(1.0, float(aging_seconds))
    promotions = min(max(0, int(max_promotions)), max(0, int(float(waited_seconds) // interval)))
    return float(max(max(1, int(safety_floor)), base - promotions))


__all__ = [
    "AdaptiveExactModelConcurrency",
    "DEPENDENCY_CONTRACT_VERSION",
    "OBJECTIVE_FINGERPRINT_VERSION",
    "RCC_VERSION",
    "TRANSIENT_PRESSURE_ERRORS",
    "aged_priority",
    "build_result_confidence_contract",
    "dependency_snapshot_id",
    "dependency_snapshot_matches",
    "normalize_objective_text",
    "objective_fingerprint",
    "result_identity",
    "result_is_acceptable",
    "stable_digest",
]
