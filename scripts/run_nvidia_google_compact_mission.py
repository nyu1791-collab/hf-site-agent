#!/usr/bin/env python3
"""Run one compact repository-grounded NVIDIA replan for Google staging.

This is a recovery path for the durable NVIDIA Lead Engineer mission when a
large-context answer is malformed or incomplete. It keeps the same logical
mission identity, sends one bounded Nemotron request through the focused
streaming adapter, validates a strict patch-bundle contract, and persists only
redacted Mission State / Result Inbox artifacts.

It never edits repository source, enables Google, bypasses the Google zero-cost
gate, uses paid fallback, deploys, merges, publishes, or exposes credentials.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import provider_adapters as provider_adapters_module
from scripts.execution_scope import ExecutionPolicy
from scripts.focused_nvidia_streaming_adapter import FocusedNvidiaStreamingAdapter
from scripts.provider_adapters import NVIDIA_NEMOTRON_MODEL, nvidia_model_options
from scripts.provider_registry import load_provider_registry


MISSION_STATE = "artifacts/mission_state.json"
RESULT_INBOX = "artifacts/result_inbox.json"
OUTPUT_TOKEN_BUDGET = 1_536
MAX_TOTAL_CONTEXT_CHARS = 14_000
MAX_FILE_CONTEXT_CHARS = 2_800
MAX_OPERATION_INSTRUCTIONS = 4_000

GOOGLE_CONTEXT_FILES: tuple[str, ...] = (
    "scripts/provider_adapters.py",
    "scripts/secure_account_evidence.py",
    "scripts/free_evidence.py",
    "scripts/probe_providers.py",
    "scripts/execution_scope.py",
    "tests/test_secure_account_evidence.py",
    "tests/test_free_evidence.py",
    "tests/test_probe_providers.py",
)

CONTEXT_MARKERS: Mapping[str, Sequence[str]] = {
    "scripts/provider_adapters.py": (
        "class GeminiNativeAdapter",
        "def probe(self, model_id",
        "def capability_probe(self, model_id",
    ),
    "scripts/secure_account_evidence.py": (
        "def _account_defaults",
        "def _pricing_metadata",
        "def _record_for_model",
    ),
    "scripts/free_evidence.py": (
        'provider_id == "google"',
        "zero_cost_verified",
        "staging_probe_allowed",
    ),
    "scripts/probe_providers.py": (
        'provider_id == "google"',
        "ZERO_COST_PREFLIGHT_BLOCKED",
        "staging_probe_allowed",
    ),
    "scripts/execution_scope.py": (
        "class ExecutionPolicy",
        "def authorize_execution",
    ),
}

REQUIRED_RESULT_KEYS = (
    "files_to_change",
    "exact_changes",
    "patch_bundle",
    "tests",
    "safety_invariants",
    "next_action",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _write(path: str, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(target)


def _read(path: str) -> Mapping[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, Mapping) else {}
    except Exception:
        return {}


def _context_window(text: str, markers: Sequence[str], budget: int) -> str:
    if budget <= 0:
        return ""
    found = [marker for marker in markers if marker and marker in text]
    if not found:
        return text[:budget]
    chunks: list[str] = []
    per = max(500, budget // len(found))
    for marker in found:
        index = text.find(marker)
        start = max(0, index - max(120, per // 4))
        end = min(len(text), start + per)
        chunks.append(f"# around {marker}\n{text[start:end]}")
    return "\n...<window>...\n".join(chunks)[:budget]


def repository_context() -> dict[str, str]:
    remaining = MAX_TOTAL_CONTEXT_CHARS
    result: dict[str, str] = {}
    for name in GOOGLE_CONTEXT_FILES:
        if remaining <= 0:
            break
        path = Path(name)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        budget = min(MAX_FILE_CONTEXT_CHARS, remaining)
        snippet = _context_window(text, CONTEXT_MARKERS.get(name, ()), budget)
        if snippet:
            result[name] = snippet
            remaining -= len(snippet)
    return result


def _parse_json(text: Any) -> Mapping[str, Any] | None:
    raw = str(text or "").strip()[:20_000]
    if not raw:
        return None
    candidates = [raw]
    if raw.startswith("```"):
        stripped = raw.replace("```json", "", 1).replace("```", "", 1).strip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
        candidates.append(stripped)
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except Exception:
            continue
        if isinstance(value, Mapping):
            return dict(value)
    return None


def validate_result(value: Mapping[str, Any] | None, allowed_paths: set[str]) -> tuple[bool, str]:
    if not isinstance(value, Mapping):
        return False, "INVALID_JSON"
    if any(key not in value or value.get(key) in (None, "", [], {}) for key in REQUIRED_RESULT_KEYS):
        return False, "INCOMPLETE_SCHEMA"

    files = value.get("files_to_change")
    if not isinstance(files, list) or not 1 <= len(files) <= 4:
        return False, "INVALID_FILES_TO_CHANGE"
    if any(not isinstance(path, str) or path not in allowed_paths for path in files):
        return False, "INVALID_PATHS"
    if len(set(files)) != len(files):
        return False, "DUPLICATE_PATHS"

    exact_changes = value.get("exact_changes")
    if not isinstance(exact_changes, list) or not exact_changes:
        return False, "INVALID_EXACT_CHANGES"
    for change in exact_changes:
        if not isinstance(change, Mapping):
            return False, "INVALID_EXACT_CHANGES"
        if change.get("path") not in allowed_paths:
            return False, "INVALID_PATHS"
        description = change.get("change")
        if not isinstance(description, str) or not description.strip():
            return False, "INVALID_EXACT_CHANGES"

    bundle = value.get("patch_bundle")
    if not isinstance(bundle, Mapping):
        return False, "INVALID_PATCH_BUNDLE"
    operations = bundle.get("operations")
    if not isinstance(operations, list) or not operations or len(operations) > 4:
        return False, "INVALID_PATCH_BUNDLE"

    op_paths: list[str] = []
    for operation in operations:
        if not isinstance(operation, Mapping):
            return False, "INVALID_PATCH_OPERATION"
        if set(operation) - {"path", "action", "anchor", "instructions"}:
            return False, "UNEXPECTED_PATCH_FIELDS"
        path = operation.get("path")
        if not isinstance(path, str) or path not in allowed_paths:
            return False, "INVALID_PATHS"
        if operation.get("action") != "modify":
            return False, "INVALID_PATCH_ACTION"
        anchor = operation.get("anchor")
        instructions = operation.get("instructions")
        if not isinstance(anchor, str) or not anchor.strip():
            return False, "INVALID_PATCH_ANCHOR"
        if not isinstance(instructions, str) or not instructions.strip():
            return False, "INVALID_PATCH_INSTRUCTIONS"
        if len(instructions) > MAX_OPERATION_INSTRUCTIONS:
            return False, "PATCH_INSTRUCTIONS_TOO_LARGE"
        op_paths.append(path)

    if set(op_paths) != set(files):
        return False, "PATCH_FILE_SET_MISMATCH"

    tests = value.get("tests")
    if not isinstance(tests, list) or not tests or any(not isinstance(item, str) or not item.strip() for item in tests):
        return False, "INVALID_TEST_PLAN"
    invariants = value.get("safety_invariants")
    if not isinstance(invariants, list) or not invariants:
        return False, "INVALID_SAFETY_INVARIANTS"
    if str(value.get("next_action") or "") not in {"WORK_INTEGRATE", "REQUEST_CONTEXT"}:
        return False, "INVALID_NEXT_ACTION"
    return True, "COMPLETE"


def _system_prompt(previous_hash: str) -> str:
    return (
        "You are NVIDIA Nemotron, Lead Engineer. This is a REPLAN after malformed output, not a continuation of prose. "
        f"The prior result hash is {previous_hash}. Return one JSON object only. No markdown. No commentary. "
        "Use only repository_context and allowed_paths. Never invent files, symbols, APIs, account facts, provider capabilities, or tools. "
        "Objective: propose the smallest implementation-ready change that improves trustworthy Google free-only staging evidence or diagnostics "
        "WITHOUT bypassing ZERO_COST_PREFLIGHT_BLOCKED, WITHOUT treating public FREE_TIER pricing as proof of the current account, and WITHOUT "
        "enabling paid fallback, production routing, secret exposure, deploy, merge, or publish. If the supplied code is insufficient, return "
        "next_action REQUEST_CONTEXT rather than guessing. Required schema: "
        '{"files_to_change":["real/path.py"],"exact_changes":[{"path":"real/path.py","change":"precise edit"}],'
        '"patch_bundle":{"operations":[{"path":"real/path.py","action":"modify","anchor":"existing symbol/text","instructions":"deterministic edit instructions"}]},'
        '"tests":["specific test"],"safety_invariants":["invariant"],"next_action":"WORK_INTEGRATE"}. '
        "Maximum four files. Every patch operation must use action=modify."
    )


def main() -> int:
    carrier_run_id = os.environ.get("GITHUB_RUN_ID", "local")
    mission_id = os.environ.get("RESUME_MISSION_ID", "").strip()
    logical_run_id = os.environ.get("RESUME_RUN_ID", "").strip()
    previous_hash = os.environ.get("RESUME_RESULT_HASH", "").strip()
    try:
        revision = max(1, int(os.environ.get("RESUME_REVISION", "3")))
    except ValueError:
        revision = 3

    if not mission_id or not logical_run_id or not previous_hash:
        mission_id = mission_id or f"nvidia-google-compact-{carrier_run_id}"
        logical_run_id = logical_run_id or carrier_run_id

    started = _now()
    state: dict[str, Any] = {
        "schema_version": "mission-state-v3",
        "mission_id": mission_id,
        "run_id": logical_run_id,
        "carrier_run_id": carrier_run_id,
        "task_id": "google-free-only-replan",
        "revision": revision,
        "replan_count": 1,
        "replan_reason": "INVALID_MODEL_OUTPUT",
        "model": NVIDIA_NEMOTRON_MODEL,
        "state": "RUNNING",
        "result_status": "RUNNING",
        "delivery_state": "DELIVERY_PENDING",
        "output_token_budget": OUTPUT_TOKEN_BUDGET,
        "nvidia_external_calls": 0,
        "started_at": started,
        "last_heartbeat_at": started,
        "paid_execution_count": 0,
        "paid_fallback_count": 0,
        "production_active": False,
        "secret_values_persisted": 0,
    }
    _write(MISSION_STATE, state)

    evidence = _read("artifacts/secure_account_evidence.json")
    probe = _read("artifacts/provider_probe.json")
    record = (((evidence.get("providers") or {}).get("nvidia") or {}).get("models") or {}).get(NVIDIA_NEMOTRON_MODEL, {})
    probe_items = probe.get("providers") if isinstance(probe.get("providers"), list) else []
    probe_ok = next(
        (
            item
            for item in probe_items
            if isinstance(item, Mapping)
            and item.get("provider") == "nvidia"
            and item.get("model") == NVIDIA_NEMOTRON_MODEL
            and item.get("status") in {"PROBE_OK", "PROBE_OK_MODEL_FIELD_UNREPORTED"}
        ),
        None,
    )
    gate_ok = (
        isinstance(record, Mapping)
        and record.get("limited_staging_probe_allowed") is True
        and isinstance(probe_ok, Mapping)
        and bool(os.environ.get("NVIDIA_API_KEY"))
    )
    if not gate_ok:
        state.update({"state": "BLOCKED", "result_status": "BLOCKED", "finished_at": _now(), "stop_reason": "NVIDIA_GATE_NOT_READY"})
        _write(MISSION_STATE, state)
        _write(RESULT_INBOX, {"schema_version": "result-inbox-v3", "mission_id": mission_id, "run_id": logical_run_id, "revision": revision, "status": "BLOCKED", "created_at": _now(), "nvidia_external_calls": 0})
        return 0

    context = repository_context()
    allowed_paths = set(context)
    if len(context) < 4:
        state.update({"state": "BLOCKED", "result_status": "BLOCKED", "finished_at": _now(), "stop_reason": "REPOSITORY_CONTEXT_INSUFFICIENT"})
        _write(MISSION_STATE, state)
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
        adapter = FocusedNvidiaStreamingAdapter(load_provider_registry(), network_enabled=True)
        previous_cap = provider_adapters_module.LIMITED_BOOTSTRAP_MAX_OUTPUT_TOKENS
        provider_adapters_module.LIMITED_BOOTSTRAP_MAX_OUTPUT_TOKENS = OUTPUT_TOKEN_BUDGET
        try:
            response = adapter.generate(
                NVIDIA_NEMOTRON_MODEL,
                [
                    {"role": "system", "content": _system_prompt(previous_hash)},
                    {"role": "user", "content": json.dumps({
                        "mission_id": mission_id,
                        "run_id": logical_run_id,
                        "revision": revision,
                        "current_head": os.environ.get("GITHUB_SHA", ""),
                        "observed_runtime": {
                            "nvidia_probe": "PROBE_OK",
                            "google_probe": "ZERO_COST_PREFLIGHT_BLOCKED",
                            "google_secret_present": True,
                            "google_account_tier": "UNKNOWN",
                            "google_quota_safe": False,
                        },
                        "allowed_paths": sorted(allowed_paths),
                        "repository_context": context,
                    }, ensure_ascii=False, sort_keys=True)},
                ],
                execution_policy=policy,
                require_zero_cost=True,
                request_id=f"{mission_id}:replan:{revision}:{previous_hash[:8]}",
                mission_id=mission_id,
                agent_id="lead-engineer-nvidia-compact",
                max_tokens=OUTPUT_TOKEN_BUDGET,
                temperature=0,
                response_format={"type": "json_object"},
                **nvidia_model_options(NVIDIA_NEMOTRON_MODEL),
            )
        finally:
            provider_adapters_module.LIMITED_BOOTSTRAP_MAX_OUTPUT_TOKENS = previous_cap

        parsed = _parse_json(response.get("text") if isinstance(response, Mapping) else "")
        complete, result_status = validate_result(parsed, allowed_paths)
        result_hash = _digest(parsed if parsed is not None else {"status": result_status})
        next_action = str((parsed or {}).get("next_action") or ("WORK_INTEGRATE" if complete else "NVIDIA_REPLAN"))
        if not complete:
            next_action = "NVIDIA_REPLAN"

        state.update({
            "state": "RESULT_READY" if complete else "RESULT_REJECTED",
            "result_status": result_status,
            "result_complete": complete,
            "result_hash": result_hash,
            "path_validation": result_status != "INVALID_PATHS",
            "repository_context_files": len(context),
            "nvidia_external_calls": 1,
            "finished_at": _now(),
        })
        _write(MISSION_STATE, state)
        _write(RESULT_INBOX, {
            "schema_version": "result-inbox-v3",
            "mission_id": mission_id,
            "run_id": logical_run_id,
            "carrier_run_id": carrier_run_id,
            "task_id": "google-free-only-replan",
            "revision": revision,
            "replan_count": 1,
            "replan_reason": "INVALID_MODEL_OUTPUT",
            "previous_result_hash": previous_hash,
            "result_hash": result_hash,
            "model": NVIDIA_NEMOTRON_MODEL,
            "status": result_status,
            "result_status": result_status,
            "result_complete": complete,
            "proposal": dict(parsed) if isinstance(parsed, Mapping) else {},
            "patch_bundle": (parsed or {}).get("patch_bundle", {}),
            "test_plan": (parsed or {}).get("tests", []),
            "safety_invariants": (parsed or {}).get("safety_invariants", []),
            "next_action": next_action,
            "allowed_paths": sorted(allowed_paths),
            "repository_context_files": sorted(context),
            "nvidia_external_calls": 1,
            "paid_execution_count": 0,
            "paid_fallback_count": 0,
            "production_active": False,
            "secret_values_persisted": 0,
            "created_at": _now(),
        })
        print(json.dumps({
            "mission_id": mission_id,
            "run_id": logical_run_id,
            "revision": revision,
            "replan_count": 1,
            "nvidia_external_calls": 1,
            "result_status": result_status,
            "result_complete": complete,
            "result_hash": result_hash,
            "next_action": next_action,
        }, sort_keys=True))
        return 0
    except Exception as exc:
        state.update({
            "state": "FAILED",
            "result_status": "FAILED",
            "stop_reason": type(exc).__name__,
            "finished_at": _now(),
            "nvidia_external_calls": 0,
        })
        _write(MISSION_STATE, state)
        _write(RESULT_INBOX, {
            "schema_version": "result-inbox-v3",
            "mission_id": mission_id,
            "run_id": logical_run_id,
            "revision": revision,
            "status": "FAILED",
            "error_class": type(exc).__name__,
            "nvidia_external_calls": 0,
            "paid_execution_count": 0,
            "production_active": False,
            "secret_values_persisted": 0,
            "created_at": _now(),
        })
        print(json.dumps({
            "mission_id": mission_id,
            "run_id": logical_run_id,
            "revision": revision,
            "nvidia_external_calls": 0,
            "state": "FAILED",
            "error_class": type(exc).__name__,
        }, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
