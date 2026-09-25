#!/usr/bin/env python3
"""Low-latency coordination fabric for the replaceable AI Army.

The fabric is deliberately local and provider-agnostic. It removes avoidable
hierarchy hops by publishing compact causal deltas as soon as work changes
state. Consumers can read only new events instead of repeatedly rebuilding a
full blackboard. A short-lived exact-worker failure registry lets a 429/5xx or
transport failure inform later dispatch decisions immediately, so the same
bad route is not hammered again before the organization has reacted.

Failure quarantine remains short-lived, while bounded same-run history records
cumulative failures and repeated quarantines. This separates fast recovery from
observability: a worker may re-enter after cooldown, but repeated degradation is
still visible to schedulers and feedback logic instead of disappearing.

This module performs no network calls, repository writes, secret reads, paid
fallbacks, deploys, or publishes.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import threading
import time
from typing import Any, Callable, Mapping, Sequence


EVENT_PRIORITIES = {
    "CRITICAL": 0,
    "HIGH": 1,
    "NORMAL": 2,
    "BACKGROUND": 3,
}
VALID_EVENT_KINDS = frozenset({
    "TASK_READY",
    "TASK_STARTED",
    "TASK_COMPLETED",
    "TASK_FAILED",
    "TASK_BLOCKED",
    "TASK_DELEGATED",
    "FAILURE_SIGNAL",
    "FAILOVER",
    "HANDOFF",
    "DECISION",
})
TRANSIENT_FAILURE_TTLS = {
    "RATE_LIMIT": 30.0,
    "RATE_LIMITED": 30.0,
    "PROVIDER_5XX": 12.0,
    "NETWORK": 8.0,
    "NETWORK_ERROR": 8.0,
    "TIMEOUT": 8.0,
    "EMPTY_RESPONSE": 5.0,
}
MAX_FAILURE_HISTORY_BINDINGS = 128


@dataclass(frozen=True)
class FabricEvent:
    seq: int
    kind: str
    priority: str
    subject: str
    source: str
    task_id: str
    parent_seq: int | None
    created_monotonic: float
    payload: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "kind": self.kind,
            "priority": self.priority,
            "subject": self.subject,
            "source": self.source,
            "task_id": self.task_id,
            "parent_seq": self.parent_seq,
            "payload": dict(self.payload),
        }


def _json_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _compact_mapping(payload: Mapping[str, Any], max_chars: int) -> dict[str, Any]:
    """Keep small structured payloads and summarize oversized values safely."""
    output: dict[str, Any] = {}
    for key, value in payload.items():
        if len(output) >= 24:
            break
        safe_key = str(key)[:100]
        candidate = dict(output)
        candidate[safe_key] = value
        try:
            if _json_size(candidate) <= max_chars:
                output[safe_key] = value
                continue
        except (TypeError, ValueError):
            value = str(value)
        remaining = max(32, min(700, max_chars - _json_size(output) - len(safe_key) - 16))
        output[safe_key] = str(value)[:remaining]
        if _json_size(output) >= max_chars:
            break
    return output


class LowLatencyAgentFabric:
    """Thread-safe append-only delta bus with bounded memory and callback isolation."""

    def __init__(
        self,
        *,
        max_events: int = 512,
        max_payload_chars: int = 2400,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_events = max(32, min(4096, int(max_events)))
        self.max_payload_chars = max(256, min(16_000, int(max_payload_chars)))
        self.clock = clock
        self._lock = threading.RLock()
        self._events: deque[FabricEvent] = deque(maxlen=self.max_events)
        self._next_seq = 1
        self._dedupe: dict[str, int] = {}
        self._subscribers: list[tuple[Callable[[FabricEvent], None], int]] = []
        self._duplicate_count = 0
        self._subscriber_failures = 0
        self._delivery_samples_ms: list[float] = []

    @property
    def latest_seq(self) -> int:
        with self._lock:
            return self._next_seq - 1

    def subscribe(self, callback: Callable[[FabricEvent], None], *, min_priority: str = "BACKGROUND") -> None:
        priority = str(min_priority or "BACKGROUND").upper()
        if priority not in EVENT_PRIORITIES:
            raise ValueError("unknown event priority")
        with self._lock:
            self._subscribers.append((callback, EVENT_PRIORITIES[priority]))

    def publish(
        self,
        *,
        kind: str,
        subject: str,
        payload: Mapping[str, Any] | None = None,
        source: str = "scheduler",
        task_id: str = "",
        priority: str = "NORMAL",
        parent_seq: int | None = None,
        dedupe_key: str | None = None,
    ) -> FabricEvent:
        event_kind = str(kind or "").upper()
        event_priority = str(priority or "NORMAL").upper()
        if event_kind not in VALID_EVENT_KINDS:
            raise ValueError("unknown event kind")
        if event_priority not in EVENT_PRIORITIES:
            raise ValueError("unknown event priority")
        bounded_payload = _compact_mapping(dict(payload or {}), self.max_payload_chars)
        subscribers: list[tuple[Callable[[FabricEvent], None], int]]
        with self._lock:
            if dedupe_key and dedupe_key in self._dedupe:
                self._duplicate_count += 1
                existing_seq = self._dedupe[dedupe_key]
                for row in reversed(self._events):
                    if row.seq == existing_seq:
                        return row
            event = FabricEvent(
                seq=self._next_seq,
                kind=event_kind,
                priority=event_priority,
                subject=str(subject or "")[:200],
                source=str(source or "")[:120],
                task_id=str(task_id or "")[:128],
                parent_seq=parent_seq if isinstance(parent_seq, int) and parent_seq > 0 else None,
                created_monotonic=self.clock(),
                payload=bounded_payload,
            )
            self._next_seq += 1
            self._events.append(event)
            if dedupe_key:
                self._dedupe[str(dedupe_key)[:240]] = event.seq
                if len(self._dedupe) > self.max_events * 2:
                    floor = max(0, event.seq - self.max_events)
                    self._dedupe = {key: seq for key, seq in self._dedupe.items() if seq >= floor}
            subscribers = list(self._subscribers)

        event_priority_rank = EVENT_PRIORITIES[event.priority]
        for callback, threshold_rank in subscribers:
            if event_priority_rank > threshold_rank:
                continue
            started = self.clock()
            try:
                callback(event)
            except Exception:
                with self._lock:
                    self._subscriber_failures += 1
            finally:
                elapsed = max(0.0, (self.clock() - started) * 1000.0)
                with self._lock:
                    if len(self._delivery_samples_ms) >= 512:
                        self._delivery_samples_ms.pop(0)
                    self._delivery_samples_ms.append(elapsed)
        return event

    def deltas_since(
        self,
        seq: int,
        *,
        task_ids: Sequence[str] | None = None,
        priorities: Sequence[str] | None = None,
        max_events: int = 64,
    ) -> list[dict[str, Any]]:
        wanted_tasks = {str(item) for item in task_ids} if task_ids else None
        wanted_priorities = {str(item).upper() for item in priorities} if priorities else None
        with self._lock:
            rows = [
                event.as_dict()
                for event in self._events
                if event.seq > int(seq)
                and (wanted_tasks is None or event.task_id in wanted_tasks)
                and (wanted_priorities is None or event.priority in wanted_priorities)
            ]
        return rows[: max(1, min(256, int(max_events)))]

    def snapshot(self, *, max_events: int = 64) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)[-max(1, min(256, int(max_events))):]
            samples = list(self._delivery_samples_ms)
            avg_delivery = sum(samples) / len(samples) if samples else 0.0
            max_delivery = max(samples, default=0.0)
            return {
                "schema_version": "low-latency-agent-fabric-v1",
                "latest_seq": self._next_seq - 1,
                "retained_event_count": len(self._events),
                "duplicate_count": self._duplicate_count,
                "subscriber_failures": self._subscriber_failures,
                "avg_callback_delivery_ms": round(avg_delivery, 3),
                "max_callback_delivery_ms": round(max_delivery, 3),
                "events": [event.as_dict() for event in events],
            }


class FailureSignalRegistry:
    """Short-lived exact-worker quarantine plus bounded same-run failure history."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._lock = threading.RLock()
        self._rows: dict[tuple[str, str], dict[str, Any]] = {}
        self._history: dict[tuple[str, str], dict[str, Any]] = {}
        self._avoided_dispatches = 0

    @staticmethod
    def _key(binding: Mapping[str, Any]) -> tuple[str, str]:
        return str(binding.get("provider") or ""), str(binding.get("model") or "")

    def _trim_history(self) -> None:
        if len(self._history) <= MAX_FAILURE_HISTORY_BINDINGS:
            return
        ordered = sorted(
            self._history.items(),
            key=lambda item: float(item[1].get("last_failure_at") or 0.0),
        )
        for key, _ in ordered[: len(self._history) - MAX_FAILURE_HISTORY_BINDINGS]:
            self._history.pop(key, None)

    def record_failure(self, binding: Mapping[str, Any], error_class: str) -> bool:
        error = str(error_class or "").upper()
        ttl = TRANSIENT_FAILURE_TTLS.get(error)
        if ttl is None:
            return False
        key = self._key(binding)
        if not all(key):
            return False
        now = self.clock()
        with self._lock:
            previous = self._rows.get(key, {})
            previous_blocked_until = float(previous.get("blocked_until") or 0.0)
            starts_new_quarantine = not previous or now >= previous_blocked_until
            count = int(previous.get("failure_count") or 0) + 1
            self._rows[key] = {
                "error_class": error,
                "failure_count": count,
                "opened_at": now,
                "blocked_until": now + ttl,
            }
            history = self._history.setdefault(key, {
                "cumulative_failure_count": 0,
                "quarantine_count": 0,
                "last_error_class": "",
                "last_failure_at": 0.0,
                "last_success_at": None,
            })
            history["cumulative_failure_count"] = int(history.get("cumulative_failure_count") or 0) + 1
            if starts_new_quarantine:
                history["quarantine_count"] = int(history.get("quarantine_count") or 0) + 1
            history["last_error_class"] = error
            history["last_failure_at"] = now
            self._trim_history()
        return True

    def record_success(self, binding: Mapping[str, Any]) -> None:
        key = self._key(binding)
        now = self.clock()
        with self._lock:
            self._rows.pop(key, None)
            history = self._history.get(key)
            if history is not None:
                history["last_success_at"] = now

    def is_available(self, binding: Mapping[str, Any], *, count_avoidance: bool = False) -> bool:
        key = self._key(binding)
        with self._lock:
            row = self._rows.get(key)
            if not row:
                return True
            if self.clock() >= float(row.get("blocked_until") or 0.0):
                self._rows.pop(key, None)
                return True
            if count_avoidance:
                self._avoided_dispatches += 1
            return False

    def snapshot(self) -> dict[str, Any]:
        now = self.clock()
        with self._lock:
            rows = []
            for (provider, model), row in sorted(self._rows.items()):
                remaining = max(0.0, float(row.get("blocked_until") or 0.0) - now)
                if remaining <= 0:
                    continue
                history = self._history.get((provider, model), {})
                quarantine_count = int(history.get("quarantine_count") or 0)
                rows.append({
                    "provider": provider,
                    "model": model,
                    "error_class": row.get("error_class"),
                    "failure_count": row.get("failure_count"),
                    "cumulative_failure_count": int(history.get("cumulative_failure_count") or 0),
                    "quarantine_count": quarantine_count,
                    "repeated_quarantine_count": max(0, quarantine_count - 1),
                    "cooldown_remaining_ms": round(remaining * 1000.0, 3),
                })
            history_rows = []
            for (provider, model), history in sorted(self._history.items()):
                quarantine_count = int(history.get("quarantine_count") or 0)
                history_rows.append({
                    "provider": provider,
                    "model": model,
                    "cumulative_failure_count": int(history.get("cumulative_failure_count") or 0),
                    "quarantine_count": quarantine_count,
                    "repeated_quarantine_count": max(0, quarantine_count - 1),
                    "last_error_class": history.get("last_error_class"),
                    "last_failure_at": history.get("last_failure_at"),
                    "last_success_at": history.get("last_success_at"),
                })
            return {
                "active_quarantines": rows,
                "active_quarantine_count": len(rows),
                "avoided_dispatches": self._avoided_dispatches,
                "failure_history": history_rows,
                "historical_binding_count": len(history_rows),
                "cumulative_failure_count": sum(int(row["cumulative_failure_count"]) for row in history_rows),
                "repeated_quarantine_count": sum(int(row["repeated_quarantine_count"]) for row in history_rows),
            }


__all__ = [
    "EVENT_PRIORITIES",
    "FabricEvent",
    "FailureSignalRegistry",
    "LowLatencyAgentFabric",
    "MAX_FAILURE_HISTORY_BINDINGS",
    "TRANSIENT_FAILURE_TTLS",
]
