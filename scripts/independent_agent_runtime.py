#!/usr/bin/env python3
"""Mission-local independent agent sessions for the replaceable AI Army.

Model bindings are treated as replaceable bodies for stable role identities. A
role agent keeps bounded mission-local working memory, receives only new
high-value coordination deltas, can revise and delegate inside its configured
scope, and hands results directly to dependent peers. Ordinary agent decisions
stay local instead of routing every step through the top commander.

Inbox cursors use consume-then-ack semantics: building a context packet never
advances the durable session cursor. The scheduler acknowledges the packet only
after the handler returns a parseable result, so an exception cannot silently
lose CRITICAL/HIGH peer deltas before a retry or failover.

Generation v3 also retains a bounded replay window of already-acknowledged peer
signals. A second task owned by the same stable role therefore still sees recent
failures, failovers and high-priority decisions even after another task advanced
that role's new-event cursor.

The registry is provider-agnostic and performs no network calls, repository
writes, secret reads, payments, deploys, publishes, or generic paid fallback.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import threading
from typing import Any, Mapping, Sequence

from scripts.low_latency_agent_fabric import LowLatencyAgentFabric
from scripts.replaceable_agent_organization import HARD_BOUNDARY_ACTIONS
from scripts.replaceable_agent_scheduler import AgentTask


DEFAULT_MEMORY_ITEMS = 6
DEFAULT_INBOX_EVENTS = 16
DEFAULT_PEER_REPLAY_EVENTS = 12
MAX_BINDING_HISTORY = 8


def stable_agent_id(slot: str) -> str:
    """Return a model-independent identity for one organization role slot."""
    return f"role-agent:{str(slot or '').strip().upper()}"


def _binding_snapshot(binding: Mapping[str, Any]) -> dict[str, str]:
    return {
        "provider": str(binding.get("provider") or "")[:80],
        "model": str(binding.get("model") or "")[:180],
    }


def _bounded_summary(value: Any, limit: int = 700) -> str:
    return str(value or "")[: max(80, min(2000, int(limit)))]


def _compact_peer_event(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = value.get("payload") if isinstance(value.get("payload"), Mapping) else {}
    return {
        "seq": int(value.get("seq") or 0),
        "kind": str(value.get("kind") or "")[:80],
        "priority": str(value.get("priority") or "")[:24],
        "subject": str(value.get("subject") or "")[:160],
        "source": str(value.get("source") or "")[:100],
        "task_id": str(value.get("task_id") or "")[:128],
        "payload": {
            str(key)[:80]: (
                item
                if item is None or isinstance(item, (bool, int, float))
                else str(item)[:360]
            )
            for key, item in list(payload.items())[:10]
        },
    }


@dataclass
class AgentSession:
    agent_id: str
    slot: str
    inbox_cursor: int = 0
    tasks_started: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    revisions_observed: int = 0
    delegated_tasks: int = 0
    handoffs_received: int = 0
    binding_swaps: int = 0
    active_tasks: set[str] = field(default_factory=set)
    binding_history: deque[dict[str, str]] = field(default_factory=lambda: deque(maxlen=MAX_BINDING_HISTORY))
    working_memory: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=DEFAULT_MEMORY_ITEMS))
    peer_context: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=DEFAULT_PEER_REPLAY_EVENTS))

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "slot": self.slot,
            "stable_role_identity": True,
            "tasks_started": self.tasks_started,
            "tasks_completed": self.tasks_completed,
            "tasks_failed": self.tasks_failed,
            "active_task_count": len(self.active_tasks),
            "revisions_observed": self.revisions_observed,
            "delegated_tasks": self.delegated_tasks,
            "handoffs_received": self.handoffs_received,
            "binding_swaps": self.binding_swaps,
            "inbox_cursor": self.inbox_cursor,
            "binding_history": list(self.binding_history),
            "working_memory": list(self.working_memory),
            "recent_peer_context": list(self.peer_context),
        }


class IndependentAgentRegistry:
    """Thread-safe bounded state for stable role agents inside one mission."""

    def __init__(
        self,
        *,
        config: Mapping[str, Any],
        fabric: LowLatencyAgentFabric,
    ) -> None:
        self.config = config
        self.fabric = fabric
        communication = config.get("communication") if isinstance(config.get("communication"), Mapping) else {}
        self.memory_items = max(2, min(12, int(communication.get("agent_session_memory_items") or DEFAULT_MEMORY_ITEMS)))
        self.inbox_events = max(4, min(48, int(communication.get("agent_inbox_max_events") or DEFAULT_INBOX_EVENTS)))
        self.peer_replay_events = max(
            4,
            min(32, int(communication.get("agent_peer_context_replay_events") or DEFAULT_PEER_REPLAY_EVENTS)),
        )
        self._lock = threading.RLock()
        self._sessions: dict[str, AgentSession] = {}
        self._peer_delta_deliveries = 0
        self._peer_context_replays = 0
        self._local_decision_turns = 0

    def _session(self, slot: str) -> AgentSession:
        key = str(slot or "").upper()
        with self._lock:
            session = self._sessions.get(key)
            if session is None:
                session = AgentSession(
                    agent_id=stable_agent_id(key),
                    slot=key,
                    working_memory=deque(maxlen=self.memory_items),
                    peer_context=deque(maxlen=self.peer_replay_events),
                )
                self._sessions[key] = session
            return session

    def start_task(self, task: AgentTask, binding: Mapping[str, Any]) -> str:
        session = self._session(task.slot)
        binding_row = _binding_snapshot(binding)
        with self._lock:
            if task.task_id not in session.active_tasks:
                session.active_tasks.add(task.task_id)
                session.tasks_started += 1
            if not session.binding_history or session.binding_history[-1] != binding_row:
                if session.binding_history:
                    session.binding_swaps += 1
                session.binding_history.append(binding_row)
        return session.agent_id

    def execution_context(
        self,
        *,
        task: AgentTask,
        binding: Mapping[str, Any],
        base_context: Mapping[str, Any],
        handoff: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Build one role-scoped context packet without consuming it yet."""
        session = self._session(task.slot)
        slot_cfg = self.config.get("slots", {}).get(task.slot, {}) if isinstance(self.config.get("slots"), Mapping) else {}
        with self._lock:
            cursor = session.inbox_cursor
            memory = list(session.working_memory)
            recent_peer_context = list(session.peer_context)

        deltas = self.fabric.deltas_since(
            cursor,
            priorities=("CRITICAL", "HIGH"),
            max_events=self.inbox_events,
        )
        latest = max([cursor, *(int(row.get("seq") or 0) for row in deltas)])

        autonomy = dict(base_context)
        max_revisions = int(autonomy.get("max_revisions", 0) or 0)
        may_delegate = slot_cfg.get("may_delegate") is True and int(slot_cfg.get("max_child_tasks", 0) or 0) > 0
        return {
            "agent_identity": {
                "agent_id": session.agent_id,
                "role_slot": task.slot,
                "stable_across_model_swap": True,
                "current_body": _binding_snapshot(binding),
            },
            "local_authority": {
                "may_plan_locally": True,
                "may_revise_locally": max_revisions > 0,
                "max_revisions": max_revisions,
                "may_delegate_locally": may_delegate,
                "max_child_tasks": int(slot_cfg.get("max_child_tasks", 0) or 0),
                "may_handoff_to_dependencies": True,
                "may_request_free_failover": True,
                "commander_roundtrip_required_for_ordinary_local_decision": False,
                "hard_boundary_actions": sorted(HARD_BOUNDARY_ACTIONS),
                "repository_write": False,
                "secret_access": False,
                "generic_paid_fallback": False,
            },
            "mission_role": str(slot_cfg.get("mission") or "")[:1200],
            "working_memory": memory,
            "peer_deltas": deltas,
            "recent_peer_context": recent_peer_context,
            "direct_dependency_handoff": dict(handoff),
            "inbox_cursor": latest,
        }

    def acknowledge_context(
        self,
        *,
        task: AgentTask,
        inbox_cursor: int,
        peer_deltas: Sequence[Mapping[str, Any]] = (),
        peer_delta_count: int | None = None,
        dependency_count: int,
    ) -> None:
        """Commit a context receipt only after the role handler consumed it.

        ``peer_delta_count`` remains as a compatibility input for older callers;
        new callers should provide the actual redacted deltas so the role-local
        replay window can retain them.
        """
        session = self._session(task.slot)
        compact = [_compact_peer_event(row) for row in peer_deltas if isinstance(row, Mapping)]
        delivered_count = len(compact) if compact else max(0, int(peer_delta_count or 0))
        with self._lock:
            previous_latest = int(session.peer_context[-1].get("seq") or 0) if session.peer_context else 0
            for row in compact:
                seq = int(row.get("seq") or 0)
                if seq > previous_latest:
                    session.peer_context.append(row)
                    previous_latest = seq
            session.inbox_cursor = max(session.inbox_cursor, max(0, int(inbox_cursor)))
            self._peer_delta_deliveries += delivered_count
            self._local_decision_turns += 1
            if int(dependency_count) > 0:
                session.handoffs_received += 1

    def note_peer_context_replay(self, *, task: AgentTask) -> None:
        """Record that an agent turn relied on previously acknowledged peer context."""
        session = self._session(task.slot)
        with self._lock:
            if session.peer_context:
                self._peer_context_replays += 1

    def observe_attempt(
        self,
        *,
        task: AgentTask,
        revision_index: int,
        next_task_count: int,
    ) -> None:
        session = self._session(task.slot)
        with self._lock:
            session.revisions_observed = max(session.revisions_observed, max(0, int(revision_index)))
            session.delegated_tasks += max(0, int(next_task_count))

    def finish_task(self, *, task: AgentTask, row: Mapping[str, Any]) -> None:
        session = self._session(task.slot)
        status = str(row.get("status") or "FAILED").upper()
        memory_row = {
            "task_id": task.task_id,
            "status": status,
            "summary": _bounded_summary(row.get("summary")),
            "quality_score": row.get("quality_score"),
            "error_class": row.get("error_class"),
        }
        with self._lock:
            session.active_tasks.discard(task.task_id)
            if status == "COMPLETED":
                session.tasks_completed += 1
            elif status in {"FAILED", "BLOCKED", "CANCELLED"}:
                session.tasks_failed += 1
            session.working_memory.append(memory_row)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            rows = [self._sessions[key].as_dict() for key in sorted(self._sessions)]
            return {
                "schema_version": "independent-agent-session-registry-v3",
                "independent_agent_count": len(rows),
                "stable_role_identity": True,
                "local_decision_turns": self._local_decision_turns,
                "peer_delta_deliveries": self._peer_delta_deliveries,
                "peer_context_replays": self._peer_context_replays,
                "active_task_count": sum(int(row.get("active_task_count") or 0) for row in rows),
                "sessions": rows,
                "repository_write": False,
                "generic_paid_fallback": False,
            }


__all__ = [
    "AgentSession",
    "IndependentAgentRegistry",
    "stable_agent_id",
]
