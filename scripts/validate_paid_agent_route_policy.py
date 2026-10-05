#!/usr/bin/env python3
"""Validate the paid API-agent gate without calling any provider."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.paid_agent_route_policy import evaluate_paid_candidate


def main() -> int:
    policy = json.loads((ROOT / "config/paid_agent_route_eligibility_policy.json").read_text(encoding="utf-8"))
    baseline = policy["comparison_baseline"]
    peak = baseline["price_profiles"]["peak"]
    assert baseline["model_id"] == "deepseek-flash"
    assert peak == {"input_cache_hit": 0.006, "input_cache_miss": 0.30, "output": 1.20}
    limits = policy["shared_budget"]
    assert limits["auto_top_up"] is False
    assert limits["maximum_estimated_cost_per_mission"] == 0.50
    assert limits["maximum_spend_plus_reservations_per_utc_day"] == 3.00
    assert policy["routing"]["automatic_paid_fallback"] is False
    assert policy["routing"]["automatic_paid_sibling_substitution"] is False
    assert "openrouter" in set(policy["eligibility"].get("excluded_provider_ids") or [])
    assert policy["routing"]["Vertex_AI"]["enabled"] is False
    assert evaluate_paid_candidate(None)["allowed"] is False

    precedence = json.loads((ROOT / "config/project_rule_precedence_policy.json").read_text(encoding="utf-8"))
    assert precedence["precedence_order"][1] == "LATEST_EXPLICIT_DIRECT_USER_INSTRUCTION_FOR_ITS_STATED_SCOPE"
    assert precedence["current_explicit_boundaries"]["pr_40_open_draft_unmerged"] is True
    assert precedence["current_explicit_boundaries"]["secret_mutation_or_display"] is False
    print(json.dumps({"status": "PASS", "paid_route_requires_evidence": True, "api_calls": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
