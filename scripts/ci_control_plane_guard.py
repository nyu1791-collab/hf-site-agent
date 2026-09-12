#!/usr/bin/env python3
"""Static CI control-plane guard.

Prevents long-lived pull requests from fanning one push out into many hosted
runner jobs and prevents bounded paid continuation from silently multiplying.
This script is deterministic and makes no network calls.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config" / "ci_execution_policy.json"
WORKFLOWS = ROOT / ".github" / "workflows"


def _load() -> dict:
    value = json.loads(POLICY.read_text(encoding="utf-8"))
    if value.get("schema_version") != "ci-execution-policy-v1":
        raise SystemExit("invalid ci execution policy schema")
    return value


def _text(name: str) -> str:
    path = WORKFLOWS / name
    if not path.is_file():
        raise SystemExit(f"workflow missing: {name}")
    return path.read_text(encoding="utf-8")


def _has_automatic_trigger(text: str, trigger: str) -> bool:
    needle = f"  {trigger}:"
    return needle in text or f"\n{trigger}:" in text


def main() -> int:
    policy = _load()
    automatic = list(policy.get("automatic_workflows") or ())
    manual = list(policy.get("manual_specialist_workflows") or ())
    limit = int(policy.get("max_automatic_workflows_per_push") or 0)
    if not 1 <= len(automatic) <= limit <= 2:
        raise SystemExit("automatic workflow fan-out limit violated")

    for name in automatic:
        text = _text(name)
        if _has_automatic_trigger(text, "pull_request"):
            raise SystemExit(f"automatic workflow may not use pull_request on long-lived PR: {name}")
        if not _has_automatic_trigger(text, "push"):
            raise SystemExit(f"automatic workflow needs push trigger: {name}")
        if "cancel-in-progress: true" not in text:
            raise SystemExit(f"automatic workflow must cancel superseded runs: {name}")

    for name in manual:
        text = _text(name)
        if "workflow_dispatch:" not in text:
            raise SystemExit(f"manual specialist missing workflow_dispatch: {name}")
        for forbidden in ("pull_request", "push", "schedule"):
            if _has_automatic_trigger(text, forbidden):
                raise SystemExit(f"manual specialist has automatic trigger {forbidden}: {name}")
        if "cancel-in-progress: true" not in text:
            raise SystemExit(f"manual specialist must cancel duplicate dispatches: {name}")

    # One explicit bounded internal carrier may be automatic in addition to the
    # core CI gate. It is not a generic push workflow: it must be path-scoped to
    # the dedicated approval trigger and retain read-only repository authority.
    carriers = list(policy.get("bounded_internal_carrier_workflows") or ())
    carrier_limit = int(policy.get("max_bounded_internal_carriers") or 0)
    if not 0 <= len(carriers) <= carrier_limit <= 1:
        raise SystemExit("bounded internal carrier fan-out limit violated")
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

    deepseek = _text("deepseek-paid-parallel.yml")
    for required in (
        "DEEPSEEK_API_KEY",
        "allow_deepseek_paid_trial=true",
        "max_estimated_cost_usd",
        "generic_paid_fallback",
        "auto_top_up",
    ):
        if required not in deepseek:
            raise SystemExit(f"DeepSeek paid lane guard missing: {required}")

    paid_policy = policy.get("paid_provider_policy") or {}
    if int(paid_policy.get("max_calls") or 0) > 2:
        raise SystemExit("DeepSeek paid call cap exceeded")
    if int(paid_policy.get("max_parallel_calls") or 0) > 2:
        raise SystemExit("DeepSeek paid parallel cap exceeded")
    if float(paid_policy.get("max_estimated_cost_usd") or 0) > 0.10:
        raise SystemExit("DeepSeek paid execution budget exceeded")
    if paid_policy.get("generic_paid_fallback") is not False:
        raise SystemExit("generic paid fallback must remain disabled")
    if paid_policy.get("auto_top_up") is not False:
        raise SystemExit("auto top-up must remain disabled")

    step0 = policy.get("step_zero_failure") or {}
    retry_count = step0.get("automatic_retry_count", -1)
    if isinstance(retry_count, bool) or int(retry_count) != 0:
        raise SystemExit("step-zero automatic retries must remain disabled")
    print("CI_CONTROL_PLANE_GUARD=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
