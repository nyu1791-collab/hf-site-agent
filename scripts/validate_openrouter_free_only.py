#!/usr/bin/env python3
"""Repository-wide static enforcement of OpenRouter free-only execution."""
from __future__ import annotations

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
WORKFLOWS = ROOT / ".github" / "workflows"

DIRECT_INFERENCE_PATTERNS = (
    "https://openrouter.ai/api/v1/chat/completions",
    "https://openrouter.ai/api/alpha/decisions",
)

SHARED_GUARD_MARKERS = (
    "assert_openrouter_free_model",
    "decide_openrouter_free_model",
    "price_guard_allows",
)


def is_direct_openrouter_inference(source: str) -> bool:
    if any(marker in source for marker in DIRECT_INFERENCE_PATTERNS):
        return True
    if "https://openrouter.ai/api/v1" in source and "OpenAI(" in source:
        return True
    if "DECISIONS_URL" in source and "_json_request(" in source:
        return True
    return False


def main() -> int:
    violations: list[dict[str, str]] = []
    direct_files: list[str] = []
    guarded_files: list[str] = []

    for path in sorted(SCRIPTS.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8", errors="replace")
        if rel == "scripts/openrouter_free_gate.py":
            continue
        if not is_direct_openrouter_inference(source):
            continue
        direct_files.append(rel)
        if not any(marker in source for marker in SHARED_GUARD_MARKERS):
            violations.append({"path": rel, "reason": "DIRECT_OPENROUTER_INFERENCE_WITHOUT_SHARED_FREE_GATE"})
        else:
            guarded_files.append(rel)

    adapter = (SCRIPTS / "provider_adapters.py").read_text(encoding="utf-8")
    if "assert_openrouter_free_model" not in adapter or '"allow_fallbacks": False' not in adapter:
        violations.append({"path": "scripts/provider_adapters.py", "reason": "OPENROUTER_ADAPTER_GATE_OR_NO_FALLBACK_MISSING"})

    design = (SCRIPTS / "design_council.py").read_text(encoding="utf-8")
    if "OPENROUTER_API_KEY" not in design or "assert_openrouter_free_model" not in design:
        violations.append({"path": "scripts/design_council.py", "reason": "DESIGN_COUNCIL_NOT_BOUND_TO_DEDICATED_FREE_GATE"})

    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        rel = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8", errors="replace")
        if "openrouter.ai" in source and "secrets.AI_API_KEY" in source:
            violations.append({"path": rel, "reason": "OPENROUTER_WORKFLOW_USES_GENERIC_AI_API_KEY"})
        if "openrouter.ai" in source and re.search(r"\bcurl\b|\bwget\b", source):
            violations.append({"path": rel, "reason": "OPENROUTER_DIRECT_SHELL_HTTP_REQUIRES_EXPLICIT_REVIEW"})

    paid = json.loads((ROOT / "config/paid_agent_route_eligibility_policy.json").read_text(encoding="utf-8"))
    excluded = {str(x).lower() for x in (paid.get("eligibility") or {}).get("excluded_provider_ids", [])}
    if "openrouter" not in excluded:
        violations.append({"path": "config/paid_agent_route_eligibility_policy.json", "reason": "OPENROUTER_NOT_EXCLUDED_FROM_PAID_ROUTES"})

    multi = json.loads((ROOT / "config/multi_agent_operating_policy.json").read_text(encoding="utf-8"))
    exceptions = (multi.get("security_and_permissions") or {}).get("preauthorized_paid_execution_exceptions") or []
    if any("openrouter" in str(item.get("provider") or "").lower() for item in exceptions if isinstance(item, dict)):
        violations.append({"path": "config/multi_agent_operating_policy.json", "reason": "OPENROUTER_PAID_EXCEPTION_REMAINS"})

    report = {
        "status": "PASS" if not violations else "FAIL",
        "openrouter_direct_inference_file_count": len(direct_files),
        "guarded_direct_inference_file_count": len(guarded_files),
        "direct_inference_files": direct_files,
        "violations": violations,
        "openrouter_paid_models_allowed": False,
        "openrouter_paid_fallback_allowed": False,
        "unknown_price_action": "BLOCKED_UNVERIFIED_PRICE",
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if violations:
        raise SystemExit(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
