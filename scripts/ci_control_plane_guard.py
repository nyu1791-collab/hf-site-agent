#!/usr/bin/env python3
"""Static CI control-plane guard.

Prevents long-lived pull requests from fanning one push out into many hosted
runner jobs. This script is deterministic and makes no network calls.
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


def main() -> int:
    policy = _load()
    automatic = list(policy.get("automatic_workflows") or ())
    manual = list(policy.get("manual_specialist_workflows") or ())
    limit = int(policy.get("max_automatic_workflows_per_push") or 0)
    if not 1 <= len(automatic) <= limit <= 2:
        raise SystemExit("automatic workflow fan-out limit violated")

    for name in automatic:
        text = _text(name)
        if "  pull_request:" in text or "\npull_request:" in text:
            raise SystemExit(f"automatic workflow may not use pull_request on long-lived PR: {name}")
        if "  push:" not in text and "\npush:" not in text:
            raise SystemExit(f"automatic workflow needs push trigger: {name}")
        if "cancel-in-progress: true" not in text:
            raise SystemExit(f"automatic workflow must cancel superseded runs: {name}")

    for name in manual:
        text = _text(name)
        if "workflow_dispatch:" not in text:
            raise SystemExit(f"manual specialist missing workflow_dispatch: {name}")
        for forbidden in ("  pull_request:", "  push:", "  schedule:"):
            if forbidden in text:
                raise SystemExit(f"manual specialist has automatic trigger {forbidden.strip()}: {name}")
        if "cancel-in-progress: true" not in text:
            raise SystemExit(f"manual specialist must cancel duplicate dispatches: {name}")

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

    step0 = policy.get("step_zero_failure") or {}
    if int(step0.get("automatic_retry_count") or -1) != 0:
        raise SystemExit("step-zero automatic retries must remain disabled")
    print("CI_CONTROL_PLANE_GUARD=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
