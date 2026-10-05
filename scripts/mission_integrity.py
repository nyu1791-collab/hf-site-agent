#!/usr/bin/env python3
"""Integrity helpers for Durable Mission resume and Result Inbox delivery.

This module is deliberately provider-agnostic and performs no network I/O.
It binds mission/run/revision/result identity to a source HEAD and validates
proposal hashes before a Result Inbox can be integrated.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping


RESULT_HASH_RE = re.compile(r"^[0-9a-f]{24}$")
HEAD_RE = re.compile(r"^[0-9a-f]{40}$")
RESULT_SCHEMAS = frozenset({"result-inbox-v2", "result-inbox-v3"})
DELIVERY_STATES = frozenset({"DELIVERY_PENDING", "DELIVERED_ACKED"})


class MissionIntegrityError(ValueError):
    """Raised when persisted mission identity cannot be trusted."""


def result_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any, limit: int = 240) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > limit or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return ""
    return value


def _iso(value: Any) -> bool:
    text = _text(value, 80)
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return True


def validate_result_inbox(
    result_inbox: Mapping[str, Any] | None,
    *,
    expected_mission_id: str | None = None,
    expected_run_id: str | None = None,
    expected_revision: int | None = None,
    expected_source_head: str | None = None,
    require_complete: bool = True,
    allow_acked: bool = False,
) -> dict[str, Any]:
    """Validate a Result Inbox before review/integration.

    COMPLETE results are accepted only when their proposal hash can be
    recomputed, source HEAD is explicit, identity matches the expected mission,
    and delivery has not already been acknowledged.
    """
    inbox = _mapping(result_inbox)
    if not inbox:
        return {
            "valid": False,
            "reason": "NO_PROPOSAL",
            "result_hash_verified": False,
            "source_head_verified": False,
        }

    schema = _text(inbox.get("schema_version"), 80)
    if schema not in RESULT_SCHEMAS:
        return {"valid": False, "reason": "RESULT_SCHEMA_INVALID", "result_hash_verified": False, "source_head_verified": False}

    mission_id = _text(inbox.get("mission_id"), 160)
    run_id = _text(inbox.get("run_id"), 160)
    revision = inbox.get("revision")
    source_head = _text(inbox.get("source_head"), 40)
    digest = _text(inbox.get("result_hash"), 24)
    delivery_state = _text(inbox.get("delivery_state"), 40)
    proposal = _mapping(inbox.get("proposal"))

    if not mission_id or not run_id:
        return {"valid": False, "reason": "RESULT_IDENTITY_MISSING", "result_hash_verified": False, "source_head_verified": False}
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return {"valid": False, "reason": "RESULT_REVISION_INVALID", "result_hash_verified": False, "source_head_verified": False}
    if not HEAD_RE.fullmatch(source_head):
        return {"valid": False, "reason": "RESULT_SOURCE_HEAD_MISSING", "result_hash_verified": False, "source_head_verified": False}
    if not RESULT_HASH_RE.fullmatch(digest):
        return {"valid": False, "reason": "RESULT_HASH_INVALID", "result_hash_verified": False, "source_head_verified": True}
    if not proposal:
        return {"valid": False, "reason": "RESULT_PROPOSAL_MISSING", "result_hash_verified": False, "source_head_verified": True}
    if result_hash(proposal) != digest:
        return {"valid": False, "reason": "RESULT_HASH_MISMATCH", "result_hash_verified": False, "source_head_verified": True}
    if not _iso(inbox.get("created_at")):
        return {"valid": False, "reason": "RESULT_CREATED_AT_INVALID", "result_hash_verified": True, "source_head_verified": True}
    if delivery_state not in DELIVERY_STATES:
        return {"valid": False, "reason": "RESULT_DELIVERY_STATE_INVALID", "result_hash_verified": True, "source_head_verified": True}
    if delivery_state == "DELIVERED_ACKED" and not allow_acked:
        return {"valid": False, "reason": "RESULT_ALREADY_ACKED", "result_hash_verified": True, "source_head_verified": True}

    if expected_mission_id is not None and mission_id != expected_mission_id:
        return {"valid": False, "reason": "RESULT_MISSION_ID_MISMATCH", "result_hash_verified": True, "source_head_verified": True}
    if expected_run_id is not None and run_id != expected_run_id:
        return {"valid": False, "reason": "RESULT_RUN_ID_MISMATCH", "result_hash_verified": True, "source_head_verified": True}
    if expected_revision is not None and revision != expected_revision:
        return {"valid": False, "reason": "RESULT_REVISION_MISMATCH", "result_hash_verified": True, "source_head_verified": True}
    if expected_source_head is not None:
        expected = _text(expected_source_head, 40)
        if not HEAD_RE.fullmatch(expected) or source_head != expected:
            return {"valid": False, "reason": "RESULT_SOURCE_HEAD_MISMATCH", "result_hash_verified": True, "source_head_verified": False}

    complete = inbox.get("result_complete") is True and inbox.get("status") == "COMPLETE"
    if require_complete and not complete:
        return {"valid": False, "reason": "LEAD_RESULT_NOT_COMPLETE", "result_hash_verified": True, "source_head_verified": True}

    return {
        "valid": True,
        "reason": "RESULT_INTEGRITY_PASS",
        "mission_id": mission_id,
        "run_id": run_id,
        "revision": revision,
        "source_head": source_head,
        "result_hash": digest,
        "result_hash_verified": True,
        "source_head_verified": True,
        "delivery_state": delivery_state,
        "complete": complete,
    }


def validate_resume_bundle(
    *,
    mission_id: str,
    run_id: str,
    previous_result_hash: str,
    requested_revision: int | None,
    source_head: str,
    mission_state: Mapping[str, Any] | None,
    result_inbox: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Validate one persisted mission generation before a new revision call."""
    identity = (mission_id.strip(), run_id.strip(), previous_result_hash.strip())
    if not any(identity):
        return {"valid": True, "resume": False, "next_revision": 0, "head_advanced": False}
    if not all(identity):
        return {"valid": False, "resume": True, "reason": "PARTIAL_RESUME_IDENTITY"}

    state = _mapping(mission_state)
    inbox = _mapping(result_inbox)
    if not state or not inbox:
        return {"valid": False, "resume": True, "reason": "RESUME_STATE_UNAVAILABLE"}

    if _text(state.get("mission_id"), 160) != identity[0] or _text(inbox.get("mission_id"), 160) != identity[0]:
        return {"valid": False, "resume": True, "reason": "RESUME_MISSION_ID_MISMATCH"}
    if _text(state.get("run_id"), 160) != identity[1] or _text(inbox.get("run_id"), 160) != identity[1]:
        return {"valid": False, "resume": True, "reason": "RESUME_RUN_ID_MISMATCH"}
    if _text(state.get("result_hash"), 24) != identity[2] or _text(inbox.get("result_hash"), 24) != identity[2]:
        return {"valid": False, "resume": True, "reason": "RESUME_RESULT_HASH_MISMATCH"}

    proposal = _mapping(inbox.get("proposal"))
    if not proposal or result_hash(proposal) != identity[2]:
        return {"valid": False, "resume": True, "reason": "RESUME_RESULT_HASH_UNVERIFIED"}

    state_revision = state.get("revision")
    inbox_revision = inbox.get("revision")
    if (
        isinstance(state_revision, bool)
        or not isinstance(state_revision, int)
        or isinstance(inbox_revision, bool)
        or not isinstance(inbox_revision, int)
        or state_revision != inbox_revision
        or state_revision < 0
    ):
        return {"valid": False, "resume": True, "reason": "RESUME_REVISION_MISMATCH"}

    previous_head_state = _text(state.get("source_head"), 40)
    previous_head_inbox = _text(inbox.get("source_head"), 40)
    if not HEAD_RE.fullmatch(previous_head_state) or previous_head_state != previous_head_inbox:
        return {"valid": False, "resume": True, "reason": "RESUME_SOURCE_HEAD_UNVERIFIED"}

    current_head = _text(source_head, 40)
    if not HEAD_RE.fullmatch(current_head):
        return {"valid": False, "resume": True, "reason": "CURRENT_SOURCE_HEAD_INVALID"}

    next_revision = state_revision + 1
    if requested_revision is not None:
        if isinstance(requested_revision, bool) or not isinstance(requested_revision, int) or requested_revision != next_revision:
            return {"valid": False, "resume": True, "reason": "RESUME_REVISION_NOT_NEXT"}

    return {
        "valid": True,
        "resume": True,
        "reason": "RESUME_INTEGRITY_PASS",
        "previous_revision": state_revision,
        "next_revision": next_revision,
        "previous_source_head": previous_head_state,
        "source_head": current_head,
        "head_advanced": previous_head_state != current_head,
    }


def acknowledge_result_inbox(path_value: str, *, expected_result_hash: str) -> dict[str, Any]:
    """Atomically ACK one validated Result Inbox and reject duplicate ACKs."""
    path = Path(path_value)
    if path.is_absolute() or ".." in path.parts:
        raise MissionIntegrityError("RESULT_INBOX_PATH_INVALID")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise MissionIntegrityError("RESULT_INBOX_INVALID")
    report = validate_result_inbox(value, require_complete=True, allow_acked=True)
    if not report.get("valid"):
        raise MissionIntegrityError(str(report.get("reason") or "RESULT_INBOX_INVALID"))
    if report.get("result_hash") != expected_result_hash:
        raise MissionIntegrityError("ACK_RESULT_HASH_MISMATCH")
    if value.get("delivery_state") == "DELIVERED_ACKED":
        raise MissionIntegrityError("RESULT_ALREADY_ACKED")
    updated = dict(value)
    updated["delivery_state"] = "DELIVERED_ACKED"
    updated["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(updated, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)
    return updated


__all__ = [
    "MissionIntegrityError",
    "acknowledge_result_inbox",
    "result_hash",
    "validate_result_inbox",
    "validate_resume_bundle",
]
