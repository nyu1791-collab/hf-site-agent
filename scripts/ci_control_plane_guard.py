#!/usr/bin/env python3
"""Static CI control-plane guard.

Prevents long-lived branches from fanning one push out into an unbounded set of
hosted jobs and prevents paid DeepSeek execution from escaping the canonical,
mission-scoped Executive Supervisor contract.

This script is deterministic and makes no network or model calls.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config" / "ci_execution_policy.json"
SUPERVISOR_POLICY = ROOT / "config" / "deepseek_paid_supervisor_policy.json"
WORKFLOWS = ROOT / ".github" / "workflows"


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"invalid object: {path}")
    return value


def _text(name: str) -> str:
    path = WORKFLOWS / name
    if not path.is_file():
        raise SystemExit(f"workflow missing: {name}")
    return path.read_text(encoding="utf-8")


def _has_automatic_trigger(text: str, trigger: str) -> bool:
    needle = f"  {trigger}:"
    return needle in text or f"\n{trigger}:" in text


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> int:
    policy = _load(POLICY)
    supervisor = _load(SUPERVISOR_POLICY)
    _require(policy.get("schema_version") == "ci-execution-policy-v2", "invalid ci execution policy schema")
    _require(supervisor.get("status") == "ACTIVE_SCOPED_EXCEPTION", "DeepSeek supervisor policy inactive")

    automatic = list(policy.get("automatic_workflows") or ())
    limit = int(policy.get("max_automatic_workflows_per_push") or 0)
    _require(1 <= len(automatic) <= limit <= 2, "automatic workflow fan-out limit violated")
    _require("verify-hierarchical-runtime.yml" in automatic, "core hierarchical gate missing")
    _require("canonical-ai-army-consistency.yml" in automatic, "canonical consistency gate missing")

    for name in automatic:
        text = _text(name)
        if _has_automatic_trigger(text, "pull_request"):
            raise SystemExit(f"automatic workflow may not use pull_request on long-lived PR: {name}")
        if not _has_automatic_trigger(text, "push"):
            raise SystemExit(f"automatic workflow needs push trigger: {name}")
        if "cancel-in-progress: true" not in text:
            raise SystemExit(f"automatic workflow must cancel superseded runs: {name}")
    consistency = _text("canonical-ai-army-consistency.yml")
    for required in (
        "config/ai_army_org_chart.json",
        "config/multi_agent_operating_policy.json",
        "config/deepseek_paid_supervisor_policy.json",
        "scripts/ai_army_routing_facade.py",
        "scripts/validate_permanent_ai_army_state.py",
    ):
        _require(required in consistency, f"consistency gate lost path scope/contract: {required}")

    manual = list(policy.get("manual_specialist_workflows") or ())
    for name in manual:
        text = _text(name)
        if "workflow_dispatch:" not in text:
            raise SystemExit(f"manual specialist missing workflow_dispatch: {name}")
        for forbidden in ("pull_request", "push", "schedule"):
            if _has_automatic_trigger(text, forbidden):
                raise SystemExit(f"manual specialist has automatic trigger {forbidden}: {name}")
        if "cancel-in-progress: true" not in text:
            raise SystemExit(f"manual specialist must cancel duplicate dispatches: {name}")

    # One explicit bounded internal carrier may remain automatic in addition to
    # the core gates. It is path-scoped and has read-only repository authority.
    carriers = list(policy.get("bounded_internal_carrier_workflows") or ())
    carrier_limit = int(policy.get("max_bounded_internal_carriers") or 0)
    _require(0 <= len(carriers) <= carrier_limit <= 1, "bounded internal carrier fan-out limit violated")
    for name in carriers:
        text = _text(name)
        if not _has_automatic_trigger(text, "push"):
            raise SystemExit(f"bounded carrier needs explicit push trigger: {name}")
        if ".github/subordinate-continuation-trigger.txt" not in text:
            raise SystemExit(f"bounded carrier is not scoped to the dedicated trigger: {name}")
        if "contents: write" in text or "persist-credentials: true" in text:
            raise SystemExit(f"bounded carrier may not receive repository write authority: {name}")
        for required in (
            "allow_deepseek_paid_trial=true",
            "max_paid_deepseek_usd_per_execution=0.10",
            "no_repo_write_by_external_ai=true",
            "max_estimated_cost_usd",
            "generic_paid_fallback",
            "auto_top_up",
            "repository_write",
            "deploy",
            "publish",
        ):
            if required not in text:
                raise SystemExit(f"bounded carrier guard missing {required}: {name}")

    # Canonical paid DeepSeek is no longer the old two-call engineering trial.
    # Exactly one mission-scoped supervisor workflow may expose DEEPSEEK_API_KEY.
    paid_supervisors = list(policy.get("bounded_paid_supervisor_workflows") or ())
    paid_limit = int(policy.get("max_bounded_paid_supervisor_workflows") or 0)
    _require(len(paid_supervisors) == 1, "exactly one paid supervisor workflow is required")
    _require(len(paid_supervisors) <= paid_limit <= 1, "paid supervisor workflow fan-out limit violated")
    _require(paid_supervisors[0] == "deepseek-supervisor-research.yml", "unexpected paid supervisor workflow")

    paid_workflow = _text(paid_supervisors[0])
    _require(_has_automatic_trigger(paid_workflow, "push"), "DeepSeek supervisor workflow needs mission-scoped push trigger")
    _require("workflow_dispatch:" in paid_workflow, "DeepSeek supervisor workflow needs explicit manual mission dispatch")
    _require("missions/deepseek-supervisor/*.json" in paid_workflow, "DeepSeek supervisor workflow is not mission-path scoped")
    _require("Expected exactly one changed supervisor mission" in paid_workflow, "DeepSeek supervisor workflow must require exactly one changed mission")
    _require("contents: read" in paid_workflow, "DeepSeek supervisor workflow must remain read-only")
    _require("contents: write" not in paid_workflow, "DeepSeek supervisor workflow gained repository write authority")
    _require("persist-credentials: false" in paid_workflow, "DeepSeek supervisor checkout credentials must not persist")
    _require("cancel-in-progress: false" in paid_workflow, "paid supervisor mission must not be silently cancelled by a later mission")
    for required in (
        "DEEPSEEK_API_KEY",
        "config/deepseek_paid_supervisor_policy.json",
        "scripts/deepseek_supervisor_research.py",
        "chatgpt_final_adjudication_required",
        "max_estimated_cost_usd",
        "known_estimated_daily_spend_before_mission_usd",
        "generic_paid_fallback",
        "auto_top_up",
        "repository_write",
        "deploy",
        "publish",
        "merge",
        "secret_mutation",
        "paid_media_generation",
    ):
        if required not in paid_workflow:
            raise SystemExit(f"DeepSeek supervisor workflow guard missing: {required}")

    retired = list(policy.get("retired_paid_deepseek_workflows") or ())
    _require(bool(retired), "retired DeepSeek workflow registry missing")
    for name in retired:
        if (WORKFLOWS / name).exists():
            raise SystemExit(f"retired paid DeepSeek workflow is still active: {name}")

    paid_policy = policy.get("paid_provider_policy") or {}
    fanout = supervisor.get("research_fanout") or {}
    expansion = fanout.get("expansion_ceiling") or {}
    budget = supervisor.get("budget") or {}
    safety = supervisor.get("safety") or {}
    _require(paid_policy.get("provider") == "deepseek", "unexpected paid provider")
    _require(paid_policy.get("authorized_role") == "EXECUTIVE_SUPERVISOR", "paid DeepSeek role drift")
    _require(paid_policy.get("canonical_workflow") == ".github/workflows/deepseek-supervisor-research.yml", "paid workflow pointer drift")
    _require(int(paid_policy.get("default_max_calls") or 0) == int(fanout.get("default_max_deepseek_calls_per_mission") or -1), "default DeepSeek call cap mismatch")
    _require(int(paid_policy.get("default_max_parallel_calls") or 0) == int(fanout.get("default_max_parallel_deepseek_calls") or -1), "default DeepSeek parallel cap mismatch")
    _require(int(paid_policy.get("expansion_hard_max_calls") or 0) == int(expansion.get("max_deepseek_calls_per_mission") or -1), "expanded DeepSeek call cap mismatch")
    _require(int(paid_policy.get("expansion_hard_max_parallel_calls") or 0) == int(expansion.get("max_parallel_deepseek_calls") or -1), "expanded DeepSeek parallel cap mismatch")
    _require(float(paid_policy.get("max_estimated_cost_usd_per_mission") or 0) == float(budget.get("max_estimated_cost_usd_per_mission") or -1), "DeepSeek mission budget mismatch")
    _require(float(paid_policy.get("max_estimated_cost_usd_per_day") or 0) == float(budget.get("max_estimated_cost_usd_per_day") or -1), "DeepSeek daily budget mismatch")
    _require(paid_policy.get("generic_paid_fallback") is False and safety.get("generic_paid_fallback") is False, "generic paid fallback must remain disabled")
    _require(paid_policy.get("auto_top_up") is False and budget.get("auto_top_up") is False, "auto top-up must remain disabled")
    _require(paid_policy.get("other_paid_providers_authorized") is False, "DeepSeek exception expanded to another paid provider")

    step0 = policy.get("step_zero_failure") or {}
    retry_count = step0.get("automatic_retry_count", -1)
    if isinstance(retry_count, bool) or int(retry_count) != 0:
        raise SystemExit("step-zero automatic retries must remain disabled")

    print("CI_CONTROL_PLANE_GUARD=PASS")
    print("CANONICAL_PAID_DEEPSEEK_WORKFLOW=deepseek-supervisor-research.yml")
    print("LEGACY_PAID_DEEPSEEK_WORKFLOWS_ACTIVE=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
