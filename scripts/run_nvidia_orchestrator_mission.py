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

from scripts import provider_adapters as provider_adapters_module
from scripts.execution_scope import ExecutionPolicy
from scripts.provider_adapters import NVIDIA_NEMOTRON_MODEL, create_provider_adapter, nvidia_model_options
from scripts.provider_registry import load_provider_registry


MISSION_ID_PREFIX = "nvidia-autonomous-orchestrator"
MISSION_STATE = "artifacts/mission_state.json"
RESULT_INBOX = "artifacts/result_inbox.json"
MAX_OUTPUT_TOKENS = 256
RESUME_OUTPUT_TOKENS = 2_048
MAX_REPOSITORY_CONTEXT_CHARS = 30_000
ALLOWED_FILES = (
    ".github/workflows/probe-free-models.yml",
    "scripts/run_nvidia_orchestrator_mission.py",
    "scripts/provider_adapters.py",
    "scripts/free_evidence.py",
    "scripts/secure_account_evidence.py",
    "scripts/probe_providers.py",
    "scripts/run_live_staging_from_probe.py",
    "scripts/live_staging_runner.py",
    "scripts/execution_scope.py",
    "tests/test_carrier_workflow.py",
    "tests/test_free_evidence.py",
    "tests/test_probe_providers.py",
    "tests/test_secure_account_evidence.py",
    "tests/test_autonomous_mission.py",
)


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


def _repository_context() -> dict[str, str]:
    """Return compact real repository context so the model cannot invent paths."""
    remaining = MAX_REPOSITORY_CONTEXT_CHARS
    context: dict[str, str] = {}
    for name in ALLOWED_FILES:
        path = Path(name)
        if not path.is_file() or remaining <= 0:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        budget = min(3_000, remaining)
        if len(text) > budget and budget >= 600:
            head = max(300, int(budget * 0.72))
            tail = budget - head
            compact = text[:head] + "\n...<snip>...\n" + text[-tail:]
        else:
            compact = text[:budget]
        context[name] = compact
        remaining -= len(compact)
    return context


def _mission_prompt(*, resume: bool, previous_hash: str) -> str:
    common = (
        "You are NVIDIA Nemotron, the Lead Engineer for the AI Army. "
        "Use only file paths present in repository_context. Never invent files, packages, symbols, "
        "or directories. If required context is missing, set next_action to REQUEST_CONTEXT instead. "
        "Return exactly one compact JSON object with no markdown fences and no prose outside JSON. "
        "Do not edit the repository, access credentials, spend money, deploy, merge, publish, or "
        "weaken paid/production/secret safeguards. patch_bundle must be an object with an operations "
        "array; every operation must name a real allowed path and describe a minimal deterministic edit. "
        "Prefer existing files and smallest changes."
    )
    if resume:
        return (
            common
            + " Continue the same mission without repeating prior analysis. "
            + f"Previous RESULT_HASH={previous_hash}. "
            + "Complete the implementation-ready continuation. Required non-empty keys: "
            + "files_to_change, exact_changes, patch_bundle, tests, resume_logic, result_inbox_logic, "
            + "failure_packet_logic, next_action. The current objective is to finish the durable mission "
            + "controller/result-inbox/controlled-integration design using the repository that actually "
            + "exists, then identify the smallest next step for Google bootstrap."
        )
    return (
        common
        + " Design the smallest implementation for persistent mission state, resume-first behavior, "
        + "a redacted Result Inbox, structured patch bundles, bounded staging-only integration, "
        + "checkpoints, idempotency, heartbeat, failure packets, and continuation of Google bootstrap. "
        + "Return keys: summary, root_cause, proposal, files_affected, tests, risks, next_action."
    )


def _parse_json_response(text: str) -> Mapping[str, Any]:
    bounded = str(text or "")[:20_000].strip()
    candidates = [bounded]
    if bounded.startswith("```"):
        stripped = bounded.replace("```json", "", 1).replace("```", "", 1).strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
        candidates.append(stripped)
    start, end = bounded.find("{"), bounded.rfind("}")
    if start >= 0 and end > start:
        candidates.append(bounded[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, Mapping):
            return dict(parsed)
    return {
        "summary": bounded,
        "proposal": bounded,
        "structured_envelope": "LIMITED_TEXT_PROPOSAL",
    }


def _paths_valid(parsed: Mapping[str, Any], context: Mapping[str, str]) -> bool:
    allowed = set(context)
    files = parsed.get("files_to_change")
    if isinstance(files, list):
        for item in files:
            if not isinstance(item, str) or item not in allowed:
                return False
    bundle = parsed.get("patch_bundle")
    if isinstance(bundle, Mapping):
        operations = bundle.get("operations")
        if isinstance(operations, list):
            for operation in operations:
                if not isinstance(operation, Mapping):
                    return False
                path = operation.get("path")
                if not isinstance(path, str) or path not in allowed:
                    return False
    return True


def main() -> int:
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    resume_mission_id = os.environ.get("RESUME_MISSION_ID", "").strip()
    resume_run_id = os.environ.get("RESUME_RUN_ID", "").strip()
    previous_hash = os.environ.get("RESUME_RESULT_HASH", "").strip()
    resume = bool(resume_mission_id and resume_run_id and previous_hash)
    mission_id = resume_mission_id if resume else f"{MISSION_ID_PREFIX}-{run_id}"
    logical_run_id = resume_run_id if resume else run_id
    try:
        revision = max(1, int(os.environ.get("RESUME_REVISION", "1"))) if resume else 0
    except ValueError:
        revision = 1 if resume else 0
    output_tokens = RESUME_OUTPUT_TOKENS if resume else MAX_OUTPUT_TOKENS
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
        "output_token_budget": output_tokens,
        "production_active": False,
        "paid_execution_count": 0,
        "secret_values_persisted": 0,
    }
    _write(MISSION_STATE, state)

    evidence = _read("artifacts/secure_account_evidence.json")
    probe = _read("artifacts/provider_probe.json")
    probe_items = probe.get("providers") if isinstance(probe.get("providers"), list) else []
    probe_ok = next(
        (
            item
            for item in probe_items
            if isinstance(item, Mapping)
            and item.get("model") == NVIDIA_NEMOTRON_MODEL
            and item.get("status") in {"PROBE_OK", "PROBE_OK_MODEL_FIELD_UNREPORTED"}
        ),
        None,
    )
    record = (
        (((evidence.get("providers") or {}).get("nvidia") or {}).get("models") or {}).get(
            NVIDIA_NEMOTRON_MODEL, {}
        )
    )
    allowed = (
        isinstance(record, Mapping)
        and record.get("limited_staging_probe_allowed") is True
        and isinstance(probe_ok, Mapping)
    )
    if not allowed or not os.environ.get("NVIDIA_API_KEY"):
        state.update(
            {
                "state": "BLOCKED",
                "stop_reason": "NVIDIA_LIMITED_STAGING_EVIDENCE_OR_SECRET_UNAVAILABLE",
                "finished_at": _now(),
            }
        )
        _write(MISSION_STATE, state)
        _write(
            RESULT_INBOX,
            {
                "schema_version": "result-inbox-v1",
                "mission_id": mission_id,
                "run_id": logical_run_id,
                "revision": revision,
                "status": "BLOCKED",
                "created_at": _now(),
                "nvidia_external_calls": 0,
            },
        )
        return 0

    try:
        policy = ExecutionPolicy(
            scope="STAGING",
            provider_id="nvidia",
            model_id=NVIDIA_NEMOTRON_MODEL,
            model_family="NEMOTRON",
            staging_approved=True,
            exact_model_verified=True,
            endpoint_verified=True,
            auth_verified=True,
            circuit_closed=True,
            staging_free_route_allowed=True,
            account_zero_cost_verified=False,
            limited_staging=True,
            limited_operation="BOOTSTRAP_PROPOSAL",
        )
        adapter = create_provider_adapter(
            load_provider_registry(), "nvidia", network_enabled=True, timeout_seconds=60.0
        )
        repository_context = _repository_context()

        previous_bootstrap_cap = provider_adapters_module.LIMITED_BOOTSTRAP_MAX_OUTPUT_TOKENS
        if resume:
            provider_adapters_module.LIMITED_BOOTSTRAP_MAX_OUTPUT_TOKENS = RESUME_OUTPUT_TOKENS
        try:
            response = adapter.generate(
                NVIDIA_NEMOTRON_MODEL,
                [
                    {"role": "system", "content": _mission_prompt(resume=resume, previous_hash=previous_hash)},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "mission_id": mission_id,
                                "run_id": logical_run_id,
                                "revision": revision,
                                "previous_result_hash": previous_hash,
                                "current_head": os.environ.get("GITHUB_SHA", ""),
                                "existing_state": "NVIDIA probe passed; Google is not live; durable mission state/result inbox already exist.",
                                "repository_context": repository_context,
                                "allowed_files": sorted(repository_context),
                                "constraints": {
                                    "repository_write": False,
                                    "paid": False,
                                    "production": False,
                                    "secret_access": False,
                                    "max_files": 5,
                                    "prefer_existing_files": True,
                                },
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    },
                ],
                execution_policy=policy,
                require_zero_cost=True,
                request_id=f"{mission_id}:rev{revision}:{previous_hash[:8] or 'initial'}",
                mission_id=mission_id,
                agent_id="lead-engineer-nvidia",
                max_tokens=output_tokens,
                temperature=0,
                **nvidia_model_options(NVIDIA_NEMOTRON_MODEL),
            )
        finally:
            provider_adapters_module.LIMITED_BOOTSTRAP_MAX_OUTPUT_TOKENS = previous_bootstrap_cap

        text = response.get("text") if isinstance(response, Mapping) else ""
        parsed = _parse_json_response(str(text or ""))
        required = (
            "files_to_change",
            "exact_changes",
            "patch_bundle",
            "tests",
            "resume_logic",
            "result_inbox_logic",
            "failure_packet_logic",
            "next_action",
        )
        path_ok = _paths_valid(parsed, repository_context)
        patch_complete = (
            resume
            and path_ok
            and all(key in parsed and parsed.get(key) not in (None, "", [], {}) for key in required)
        )
        if patch_complete:
            result_status = "COMPLETE"
        elif resume and not path_ok:
            result_status = "INVALID_PATHS"
        else:
            result_status = "PARTIAL_TRUNCATED" if resume else "RESULT_READY"
        next_action = parsed.get("next_action") if isinstance(parsed.get("next_action"), str) else ""
        if result_status != "COMPLETE" and not next_action:
            next_action = "NVIDIA_CONTINUE"

        state.update(
            {
                "state": "RESULT_READY",
                "result_status": result_status,
                "delivery_state": "DELIVERY_PENDING",
                "finished_at": _now(),
                "nvidia_external_calls": 1,
                "result_hash": _digest(parsed),
                "repository_context_files": len(repository_context),
                "path_validation": path_ok,
            }
        )
        _write(MISSION_STATE, state)
        _write(
            RESULT_INBOX,
            {
                "schema_version": "result-inbox-v1",
                "mission_id": mission_id,
                "task_id": "lead-engineer-build",
                "run_id": logical_run_id,
                "carrier_run_id": run_id,
                "revision": revision,
                "previous_result_hash": previous_hash,
                "trace_id": _digest(
                    {"mission_id": mission_id, "run_id": logical_run_id, "revision": revision}
                ),
                "model": NVIDIA_NEMOTRON_MODEL,
                "result_type": "LEAD_ENGINEER_PROPOSAL",
                "result_hash": _digest(parsed),
                "status": result_status,
                "result_status": result_status,
                "proposal": dict(parsed),
                "patch_bundle": parsed.get("patch_bundle", {}),
                "test_plan": parsed.get("tests", []),
                "next_action": next_action or "WORK_REVIEW",
                "path_validation": path_ok,
                "repository_context_files": sorted(repository_context),
                "created_at": _now(),
                "secret_values_persisted": 0,
                "production_active": False,
            },
        )
        print(
            json.dumps(
                {
                    "mission_id": mission_id,
                    "run_id": logical_run_id,
                    "revision": revision,
                    "nvidia_external_calls": 1,
                    "result_status": result_status,
                    "path_validation": path_ok,
                    "result_hash": _digest(parsed),
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        state.update(
            {
                "state": "FAILED",
                "stop_reason": type(exc).__name__,
                "finished_at": _now(),
                "nvidia_external_calls": 0,
            }
        )
        _write(MISSION_STATE, state)
        _write(
            RESULT_INBOX,
            {
                "schema_version": "result-inbox-v1",
                "mission_id": mission_id,
                "run_id": logical_run_id,
                "revision": revision,
                "status": "FAILED",
                "error_class": type(exc).__name__,
                "created_at": _now(),
                "secret_values_persisted": 0,
            },
        )
        print(
            json.dumps(
                {
                    "mission_id": mission_id,
                    "run_id": logical_run_id,
                    "revision": revision,
                    "nvidia_external_calls": 0,
                    "state": "FAILED",
                    "error_class": type(exc).__name__,
                },
                sort_keys=True,
            )
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
