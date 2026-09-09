#!/usr/bin/env python3
"""Start the bounded live Executor/Reviewer mission from redacted reports.

This command is intentionally a second, explicit stage after the secure
evidence collector and one-shot provider probe.  It only considers the fixed
candidate IDs, requires two successful probe results from different model
families, and creates ephemeral STAGING policies.  It never changes the
production registry or writes repository source.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover - script invocation path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.autonomous_mission import AutonomousBounds
from scripts.execution_scope import ExecutionPolicy
from scripts.live_staging_runner import (
    LiveAgentBinding,
    build_minimal_staging_plan,
    run_live_staging_mission,
)
from scripts.provider_adapters import create_provider_adapter
from scripts.provider_registry import load_provider_registry
from scripts.validate_secure_evidence import validate_report


MODEL_FAMILIES = {
    ("groq", "qwen/qwen3.8-27b"): "QWEN",
    ("nvidia", "deepseek-ai/deepseek-v4-flash-0731"): "DEEPSEEK",
    ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b"): "NEMOTRON",
    ("google", "gemini-3.8-flash"): "GEMINI",
    ("openrouter", "z-ai/glm-5.3-flash:free"): "GLM",
}
EXECUTOR_PREFERENCE = (
    ("groq", "qwen/qwen3.8-27b"),
    ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b"),
    ("google", "gemini-3.8-flash"),
)
REVIEWER_PREFERENCE = (
    ("nvidia", "deepseek-ai/deepseek-v4-flash-0731"),
    ("google", "gemini-3.8-flash"),
    ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b"),
)


def _read_json(path_value: str) -> Mapping[str, Any]:
    path = Path(path_value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("input must stay inside the workspace")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("input must be an object")
    return payload


def _model_record(evidence: Mapping[str, Any], provider: str, model: str) -> Mapping[str, Any]:
    provider_value = evidence.get("providers", {}).get(provider) if isinstance(evidence.get("providers"), Mapping) else None
    models = provider_value.get("models") if isinstance(provider_value, Mapping) else None
    record = models.get(model) if isinstance(models, Mapping) else None
    return record if isinstance(record, Mapping) else {}


def _probe_record(probe: Mapping[str, Any], provider: str, model: str) -> Mapping[str, Any]:
    for item in probe.get("providers", []) if isinstance(probe.get("providers"), list) else []:
        if isinstance(item, Mapping) and item.get("provider") == provider and item.get("model") == model:
            return item
    return {}


def _fresh(record: Mapping[str, Any]) -> bool:
    expiry = record.get("expires_at")
    if not isinstance(expiry, str):
        return False
    try:
        parsed = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed > datetime.now(timezone.utc)
    except ValueError:
        return False


def _candidate_ok(evidence: Mapping[str, Any], probe: Mapping[str, Any], provider: str, model: str) -> bool:
    record = _model_record(evidence, provider, model)
    probe_record = _probe_record(probe, provider, model)
    return (
        record.get("secure_evidence") is True
        and record.get("current") is True
        and _fresh(record)
        and (record.get("zero_cost_verified") is True or record.get("staging_probe_allowed") is True)
        and record.get("model_verified") is True
        and record.get("endpoint_verified") is True
        and record.get("auth_verified") is True
        and record.get("quota_safe") is True
        and record.get("paid_fallback_possible") is False
        and record.get("paid_transition_possible") is False
        and probe_record.get("status") == "PROBE_OK"
        and probe_record.get("model_calls") == 1
    )


def select_live_candidates(evidence: Mapping[str, Any], probe: Mapping[str, Any]) -> tuple[tuple[str, str] | None, tuple[str, str] | None]:
    """Select fixed-role candidates without guessing sibling model IDs."""
    executor = next((candidate for candidate in EXECUTOR_PREFERENCE if _candidate_ok(evidence, probe, *candidate)), None)
    reviewer = next((candidate for candidate in REVIEWER_PREFERENCE if _candidate_ok(evidence, probe, *candidate)), None)
    if executor and reviewer and MODEL_FAMILIES[executor] == MODEL_FAMILIES[reviewer]:
        reviewer = next(
            (candidate for candidate in REVIEWER_PREFERENCE if _candidate_ok(evidence, probe, *candidate) and MODEL_FAMILIES[candidate] != MODEL_FAMILIES[executor]),
            None,
        )
    return executor, reviewer


def _capability_policy(provider: str, model: str, family: str, record: Mapping[str, Any]) -> ExecutionPolicy:
    return ExecutionPolicy(
        scope="STAGING",
        provider_id=provider,
        model_id=model,
        model_family=family,
        technically_ready=True,
        staging_approved=True,
        exact_model_verified=record.get("model_verified") is True,
        endpoint_verified=record.get("endpoint_verified") is True,
        auth_verified=record.get("auth_verified") is True,
        capability_verified=True,
        free_verified=record.get("zero_cost_verified") is True,
        cost_safe=record.get("zero_cost_verified") is True,
        quota_safe=record.get("quota_safe") is True,
        circuit_closed=True,
        paid_fallback=False,
        auto_top_up=False,
        max_retries=0,
        staging_free_route_allowed=record.get("staging_probe_allowed") is True,
        account_zero_cost_verified=record.get("zero_cost_verified") is True,
    )


def run_from_reports(
    evidence: Mapping[str, Any],
    probe: Mapping[str, Any],
    *,
    network_enabled: bool,
    ledger_path: str | Path,
    checkpoint_root: str | Path,
) -> dict[str, Any]:
    if not network_enabled:
        return {"status": "blocked", "stop_reason": "NETWORK_NOT_EXPLICITLY_ENABLED", "live_staging": False, "model_calls": 0}
    executor_choice, reviewer_choice = select_live_candidates(evidence, probe)
    if not executor_choice or not reviewer_choice:
        return {
            "status": "blocked",
            "stop_reason": "TWO_FRESH_ZERO_COST_MODEL_FAMILIES_REQUIRED",
            "live_staging": False,
            "live_provider_count": 0,
            "live_model_family_count": 0,
            "model_calls": 0,
            "paid_execution_count": 0,
            "paid_fallback_count": 0,
            "production_active": False,
        }
    if MODEL_FAMILIES[executor_choice] == MODEL_FAMILIES[reviewer_choice]:
        return {"status": "blocked", "stop_reason": "MODEL_FAMILY_SEPARATION_REQUIRED", "live_staging": False, "model_calls": 0}
    registry = load_provider_registry()
    bindings: list[LiveAgentBinding] = []
    capability_results: dict[str, str] = {}
    for role, choice in (("EXECUTOR", executor_choice), ("REVIEWER", reviewer_choice)):
        provider, model = choice
        record = _model_record(evidence, provider, model)
        adapter = create_provider_adapter(registry, provider, network_enabled=True, timeout_seconds=8.0)
        try:
            capability = adapter.capability_probe(model, "structured_output")
            capability_status = str(capability.get("status") or "CAPABILITY_FAILED") if isinstance(capability, Mapping) else "CAPABILITY_FAILED"
        except Exception:
            capability_status = "CAPABILITY_FAILED"
        capability_results[f"{provider}:{model}"] = capability_status
        if capability_status != "CAPABILITY_OK":
            return {
                "status": "blocked",
                "stop_reason": "CAPABILITY_BENCHMARK_FAILED",
                "capability_results": capability_results,
                "live_staging": False,
                "model_calls": 2,
                "paid_execution_count": 0,
                "paid_fallback_count": 0,
                "production_active": False,
            }
        policy = _capability_policy(provider, model, MODEL_FAMILIES[choice], record)
        bindings.append(LiveAgentBinding(
            role=role,
            provider_id=provider,
            model_id=model,
            model_family=MODEL_FAMILIES[choice],
            adapter=adapter,
            execution_policy=policy,
        ))
    executor, reviewer = bindings
    plan = build_minimal_staging_plan(
        mission_id="phase9-live-executor-reviewer",
        executor=executor,
        request_budget=6,
        token_budget=2_048,
    )
    report = run_live_staging_mission(
        plan,
        executor,
        reviewer,
        ledger_path=ledger_path,
        checkpoint_root=checkpoint_root,
        bounds=AutonomousBounds(max_iterations=3, max_revisions=2, max_replans=1, max_requests=6, max_tokens=2_048),
        network_enabled=True,
    )
    report["capability_results"] = capability_results
    report["executor_model"] = executor.model_id
    report["executor_family"] = executor.model_family
    report["reviewer_model"] = reviewer.model_id
    report["reviewer_family"] = reviewer.model_family
    report["production_active"] = False
    first_calls = int((report.get("live_staging") or {}).get("external_model_calls", 0))
    capability_calls = len(capability_results)
    remaining_calls = max(0, 6 - capability_calls - first_calls)
    bootstrap: dict[str, Any] = {
        "started": False,
        "status": "NOT_STARTED",
        "proposal_review_passed": False,
        "work_integration_required": True,
        "external_model_calls": 0,
    }
    if report.get("status") == "completed" and remaining_calls > 0:
        bootstrap_plan = build_minimal_staging_plan(
            mission_id="phase9-self-bootstrap",
            executor=executor,
            request_budget=remaining_calls,
            token_budget=2_048,
            task_id="SELF-BOOTSTRAP-1",
            task_role="SELF_BOOTSTRAP_PROPOSAL",
            objective=(
                "Analyze the current AI Army implementation and propose one small, low-risk "
                "regression test or adapter-contract improvement. Return a proposal only; "
                "do not edit the repository or weaken any safety guard."
            ),
        )
        bootstrap_ledger = Path(ledger_path).with_name(f"{Path(ledger_path).stem}-self-bootstrap{Path(ledger_path).suffix}")
        bootstrap_report = run_live_staging_mission(
            bootstrap_plan,
            executor,
            reviewer,
            ledger_path=bootstrap_ledger,
            checkpoint_root=Path(checkpoint_root) / "self-bootstrap",
            bounds=AutonomousBounds(
                max_iterations=3,
                max_revisions=2,
                max_replans=1,
                max_requests=remaining_calls,
                max_tokens=2_048,
            ),
            network_enabled=True,
        )
        bootstrap_live = bootstrap_report.get("live_staging") if isinstance(bootstrap_report.get("live_staging"), Mapping) else {}
        bootstrap = {
            "started": True,
            "status": bootstrap_report.get("status", "blocked"),
            "proposal_review_passed": bootstrap_report.get("status") == "completed",
            "work_integration_required": True,
            "external_model_calls": int(bootstrap_live.get("external_model_calls", 0)),
            "revision_count": int((bootstrap_report.get("tasks", {}).get("SELF-BOOTSTRAP-1", {}).get("result", {}).get("autonomous", {}) or {}).get("revision_count", 0)) if isinstance(bootstrap_report.get("tasks"), Mapping) else 0,
            "replan_count": int((bootstrap_report.get("tasks", {}).get("SELF-BOOTSTRAP-1", {}).get("result", {}).get("autonomous", {}) or {}).get("replan_count", 0)) if isinstance(bootstrap_report.get("tasks"), Mapping) else 0,
        }
    elif report.get("status") == "completed":
        bootstrap["status"] = "BOUNDED_CALL_BUDGET_EXHAUSTED"
    report["self_bootstrap"] = bootstrap
    report["total_external_model_calls_in_command"] = capability_calls + first_calls + int(bootstrap.get("external_model_calls", 0))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-file", required=True)
    parser.add_argument("--probe-file", required=True)
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--ledger", default="artifacts/live_staging_ledger.json")
    parser.add_argument("--checkpoint-root", default="artifacts/live_staging_checkpoints")
    parser.add_argument("--output", default="artifacts/live_staging_report.json")
    args = parser.parse_args()
    try:
        evidence = _read_json(args.evidence_file)
        validate_report(evidence)
        probe = _read_json(args.probe_file)
        report = run_from_reports(
            evidence,
            probe,
            network_enabled=args.network,
            ledger_path=args.ledger,
            checkpoint_root=args.checkpoint_root,
        )
    except Exception:
        report = {
            "status": "blocked",
            "stop_reason": "LIVE_STAGING_INPUT_INVALID",
            "live_staging": False,
            "model_calls": 0,
            "paid_execution_count": 0,
            "paid_fallback_count": 0,
            "production_active": False,
        }
    output = Path(args.output)
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output must stay inside the workspace")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "live_staging": report.get("live_staging", False),
        "model_calls": report.get("model_calls", 0),
        "production_active": report.get("production_active", False),
    }, sort_keys=True))
    return 0


__all__ = ["run_from_reports", "select_live_candidates"]


if __name__ == "__main__":
    raise SystemExit(main())
