import copy
import unittest
from datetime import datetime, timezone

from scripts.paid_agent_route_policy import evaluate_paid_candidate


NOW = datetime(2026, 10, 2, 0, 0, tzinfo=timezone.utc)


def evidence():
    common_rates = {"input_cache_hit": 0.006, "input_cache_miss": 0.30, "output": 1.20}
    baseline = {
        "model_id": "deepseek-flash",
        "price_evidence": {
            "official_source": True,
            "source_url": "https://api-docs.deepseek.com/quick_start/pricing/",
            "verified_at": "2026-10-01T00:00:00Z",
            "profiles": {"peak": common_rates, "off_peak": {"input_cache_hit": 0.003, "input_cache_miss": 0.15, "output": 0.60}},
        },
        "benchmark": {
            "measured_at": "2026-09-25T00:00:00Z",
            "workload_id": "research-json-v1",
            "sample_count": 30,
            "quality_score": 0.82,
            "estimated_total_cost_usd": 0.20,
        },
    }
    candidate = {
        "provider_id": "fixture-provider",
        "model_id": "fixture/reasoner-1",
        "price_evidence": {
            "official_source": True,
            "source_url": "https://provider.example/pricing",
            "verified_at": "2026-10-01T00:00:00Z",
            "all_charges_included": True,
            "profiles": {"peak": {"input_cache_hit": 0.005, "input_cache_miss": 0.25, "output": 1.00}},
        },
        "feature_evidence": {
            "official_source": True,
            "source_url": "https://provider.example/docs/tools",
            "verified_features": ["tool_calling", "structured_output", "long_context"],
        },
        "benchmark": {
            "measured_at": "2026-09-25T00:00:00Z",
            "workload_id": "research-json-v1",
            "sample_count": 30,
            "quality_score": 0.90,
            "estimated_total_cost_usd": 0.12,
            "material_improvements": ["higher tool-contract success"],
            "critical_regressions": [],
        },
        "account_status": {"authentication": "AUTH_OK", "billing": "READY", "quota": "READY"},
    }
    return {
        "candidate": candidate,
        "baseline": baseline,
        "task": {
            "workload_id": "research-json-v1",
            "price_profile": "peak",
            "required_features": ["tool_calling", "structured_output"],
        },
        "budget_snapshot": {
            "verified": True,
            "currency": "USD",
            "auto_top_up_enabled": False,
            "daily_spend_usd": 0.40,
            "daily_reserved_usd": 0.20,
            "mission_spend_usd": 0.05,
            "mission_reserved_usd": 0.05,
            "eligible_credit_usd": 1.00,
            "calls_today": 2,
            "parallel_paid_calls": 1,
        },
    }


class PaidAgentRoutePolicyTests(unittest.TestCase):
    def test_cheaper_and_materially_better_candidate_passes_gate(self):
        result = evaluate_paid_candidate(evidence(), now=NOW)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["model_id"], "fixture/reasoner-1")
        self.assertEqual(result["reason_codes"], [])

    def test_openrouter_is_never_eligible_as_paid_candidate(self):
        item = evidence()
        item["candidate"]["provider_id"] = "openrouter"
        result = evaluate_paid_candidate(item, now=NOW)
        self.assertFalse(result["allowed"])
        self.assertIn("PROVIDER_EXCLUDED_FROM_PAID_ROUTE", result["reason_codes"])

    def test_missing_evidence_fails_closed_without_echoing_inputs(self):
        result = evaluate_paid_candidate({"candidate": {"model_id": "secret-bearing-name"}}, now=NOW)
        self.assertFalse(result["allowed"])
        self.assertNotIn("secret-bearing-name", str(result))
        self.assertIn("OFFICIAL_PRICE_SOURCE_MISSING", result["reason_codes"])

    def test_candidate_must_be_strictly_cheaper_and_better(self):
        item = evidence()
        item["candidate"]["benchmark"]["estimated_total_cost_usd"] = 0.20
        result = evaluate_paid_candidate(item, now=NOW)
        self.assertFalse(result["allowed"])
        self.assertIn("SAME_WORKLOAD_TOTAL_COST_NOT_LOWER", result["reason_codes"])

    def test_component_price_cannot_exceed_deepseek_even_if_total_cost_is_lower(self):
        item = evidence()
        item["candidate"]["price_evidence"]["profiles"]["peak"]["output"] = 1.21
        result = evaluate_paid_candidate(item, now=NOW)
        self.assertFalse(result["allowed"])
        self.assertIn("COMPONENT_PRICE_EXCEEDS_DEEPSEEK", result["reason_codes"])

    def test_unknown_ledger_top_up_stale_evidence_and_regressions_block(self):
        item = evidence()
        item["budget_snapshot"]["verified"] = False
        item["budget_snapshot"]["auto_top_up_enabled"] = True
        item["candidate"]["price_evidence"]["verified_at"] = "2026-08-01T00:00:00Z"
        item["candidate"]["benchmark"]["critical_regressions"] = ["schema accuracy"]
        result = evaluate_paid_candidate(item, now=NOW)
        self.assertFalse(result["allowed"])
        self.assertIn("SHARED_BUDGET_LEDGER_UNVERIFIED", result["reason_codes"])
        self.assertIn("AUTO_TOP_UP_MUST_REMAIN_DISABLED", result["reason_codes"])
        self.assertIn("PRICE_EVIDENCE_STALE_OR_MISSING", result["reason_codes"])
        self.assertIn("CRITICAL_REGRESSION", result["reason_codes"])


if __name__ == "__main__":
    unittest.main()
