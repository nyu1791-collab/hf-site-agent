#!/usr/bin/env python3
"""Run one bounded NVIDIA Lead Engineer mission and persist its result."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.execution_scope import ExecutionPolicy
from scripts.provider_adapters import NVIDIA_NEMOTRON_MODEL, create_provider_adapter, nvidia_model_options
from scripts.provider_registry import load_provider_registry


MISSION_ID_PREFIX = "nvidia-autonomous-orchestrator"
MISSION_STATE = "artifacts/mission_state.json"
RESULT_INBOX = "artifacts/result_inbox.json"
MAX_OUTPUT_TOKENS = 256


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: str, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)


def _read(path: str) -> Mapping[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, Mapping) else {}
    except Exception:
        return {}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _mission_prompt(*, resume: bool, previous_hash: str) -> str:
    if resume:
        return (
            "Continue the same NVIDIA Lead Engineer mission. Do not repeat the previous analysis "
            f"or change the mission identity. Previous RESULT_HASH={previous_hash}. "
            "Return only the implementation-ready Structured Patch Bundle continuation. "
            "Required keys: files_to_change, exact_changes, patch_bundle, tests, resume_logic, "
            "result_inbox_logic, failure_packet_logic, next_action. Keep explanations minimal. "
            "Do not edit the repository, access credentials, spend money, deploy, merge, publish, "
            "or weaken paid/production/secret safeguards."
        )
    return (
        "You are NVIDIA Nemotron, the Lead Engineer for the AI Army. "
        "Analyze only the compact mission below and return a bounded JSON proposal. "
        "Do not edit the repository, access credentials, spend money, deploy, merge, publish, "
        "or weaken paid/production/secret safeguards. Design the smallest implementation for: "
        "persistent mission state with CREATED, DISPATCHED, RUNNING, RESULT_READY, VALIDATING, "
        "INTEGRATING, TESTING, REVISING, COMPLETE, BLOCKED, FAILED; resume-first behavior; "
        "a redacted Result Inbox; structured patch bundles; bounded staging-only auto-integration; "
        "checkpoints, idempotency, heartbeat, failure packets, and continuation of the Google "
        "bootstrap. Return keys: summary, root_cause, proposal, files_affected, tests, risks, next_action."
    )


def main() -> int:
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    resume_mission_id = os.environ.get("RESUME_MISSION_ID", "").strip()
    resume_run_id = os.environ.get("RESUME_RUN_ID", "").strip()
    previous_hash = os.environ.get("RESUME_RESULT_HASH", "").strip()
    resume = bool(resume_mission_id and resume_run_id and previous_hash)
    mission_id = resume_mission_id if resume else f"{MISSION_ID_PREFIX}-{run_id}"
    logical_run_id = resume_run_id if resume else run_id
    revision = 1 if resume else 0
    output_tokens = 1_024 if resume else MAX_OUTPUT_TOKENS
    started = _now()
    state = {
        "schema_version": "mission-state-v1",
        "mission_id": mission_id,
        "task_id": "lead-engineer-build",
        "run_id": logical_run_id,
        "carrier_run_id": run_id,
        "revision": revision,
        "model": NVIDIA_NEMOTRON_MODEL,
        "state": "RUNNING",
        "result_status": "PARTIAL_TRUNCATED" if resume else "RUNNING",
        "delivery_state": "DELIVERY_PENDING",
        "started_at": started,
        "last_heartbeat_at": started,
        "nvidia_external_calls": 0,
        "production_active": False,
        "paid_execution_count": 0,
        "secret_values_persisted": 0,
    }
    _write(MISSION_STATE, state)
    evidence = _read("artifacts/secure_account_evidence.json")
    probe = _read("artifacts/provider_probe.json")
    probe_items = probe.get("providers") if isinstance(probe.get("providers"), list) else []
    probe_ok = next((item for item in probe_items if isinstance(item, Mapping) and item.get("model") == NVIDIA_NEMOTRON_MODEL and item.get("status") in {"PROBE_OK", "PROBE_OK_MODEL_FIELD_UNREPORTED"}), None)
    record = (((evidence.get("providers") or {}).get("nvidia") or {}).get("models") or {}).get(NVIDIA_NEMOTRON_MODEL, {})
    allowed = isinstance(record, Mapping) and record.get("limited_staging_probe_allowed") is True and isinstance(probe_ok, Mapping)
    if not allowed or not os.environ.get("NVIDIA_API_KEY"):
        state.update({"state": "BLOCKED", "stop_reason": "NVIDIA_LIMITED_STAGING_EVIDENCE_OR_SECRET_UNAVAILABLE", "finished_at": _now()})
        _write(MISSION_STATE, state)
        _write(RESULT_INBOX, {"schema_version": "result-inbox-v1", "mission_id": mission_id, "run_id": logical_run_id, "revision": revision, "status": "BLOCKED", "created_at": _now(), "nvidia_external_calls": 0})
        return 0
    try:
        policy = ExecutionPolicy(
            scope="STAGING", provider_id="nvidia", model_id=NVIDIA_NEMOTRON_MODEL,
            model_family="NEMOTRON", staging_approved=True, exact_model_verified=True,
            endpoint_verified=True, auth_verified=True, circuit_closed=True,
            staging_free_route_allowed=True, account_zero_cost_verified=False,
            limited_staging=True, limited_operation="BOOTSTRAP_PROPOSAL",
        )
        adapter = create_provider_adapter(load_provider_registry(), "nvidia", network_enabled=True, timeout_seconds=60.0)
        response = adapter.generate(
            NVIDIA_NEMOTRON_MODEL,
            [{"role": "system", "content": _mission_prompt(resume=resume, previous_hash=previous_hash)}, {"role": "user", "content": json.dumps({
                "mission_id": mission_id, "run_id": logical_run_id, "revision": revision,
                "previous_result_hash": previous_hash, "current_head": os.environ.get("GITHUB_SHA", ""),
                "existing_state": "NVIDIA probe passed; Google is not live; Work is single writer.",
                "constraints": {"repository_write": False, "paid": False, "production": False, "secret_access": False},
            }, sort_keys=True)}],
            execution_policy=policy, require_zero_cost=True, request_id=f"{mission_id}:1",
            mission_id=mission_id, agent_id="lead-engineer-nvidia", max_tokens=output_tokens,
            temperature=0, **nvidia_model_options(NVIDIA_NEMOTRON_MODEL),
        )
        text = response.get("text") if isinstance(response, Mapping) else ""
        bounded_text = str(text or "")[:8_000]
        parsed: Any
        try:
            parsed = json.loads(bounded_text)
        except Exception:
            parsed = {"summary": bounded_text, "proposal": bounded_text, "structured_envelope": "LIMITED_TEXT_PROPOSAL"}
        if not isinstance(parsed, Mapping):
            parsed = {"summary": bounded_text, "proposal": bounded_text, "structured_envelope": "LIMITED_TEXT_PROPOSAL"}
        required = ("files_to_change", "exact_changes", "patch_bundle", "tests", "resume_logic", "result_inbox_logic", "failure_packet_logic", "next_action")
        patch_complete = resume and all(key in parsed and parsed.get(key) not in (None, "", [], {}) for key in required)
        result_status = "COMPLETE" if patch_complete else ("PARTIAL_TRUNCATED" if resume else "RESULT_READY")
        state.update({"state": "RESULT_READY", "result_status": result_status, "delivery_state": "DELIVERY_PENDING", "finished_at": _now(), "nvidia_external_calls": 1, "result_hash": _digest(parsed)})
        _write(MISSION_STATE, state)
        _write(RESULT_INBOX, {
            "schema_version": "result-inbox-v1", "mission_id": mission_id, "task_id": "lead-engineer-build",
            "run_id": logical_run_id, "carrier_run_id": run_id, "revision": revision,
            "previous_result_hash": previous_hash, "trace_id": _digest({"mission_id": mission_id, "run_id": logical_run_id, "revision": revision}),
            "model": NVIDIA_NEMOTRON_MODEL, "result_type": "LEAD_ENGINEER_PROPOSAL", "result_hash": _digest(parsed),
            "status": result_status, "result_status": result_status, "proposal": dict(parsed), "patch_bundle": parsed.get("patch_bundle", {}),
            "test_plan": parsed.get("tests", []), "next_action": parsed.get("next_action", "WORK_REVIEW"),
            "created_at": _now(), "secret_values_persisted": 0, "production_active": False,
        })
        print(json.dumps({"mission_id": mission_id, "run_id": logical_run_id, "revision": revision, "nvidia_external_calls": 1, "result_status": result_status, "result_hash": _digest(parsed)}, sort_keys=True))
        return 0
    except Exception as exc:
        state.update({"state": "FAILED", "stop_reason": type(exc).__name__, "finished_at": _now(), "nvidia_external_calls": 0})
        _write(MISSION_STATE, state)
        _write(RESULT_INBOX, {"schema_version": "result-inbox-v1", "mission_id": mission_id, "run_id": logical_run_id, "revision": revision, "status": "FAILED", "error_class": type(exc).__name__, "created_at": _now(), "secret_values_persisted": 0})
        print(json.dumps({"mission_id": mission_id, "run_id": logical_run_id, "revision": revision, "nvidia_external_calls": 0, "state": "FAILED", "error_class": type(exc).__name__}, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
