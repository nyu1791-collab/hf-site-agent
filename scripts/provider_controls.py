#!/usr/bin/env python3
"""Provider-scoped quota ledgers and circuit breakers.

This module never assumes that one provider's quota applies to another.  A
request is reserved and recorded before a caller sends it.  Unknown quota is a
stop condition, not permission for unlimited use.  No retry is performed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from typing import Any, Mapping


class QuotaGuardError(RuntimeError):
    """Provider quota or circuit policy blocks a request."""

    def __init__(self, reason: str, *, provider_id: str):
        super().__init__(reason)
        self.reason = reason
        self.provider_id = provider_id


class CircuitState:
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


def utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def quota_zone(used: int, limit: int | None) -> str:
    if limit is None or limit <= 0:
        return "UNKNOWN"
    ratio = used / limit
    if ratio >= 0.95:
        return "RED"
    if ratio >= 0.90:
        return "ORANGE"
    if ratio >= 0.80:
        return "YELLOW"
    return "GREEN"


@dataclass(frozen=True)
class CircuitSnapshot:
    provider_id: str
    state: str
    reason: str
    opened_at: str | None


class ProviderCircuitBreaker:
    def __init__(self, provider_id: str):
        self.provider_id = provider_id
        self._state = CircuitState.CLOSED
        self._reason = ""
        self._opened_at: str | None = None
        self._lock = threading.RLock()

    def snapshot(self) -> CircuitSnapshot:
        with self._lock:
            return CircuitSnapshot(self.provider_id, self._state, self._reason, self._opened_at)

    def open(self, reason: str) -> CircuitSnapshot:
        with self._lock:
            self._state = CircuitState.OPEN
            self._reason = str(reason)[:120]
            self._opened_at = utc_iso()
            return self.snapshot()

    def half_open(self) -> CircuitSnapshot:
        with self._lock:
            if self._state == CircuitState.OPEN:
                self._state = CircuitState.HALF_OPEN
            return self.snapshot()

    def close(self) -> CircuitSnapshot:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._reason = ""
            self._opened_at = None
            return self.snapshot()

    def assert_request_allowed(self) -> None:
        with self._lock:
            if self._state == CircuitState.OPEN:
                raise QuotaGuardError("PROVIDER_CIRCUIT_OPEN", provider_id=self.provider_id)
            if self._state == CircuitState.HALF_OPEN:
                raise QuotaGuardError("PROVIDER_CIRCUIT_HALF_OPEN_REQUIRES_PROBE", provider_id=self.provider_id)


class ProviderQuotaLedger:
    """A persistent ledger for one provider namespace."""

    def __init__(
        self,
        provider_id: str,
        path: str | Path,
        *,
        daily_limit: int | None,
        hard_stop: int | None = None,
        rpm_limit: int | None = None,
        emergency_reserve: int = 0,
    ):
        if not provider_id or "/" in provider_id:
            raise ValueError("invalid provider_id")
        if daily_limit is not None and daily_limit <= 0:
            raise ValueError("daily_limit must be positive when known")
        if hard_stop is not None and (hard_stop <= 0 or daily_limit is None or hard_stop > daily_limit):
            raise ValueError("hard_stop must not exceed a known daily_limit")
        if rpm_limit is not None and rpm_limit <= 0:
            raise ValueError("rpm_limit must be positive when known")
        self.provider_id = provider_id
        self.path = Path(path)
        self.daily_limit = daily_limit
        self.hard_stop = hard_stop if hard_stop is not None else daily_limit
        self.rpm_limit = rpm_limit
        self.emergency_reserve = max(0, emergency_reserve)
        self.circuit = ProviderCircuitBreaker(provider_id)
        self._lock = threading.RLock()
        self._entries: list[dict[str, Any]] = []
        self._reservations: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            raise QuotaGuardError("QUOTA_LEDGER_INVALID", provider_id=self.provider_id)
        if not isinstance(payload, Mapping):
            raise QuotaGuardError("QUOTA_LEDGER_INVALID", provider_id=self.provider_id)
        provider_state = payload.get("providers", {}).get(self.provider_id)
        if not isinstance(provider_state, Mapping):
            return
        self._entries = [dict(item) for item in provider_state.get("entries", []) if isinstance(item, Mapping)]
        self._reservations = {str(key): dict(value) for key, value in (provider_state.get("reservations", {}) or {}).items() if isinstance(value, Mapping)}
        circuit = provider_state.get("circuit")
        if isinstance(circuit, Mapping):
            state = circuit.get("state")
            if state == CircuitState.OPEN:
                self.circuit.open(str(circuit.get("reason") or "restored_open"))
            elif state == CircuitState.HALF_OPEN:
                self.circuit.half_open()

    def _write_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {"version": 1, "providers": {}}
        if self.path.exists():
            try:
                existing = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(existing, Mapping) and isinstance(existing.get("providers"), Mapping):
                    payload["providers"] = {str(key): dict(value) for key, value in existing["providers"].items() if isinstance(value, Mapping)}
            except (OSError, ValueError, json.JSONDecodeError):
                raise QuotaGuardError("QUOTA_LEDGER_INVALID", provider_id=self.provider_id)
        snapshot = self.circuit.snapshot()
        payload["providers"][self.provider_id] = {
            "entries": self._entries,
            "reservations": self._reservations,
            "circuit": {
                "state": snapshot.state,
                "reason": snapshot.reason,
                "opened_at": snapshot.opened_at,
            },
            "updated_at": utc_iso(),
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def _today_entries_locked(self) -> list[dict[str, Any]]:
        today = utc_iso()[:10]
        return [entry for entry in self._entries if str(entry.get("requested_at", ""))[:10] == today and entry.get("quota_counted") is True]

    def _recent_entries_locked(self, window_seconds: int = 60) -> list[dict[str, Any]]:
        cutoff = time.time() - window_seconds
        result = []
        for entry in self._today_entries_locked():
            try:
                stamp = datetime.fromisoformat(str(entry["requested_at"]).replace("Z", "+00:00")).timestamp()
            except (KeyError, TypeError, ValueError):
                continue
            if stamp >= cutoff:
                result.append(entry)
        return result

    def summary(self) -> dict[str, Any]:
        with self._lock:
            used = len(self._today_entries_locked())
            reserved = sum(max(0, int(item.get("estimated_requests", 0))) for item in self._reservations.values() if item.get("released") is not True)
            zone = quota_zone(used + reserved, self.daily_limit)
            return {
                "provider_id": self.provider_id,
                "used": used,
                "reserved": reserved,
                "daily_limit": self.daily_limit,
                "hard_stop": self.hard_stop,
                "rpm_limit": self.rpm_limit,
                "zone": zone,
                "circuit_state": self.circuit.snapshot().state,
                "circuit_reason": self.circuit.snapshot().reason,
                "paid_fallback": False,
                "allow_new_requests": self._allow_new_locked(1),
            }

    def _allow_new_locked(self, estimated_requests: int) -> bool:
        if self.daily_limit is None or self.rpm_limit is None:
            return False
        used = len(self._today_entries_locked())
        reserved = sum(max(0, int(item.get("estimated_requests", 0))) for item in self._reservations.values() if item.get("released") is not True)
        if self.hard_stop is None or used + reserved + estimated_requests > self.hard_stop:
            return False
        if len(self._recent_entries_locked()) + estimated_requests > self.rpm_limit:
            return False
        return quota_zone(used + reserved + estimated_requests, self.daily_limit) != "RED"

    def reserve(self, *, request_id: str, mission_id: str, agent_id: str, model: str, estimated_requests: int = 1) -> dict[str, Any]:
        if not request_id.strip() or not mission_id.strip() or not agent_id.strip() or not model.strip():
            raise ValueError("request identity is required")
        estimated_requests = int(estimated_requests)
        if estimated_requests < 1:
            raise ValueError("estimated_requests must be positive")
        with self._lock:
            self.circuit.assert_request_allowed()
            existing = self._reservations.get(request_id)
            if existing is not None:
                if existing.get("released") is True:
                    raise QuotaGuardError("DUPLICATE_REQUEST", provider_id=self.provider_id)
                raise QuotaGuardError("REQUEST_IN_PROGRESS", provider_id=self.provider_id)
            if not self._allow_new_locked(estimated_requests):
                reason = "QUOTA_UNKNOWN" if self.daily_limit is None or self.rpm_limit is None else "QUOTA_GUARD_BLOCKED"
                raise QuotaGuardError(reason, provider_id=self.provider_id)
            now = utc_iso()
            reservation = {
                "request_id": request_id,
                "provider_id": self.provider_id,
                "mission_id": mission_id,
                "agent_id": agent_id,
                "model": model,
                "requested_at": now,
                "estimated_requests": estimated_requests,
                "released": False,
            }
            self._reservations[request_id] = reservation
            # Count before send.  A failed or timed-out request remains in the
            # ledger, satisfying the free-resource safety contract.
            self._entries.append({
                "provider_id": self.provider_id,
                "date_utc": now[:10],
                "request_id": request_id,
                "mission_id": mission_id,
                "agent_id": agent_id,
                "model": model,
                "requested_at": now,
                "success": None,
                "http_status": None,
                "retry": 0,
                "quota_counted": True,
            })
            self._write_locked()
            return dict(reservation)

    def record_result(self, request_id: str, *, success: bool, http_status: int | None, retry: int = 0, error_class: str | None = None, retry_after_seconds: int | None = None) -> dict[str, Any]:
        with self._lock:
            entry = next((item for item in reversed(self._entries) if item.get("request_id") == request_id), None)
            if entry is None:
                raise QuotaGuardError("UNKNOWN_REQUEST_RESERVATION", provider_id=self.provider_id)
            entry.update({
                "success": bool(success),
                "http_status": http_status,
                "retry": max(0, int(retry)),
                "error_class": error_class,
                "retry_after_seconds": retry_after_seconds,
            })
            reservation = self._reservations.get(request_id)
            if reservation is not None:
                reservation["released"] = True
            if http_status == 429 or error_class in {"RATE_LIMITED", "CREDIT_EXHAUSTED", "AUTH_ERROR", "PERMISSION_ERROR"}:
                self.circuit.open(error_class or "PROVIDER_BLOCKED")
            self._write_locked()
            return dict(entry)

    def recover_after_probe(self, success: bool) -> CircuitSnapshot:
        if success:
            return self.circuit.close()
        return self.circuit.open("PROBE_FAILED")


def ledger_from_registry(provider_id: str, registry: Mapping[str, Any], path: str | Path) -> ProviderQuotaLedger:
    """Create one provider-scoped ledger from registry limits.

    Unknown provider limits intentionally become a blocked ledger.  OpenRouter
    retains its separate 1000 daily cap, 900 hard stop, 100-request reserve,
    and 15 RPM policy; those numbers are not copied to other providers.
    """
    providers = registry.get("providers") if isinstance(registry, Mapping) else None
    config = providers.get(provider_id) if isinstance(providers, Mapping) else None
    if not isinstance(config, Mapping):
        raise QuotaGuardError("UNKNOWN_PROVIDER", provider_id=provider_id)
    daily_cap = config.get("daily_cap")
    hard_stop = config.get("hard_stop")
    rpm_limit = config.get("rpm_limit")
    return ProviderQuotaLedger(
        provider_id,
        path,
        daily_limit=int(daily_cap) if isinstance(daily_cap, int) else None,
        hard_stop=int(hard_stop) if isinstance(hard_stop, int) else None,
        rpm_limit=int(rpm_limit) if isinstance(rpm_limit, int) else None,
        emergency_reserve=int(config.get("emergency_reserve") or 0),
    )


__all__ = [
    "CircuitSnapshot", "CircuitState", "ProviderCircuitBreaker", "ProviderQuotaLedger",
    "QuotaGuardError", "ledger_from_registry", "quota_zone",
]
