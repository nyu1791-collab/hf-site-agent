#!/usr/bin/env python3
"""Shared free-model quota ledger, reservation, limiter, and circuit breaker.

This module is deliberately provider-agnostic and performs no network calls.
It records an attempted request before the caller sends it, so failed
requests cannot silently escape the local budget.  The store is a small JSON
file suitable for an Actions artifact or a mounted persistent directory.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

try:  # Linux/GitHub Actions; keep a safe no-op fallback for other platforms.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

FREE_MODEL_RE = re.compile(r"^[A-Za-z0-9._/-]+:free$")
GENERIC_FREE_ROUTER = "openrouter/free"
DAILY_CAP = 1000
HARD_STOP = 900
MAX_FREE_RPM = 15
WINDOW_SECONDS = 60
ZONES = (
    (0, 800, "GREEN"),
    (800, 850, "YELLOW"),
    (850, 900, "ORANGE"),
    (900, DAILY_CAP + 1, "RED"),
)
_STATE_VERSION = 1
_LOCK = threading.RLock()


class FreeQuotaBlocked(RuntimeError):
    """Raised before a free request is sent when policy blocks it."""

    def __init__(self, reason: str, *, status: str = "PAUSED_FREE_QUOTA", details: dict[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.details = details or {}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_day(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(timezone.utc).date().isoformat()


def classify_zone(count: int) -> str:
    for lower, upper, name in ZONES:
        if lower <= count < upper:
            return name
    return "RED" if count >= HARD_STOP else "GREEN"


def is_explicit_free_model(model: str) -> bool:
    return bool(FREE_MODEL_RE.fullmatch(str(model or "").strip())) and model != GENERIC_FREE_ROUTER


class FreeUsageLedger:
    """Atomic local ledger shared by all commanders/workers in one runtime."""

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        now: Callable[[], datetime] = utc_now,
        daily_cap: int = DAILY_CAP,
        hard_stop: int = HARD_STOP,
        max_rpm: int = MAX_FREE_RPM,
    ):
        self.path = Path(path or os.environ.get("FREE_USAGE_LEDGER_PATH", "artifacts/free_usage_ledger.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.now = now
        self.daily_cap = int(daily_cap)
        self.hard_stop = int(hard_stop)
        self.max_rpm = int(max_rpm)
        if self.hard_stop >= self.daily_cap:
            raise ValueError("hard_stop must leave an emergency reserve")
        if self.max_rpm <= 0:
            raise ValueError("max_rpm must be positive")

    @contextmanager
    def _locked_state(self, *, write: bool = False) -> Iterator[dict[str, Any]]:
        lock_path = self.path.with_name(self.path.name + ".lock")
        lock_path.touch(exist_ok=True)
        with _LOCK, lock_path.open("r+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            state = self._read_unlocked()
            yield state
            if write:
                self._write_unlocked(state)
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _empty(self) -> dict[str, Any]:
        return {
            "version": _STATE_VERSION,
            "circuit_breaker": "CLOSED",
            "breaker_reason": "",
            "probe_date_utc": None,
            "entries": [],
            "reservations": {},
        }

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return self._empty()
        if not isinstance(payload, dict) or payload.get("version") != _STATE_VERSION:
            return self._empty()
        payload.setdefault("entries", [])
        payload.setdefault("reservations", {})
        payload.setdefault("circuit_breaker", "CLOSED")
        payload.setdefault("breaker_reason", "")
        payload.setdefault("probe_date_utc", None)
        return payload

    def _write_unlocked(self, state: dict[str, Any]) -> None:
        data = json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(prefix=self.path.name + ".", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass

    def _today_entries(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        day = utc_day(self.now())
        return [
            item for item in state.get("entries", [])
            if isinstance(item, dict) and item.get("date_utc") == day and item.get("quota_counted") is True
        ]

    def _active_reservations(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        day = utc_day(self.now())
        result = []
        for item in state.get("reservations", {}).values():
            if not isinstance(item, dict) or item.get("date_utc") != day or item.get("released") is True:
                continue
            estimated = max(0, int(item.get("estimated_requests", 0)))
            used = min(estimated, max(0, int(item.get("used", 0))))
            item["remaining"] = max(0, estimated - used)
            result.append(item)
        return result

    def usage_count(self) -> int:
        with self._locked_state() as state:
            return len(self._today_entries(state))

    def zone(self) -> str:
        return classify_zone(self.usage_count())

    def summary(self) -> dict[str, Any]:
        with self._locked_state() as state:
            count = len(self._today_entries(state))
            remaining_reserved = sum(int(item.get("remaining", 0)) for item in self._active_reservations(state))
            return {
                "date_utc": utc_day(self.now()),
                "free_requests_today": count,
                "daily_cap": self.daily_cap,
                "hard_stop": self.hard_stop,
                "reserved_emergency": self.daily_cap - self.hard_stop,
                "reserved_mission_requests": remaining_reserved,
                "zone": classify_zone(count),
                "circuit_breaker": state.get("circuit_breaker", "CLOSED"),
                "breaker_reason": state.get("breaker_reason", ""),
                "status": "PAUSED_FREE_QUOTA" if state.get("circuit_breaker") == "OPEN" else "READY",
                "paid_fallback": False,
            }

    def reserve(self, mission_id: str, estimated_requests: int) -> dict[str, Any]:
        mission_id = str(mission_id or "").strip()
        estimated_requests = int(estimated_requests)
        if not mission_id or estimated_requests < 0:
            raise ValueError("mission_id and non-negative estimated_requests are required")
        with self._locked_state(write=True) as state:
            reservations = state.setdefault("reservations", {})
            existing = reservations.get(mission_id)
            if isinstance(existing, dict) and existing.get("released") is not True:
                if int(existing.get("estimated_requests", 0)) != estimated_requests:
                    raise FreeQuotaBlocked("mission_reservation_conflict", status="BLOCKED")
                return dict(existing)
            count = len(self._today_entries(state))
            reserved = sum(int(item.get("remaining", 0)) for item in self._active_reservations(state))
            if state.get("circuit_breaker") == "OPEN" or count + reserved + estimated_requests > self.hard_stop:
                raise FreeQuotaBlocked(
                    "free_daily_safety_limit",
                    details={"used": count, "requested": estimated_requests, "reserved": reserved, "hard_stop": self.hard_stop},
                )
            reservation = {
                "mission_id": mission_id,
                "date_utc": utc_day(self.now()),
                "estimated_requests": estimated_requests,
                "used": 0,
                "remaining": estimated_requests,
                "reserved_at": utc_iso(self.now()),
                "released": False,
            }
            reservations[mission_id] = reservation
            return dict(reservation)

    def release(self, mission_id: str) -> None:
        with self._locked_state(write=True) as state:
            item = state.setdefault("reservations", {}).get(str(mission_id))
            if isinstance(item, dict):
                item["released"] = True
                item["released_at"] = utc_iso(self.now())

    def before_request(
        self,
        *,
        request_id: str,
        mission_id: str,
        agent_id: str,
        model: str,
        retry: int = 0,
    ) -> dict[str, Any]:
        if not is_explicit_free_model(model):
            raise FreeQuotaBlocked("non_free_model_blocked", status="BLOCKED")
        request_id = str(request_id or "").strip()
        if not request_id:
            raise ValueError("request_id is required")
        with self._locked_state(write=True) as state:
            for item in state.get("entries", []):
                if isinstance(item, dict) and item.get("request_id") == request_id:
                    return {"allowed": False, "reason": "duplicate_request", "entry": dict(item)}
            if state.get("circuit_breaker") == "OPEN":
                raise FreeQuotaBlocked(state.get("breaker_reason") or "free_quota_circuit_open")
            now = self.now()
            entries = self._today_entries(state)
            count = len(entries)
            if count >= self.hard_stop:
                state["circuit_breaker"] = "OPEN"
                state["breaker_reason"] = "free_daily_safety_limit"
                raise FreeQuotaBlocked("free_daily_safety_limit", details={"used": count, "hard_stop": self.hard_stop})
            window_start = now - timedelta(seconds=WINDOW_SECONDS)
            recent = 0
            for item in entries:
                try:
                    requested_at = datetime.fromisoformat(str(item.get("requested_at", "")).replace("Z", "+00:00"))
                except ValueError:
                    continue
                if requested_at >= window_start:
                    recent += 1
            if recent >= self.max_rpm:
                raise FreeQuotaBlocked("free_rpm_limit", status="QUEUED_FREE_QUOTA", details={"recent": recent, "max_rpm": self.max_rpm})
            remaining_reservation = sum(int(item.get("remaining", 0)) for item in self._active_reservations(state))
            mission_reservation = state.setdefault("reservations", {}).get(str(mission_id))
            if isinstance(mission_reservation, dict) and mission_reservation.get("released") is not True:
                if int(mission_reservation.get("remaining", 0)) <= 0:
                    raise FreeQuotaBlocked("mission_reservation_exhausted", status="BLOCKED")
            elif count + 1 + remaining_reservation > self.hard_stop:
                raise FreeQuotaBlocked("unreserved_free_budget", status="BLOCKED")
            entry = {
                "date_utc": utc_day(now),
                "request_id": request_id,
                "mission_id": str(mission_id),
                "agent_id": str(agent_id),
                "model": str(model),
                "requested_at": utc_iso(now),
                "success": None,
                "http_status": None,
                "retry": int(retry),
                "quota_counted": True,
            }
            state.setdefault("entries", []).append(entry)
            if isinstance(mission_reservation, dict) and mission_reservation.get("released") is not True:
                mission_reservation["used"] = int(mission_reservation.get("used", 0)) + 1
                mission_reservation["remaining"] = max(0, int(mission_reservation.get("estimated_requests", 0)) - mission_reservation["used"])
            if count + 1 >= self.hard_stop:
                state["circuit_breaker"] = "OPEN"
                state["breaker_reason"] = "free_daily_safety_limit"
            return {"allowed": True, "entry": dict(entry), "summary": self.summary()}

    def record_response(self, request_id: str, *, success: bool, http_status: int | None, retry: int = 0) -> dict[str, Any]:
        with self._locked_state(write=True) as state:
            for item in state.get("entries", []):
                if isinstance(item, dict) and item.get("request_id") == request_id:
                    item["success"] = bool(success)
                    item["http_status"] = http_status
                    item["retry"] = int(retry)
                    if http_status == 429:
                        state["circuit_breaker"] = "OPEN"
                        state["breaker_reason"] = "free_endpoint_429"
                    return dict(item)
        return {"request_id": request_id, "updated": False}

    def probe_allowed(self) -> bool:
        with self._locked_state() as state:
            return state.get("probe_date_utc") != utc_day(self.now())

    def record_probe(self, *, success: bool, http_status: int | None) -> None:
        with self._locked_state(write=True) as state:
            day = utc_day(self.now())
            state["probe_date_utc"] = day
            if success:
                state["circuit_breaker"] = "CLOSED"
                state["breaker_reason"] = ""
            elif http_status == 429:
                state["circuit_breaker"] = "OPEN"
                state["breaker_reason"] = "free_endpoint_429"
            else:
                state["circuit_breaker"] = "OPEN"
                state["breaker_reason"] = "free_quota_probe_failed"

    def mark_429(self, request_id: str) -> dict[str, Any]:
        return self.record_response(request_id, success=False, http_status=429, retry=0)


__all__ = [
    "DAILY_CAP",
    "HARD_STOP",
    "MAX_FREE_RPM",
    "FreeQuotaBlocked",
    "FreeUsageLedger",
    "classify_zone",
    "is_explicit_free_model",
]
