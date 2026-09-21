#!/usr/bin/env python3
"""Fail closed on Jev fast-decision-plane policy/runtime drift."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config" / "jev_decision_engine_policy.json"
RUNTIME = ROOT / "scripts" / "jev_decision_engine.py"
COORDINATOR = ROOT / "scripts" / "jev_routing_coordinator.py"
MULTI = ROOT / "config" / "multi_agent_operating_policy.json"
ORG = ROOT / "config" / "ai_army_org_chart.json"


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> int:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    require(policy.get("status") == "ACTIVE_SCOPED_PAID_EXCEPTION", "Jev exception inactive")
    auth = policy.get("user_authorization") or {}
    require(auth.get("authorized") is True, "Jev not authorized")
    require(auth.get("persistent_across_chat_tabs") is True, "Jev authorization not persistent")
    require(auth.get("repeat_per_call_approval_required") is False, "Jev incorrectly requires repeat approval")

    provider = policy.get("provider") or {}
    require(provider.get("canonical_model_alias") == "~typesafe/jev-latest", "Jev latest alias drift")
    require(provider.get("last_known_good_model") == "typesafe/jev-1.13", "Jev last-known-good pin drift")
    require(provider.get("automatic_latest_migration") is True, "Jev auto latest migration disabled")

    guard = policy.get("cost_guard") or {}
    require(float(guard.get("request_max_price_prompt_usd_per_million", 999)) <= 0.05, "Jev prompt price ceiling expanded")
    require(float(guard.get("request_max_price_completion_usd_per_million", 999)) == 0.0, "Jev output price ceiling expanded")
    require(guard.get("auto_top_up") is False, "Jev auto top-up enabled")
    require(guard.get("other_paid_model_fallback") is False, "Jev may fall back to other paid model")

    contract = policy.get("decision_contract") or {}
    require(contract.get("typed_output_required") is True, "Jev typed output lost")
    require(contract.get("final_authority") == "chatgpt-top-commander", "Jev gained final authority")
    require(contract.get("model_may_not_expand_candidate_set") is True, "Jev may expand candidate set")
    require(contract.get("model_may_not_expand_permissions") is True, "Jev may expand permissions")
    require(contract.get("model_may_not_authorize_paid_workers") is True, "Jev may authorize paid workers")

    source = RUNTIME.read_text(encoding="utf-8")
    require('DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"' in source, "Jev is not using Decisions API")
    require("~typesafe/jev-latest" in source, "Jev latest alias missing from runtime")
    require("candidate_expansion_blocked" in source, "candidate expansion guard missing")
    require(COORDINATOR.is_file(), "Jev routing coordinator missing")

    multi = json.loads(MULTI.read_text(encoding="utf-8"))
    require((multi.get("routing") or {}).get("jev_default_for_nontrivial_model_and_fanout_choice") is True, "Jev not default for nontrivial routing choice")
    exceptions = (multi.get("security_and_permissions") or {}).get("preauthorized_paid_execution_exceptions") or []
    jev_ex = [x for x in exceptions if isinstance(x, dict) and x.get("role") == "FAST_DECISION_PLANE"]
    require(len(jev_ex) == 1, "Jev paid exception must be exactly one")

    org = json.loads(ORG.read_text(encoding="utf-8"))
    plane = (org.get("hierarchy") or {}).get("decision_plane") or {}
    require(plane.get("role") == "FAST_DECISION_PLANE", "org chart lost Jev decision plane")
    require(plane.get("final_decision_authority") is False, "Jev gained final authority")

    print(json.dumps({
        "status": "PASS",
        "role": "FAST_DECISION_PLANE",
        "model_alias": provider.get("canonical_model_alias"),
        "last_known_good": provider.get("last_known_good_model"),
        "prompt_price_ceiling_per_million": guard.get("request_max_price_prompt_usd_per_million"),
        "auto_top_up": False,
        "other_paid_fallback": False,
        "chatgpt_final_authority": True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
