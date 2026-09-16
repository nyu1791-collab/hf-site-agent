#!/usr/bin/env python3
"""Ask the live NVIDIA Nemotron agent for one grounded Google bootstrap patch plan."""

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

OUTPUT = "artifacts/google_bootstrap_proposal.json"
STATE = "artifacts/google_bootstrap_state.json"
MAX_OUTPUT_TOKENS = 2_048
MAX_CONTEXT_CHARS = 48_000
CONTEXT_FILES = (
    "config/provider_registry.json",
    ".github/workflows/probe-free-models.yml",
    "scripts/provider_adapters.py",
    "scripts/free_evidence.py",
    "scripts/secure_account_evidence.py",
    "scripts/probe_providers.py",
    "scripts/execution_scope.py",
    "scripts/run_live_staging_from_probe.py",
    "scripts/live_staging_runner.py",
    "tests/test_free_evidence.py",
    "tests/test_probe_providers.py",
    "tests/test_secure_account_evidence.py",
    "tests/test_execution_scope.py",
    "tests/test_carrier_workflow.py",
)
KEYWORDS = (
    "google",
    "gemini",
    "free_tier",
    "paid_transition",
    "paid_fallback",
    "billing",
    "staging_free_route_allowed",
    "limited_staging",
    "probe",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(path: str, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _focused_excerpt(text: str, *, budget: int) -> str:
    if len(text) <= budget:
        return text
    lines = text.splitlines()
    picked: set[int] = set()
    lowered = [line.lower() for line in lines]
    for index, line in enumerate(lowered):
        if any(keyword in line for keyword in KEYWORDS):
            for candidate in range(max(0, index - 8), min(len(lines), index + 9)):
                picked.add(candidate)
    if picked:
        selected = "\n".join(f"{i+1}: {lines[i]}" for i in sorted(picked))
        if len(selected) <= budget:
            return selected
        return selected[:budget]
    head = int(budget * 0.72)
    return text[:head] + "\n...<snip>...\n" + text[-(budget - head):]


def _context() -> dict[str, str]:
    remaining = MAX_CONTEXT_CHARS
    result: dict[str, str] = {}
    for name in CONTEXT_FILES:
        if remaining <= 0:
            break
        path = Path(name)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        per_file = min(8_000 if name.startswith("scripts/") else 5_000, remaining)
        excerpt = _focused_excerpt(text, budget=per_file)
        result[name] = excerpt
        remaining -= len(excerpt)
    return result


def _prompt() -> str:
    return (
        "You are NVIDIA Nemotron acting as the Google Bootstrap Lead Engineer. "
        "The repository context below is authoritative. Do not invent files, APIs, model IDs, account facts, "
        "or capabilities. Google target model is gemini-3.8-flash. Current known state: catalog verified, public "
        "FREE_TIER route verified, Google secret exists, Google probe has not run, current project billing/account "
        "tier and quota are unknown, automatic paid transition is not proven safe. NVIDIA itself is already a live "
        "staging agent. Goal: identify the smallest code change that can safely move Google toward one bounded STAGING "
        "probe without enabling paid execution, paid fallback, auto top-up, production, merge, deploy, or publish. "
        "Do not claim account_zero_cost_verified=true unless the supplied code/evidence proves it. Distinguish missing "
        "evidence from an actually reachable paid path. If Google cannot yet be safely probed, identify the exact code "
        "condition and the smallest technically enforceable mitigation instead of hand-waving. "
        "Return exactly one JSON object, no markdown. Required keys: root_cause, current_guards, decision, "
        "files_to_change, operations, tests, google_probe_plan, stop_conditions, risks, next_action. "
        "files_to_change must use only repository_context paths. operations must be a list of objects with path, symbol, "
        "before_condition, after_condition, and rationale. Prefer 1-4 files and minimal edits. next_action must be one of "
        "APPLY_MINIMAL_PATCH, PROBE_GOOGLE_ONCE, or BLOCKED_NEEDS_EVIDENCE."
    )


def _parse(text: str) -> Mapping[str, Any]:
    bounded = str(text or "")[:24_000].strip()
    candidates = [bounded]
    start, end = bounded.find("{"), bounded.rfind("}")
    if start >= 0 and end > start:
        candidates.append(bounded[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except Exception:
            continue
        if isinstance(value, Mapping):
            return dict(value)
    return {"raw": bounded, "next_action": "BLOCKED_NEEDS_EVIDENCE"}


def _valid_paths(value: Mapping[str, Any], context: Mapping[str, str]) -> bool:
    allowed = set(context)
    files = value.get("files_to_change")
    if not isinstance(files, list):
        return False
    if len(files) > 4:
        return False
    if any(not isinstance(path, str) or path not in allowed for path in files):
        return False
    operations = value.get("operations")
    if not isinstance(operations, list):
        return False
    for operation in operations:
        if not isinstance(operation, Mapping):
            return False
        if operation.get("path") not in allowed:
            return False
    return True


def main() -> int:
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    mission_id = f"google-bootstrap-by-nvidia-{run_id}"
    context = _context()
    state = {
        "schema_version": "google-bootstrap-state-v1",
        "mission_id": mission_id,
        "run_id": run_id,
        "state": "RUNNING",
        "model": NVIDIA_NEMOTRON_MODEL,
        "nvidia_external_calls": 0,
        "paid_execution_count": 0,
        "production_active": False,
        "started_at": _now(),
    }
    _write(STATE, state)

    evidence = json.loads(Path("artifacts/secure_account_evidence.json").read_text(encoding="utf-8"))
    probe = json.loads(Path("artifacts/provider_probe.json").read_text(encoding="utf-8"))
    model_record = ((((evidence.get("providers") or {}).get("nvidia") or {}).get("models") or {}).get(NVIDIA_NEMOTRON_MODEL, {}))
    probe_items = probe.get("providers") if isinstance(probe, Mapping) else []
    live_probe = next((item for item in probe_items or [] if isinstance(item, Mapping) and item.get("model") == NVIDIA_NEMOTRON_MODEL and item.get("status") in {"PROBE_OK", "PROBE_OK_MODEL_FIELD_UNREPORTED"}), None)
    allowed = isinstance(model_record, Mapping) and model_record.get("limited_staging_probe_allowed") is True and isinstance(live_probe, Mapping)
    if not allowed or not os.environ.get("NVIDIA_API_KEY"):
        state.update({"state": "BLOCKED", "stop_reason": "NVIDIA_LIVE_STAGING_EVIDENCE_UNAVAILABLE", "finished_at": _now()})
        _write(STATE, state)
        _write(OUTPUT, {"mission_id": mission_id, "status": "BLOCKED", "nvidia_external_calls": 0})
        return 0

    policy = ExecutionPolicy(
        scope="STAGING", provider_id="nvidia", model_id=NVIDIA_NEMOTRON_MODEL, model_family="NEMOTRON",
        staging_approved=True, exact_model_verified=True, endpoint_verified=True, auth_verified=True,
        circuit_closed=True, staging_free_route_allowed=True, account_zero_cost_verified=False,
        limited_staging=True, limited_operation="BOOTSTRAP_PROPOSAL",
    )
    adapter = create_provider_adapter(load_provider_registry(), "nvidia", network_enabled=True, timeout_seconds=60.0)
    previous_cap = provider_adapters_module.LIMITED_BOOTSTRAP_MAX_OUTPUT_TOKENS
    provider_adapters_module.LIMITED_BOOTSTRAP_MAX_OUTPUT_TOKENS = MAX_OUTPUT_TOKENS
    try:
        response = adapter.generate(
            NVIDIA_NEMOTRON_MODEL,
            [
                {"role": "system", "content": _prompt()},
                {"role": "user", "content": json.dumps({"repository_context": context, "allowed_files": sorted(context), "constraints": {"repository_write": False, "paid": False, "production": False, "secret_access": False}}, ensure_ascii=False, sort_keys=True)},
            ],
            execution_policy=policy,
            require_zero_cost=True,
            request_id=f"{mission_id}:analysis",
            mission_id=mission_id,
            agent_id="google-bootstrap-lead-nvidia",
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=0,
            response_format={"type": "json_object"},
            **nvidia_model_options(NVIDIA_NEMOTRON_MODEL),
        )
    finally:
        provider_adapters_module.LIMITED_BOOTSTRAP_MAX_OUTPUT_TOKENS = previous_cap

    parsed = _parse(response.get("text") if isinstance(response, Mapping) else "")
    required = ("root_cause", "current_guards", "decision", "files_to_change", "operations", "tests", "google_probe_plan", "stop_conditions", "risks", "next_action")
    complete = all(key in parsed and parsed.get(key) not in (None, "", [], {}) for key in required)
    paths_ok = _valid_paths(parsed, context)
    next_action = parsed.get("next_action")
    allowed_actions = {"APPLY_MINIMAL_PATCH", "PROBE_GOOGLE_ONCE", "BLOCKED_NEEDS_EVIDENCE"}
    action_ok = isinstance(next_action, str) and next_action in allowed_actions
    status = "COMPLETE" if complete and paths_ok and action_ok else "INVALID_OR_INCOMPLETE"
    result = {
        "schema_version": "google-bootstrap-proposal-v1",
        "mission_id": mission_id,
        "run_id": run_id,
        "model": NVIDIA_NEMOTRON_MODEL,
        "status": status,
        "path_validation": paths_ok,
        "proposal": dict(parsed),
        "result_hash": _digest(parsed),
        "nvidia_external_calls": 1,
        "created_at": _now(),
        "secret_values_persisted": 0,
        "paid_execution_count": 0,
        "production_active": False,
    }
    _write(OUTPUT, result)
    state.update({"state": "RESULT_READY", "status": status, "nvidia_external_calls": 1, "result_hash": result["result_hash"], "finished_at": _now()})
    _write(STATE, state)
    print(json.dumps({"mission_id": mission_id, "status": status, "path_validation": paths_ok, "nvidia_external_calls": 1, "result_hash": result["result_hash"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
