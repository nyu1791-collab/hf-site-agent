import copy
import unittest

from scripts.execution_scope import ExecutionPolicy, ExecutionScopeError, authorize_execution
from scripts.provider_evidence import evaluate_zero_cost, staging_evidence
from scripts.provider_registry import load_provider_registry


def ready_evidence():
    return {
        "current": True,
        "catalog_verified": True,
        "exact_model_verified": True,
        "endpoint_verified": True,
        "auth_verified": True,
        "capability_verified": True,
        "free_evidence_type": "FREE_ENDPOINT",
        "evidence_timestamp": "2026-09-09T00:00:00Z",
        "evidence_source": "official-catalog-fixture",
        "pricing_input": "0",
        "pricing_output": "0",
        "estimated_cost": "0",
        "billing_transition_risk": "NONE",
        "quota_safe": True,
        "current_account_eligible": True,
        "free_verified": True,
        "cost_safe": True,
        "production_active": False,
        "paid_fallback": False,
        "auto_top_up": False,
        "max_retries": 0,
        "circuit_closed": True,
        "technically_ready": True,
    }


class ExecutionScopeTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_provider_registry()

    def test_staging_does_not_require_or_mutate_production_activation(self):
        config = copy.deepcopy(self.registry["providers"]["groq"])
        policy = ExecutionPolicy.from_evidence(
            scope="STAGING",
            provider_id="groq",
            model_id="qwen/qwen3.8-27b",
            evidence=ready_evidence(),
            model_family="Qwen",
            staging_approved=True,
        )
        result = authorize_execution(config, policy)
        self.assertEqual(result["scope"], "STAGING")
        self.assertFalse(config["enabled"])
        self.assertFalse(config["activation_approved"])

    def test_staging_rejects_production_flags(self):
        config = copy.deepcopy(self.registry["providers"]["groq"])
        config["activation_approved"] = True
        policy = ExecutionPolicy.from_evidence(
            scope="STAGING",
            provider_id="groq",
            model_id="qwen/qwen3.8-27b",
            evidence=ready_evidence(),
            staging_approved=True,
        )
        with self.assertRaisesRegex(ExecutionScopeError, "PRODUCTION_ACTIVATION"):
            authorize_execution(config, policy)

    def test_staging_fixed_free_route_is_separate_from_account_zero_cost(self):
        config = copy.deepcopy(self.registry["providers"]["groq"])
        evidence = {
            **ready_evidence(),
            "current_account_eligible": None,
            "free_verified": False,
            "cost_safe": False,
            "staging_free_route_allowed": True,
            "account_zero_cost_verified": False,
        }
        policy = ExecutionPolicy.from_evidence(
            scope="STAGING",
            provider_id="groq",
            model_id="qwen/qwen3.8-27b",
            evidence=evidence,
            staging_approved=True,
        )
        result = authorize_execution(config, policy)
        self.assertEqual(result["scope"], "STAGING")
        self.assertFalse(policy.account_zero_cost_verified)

    def test_production_scope_requires_explicit_active_registry(self):
        config = copy.deepcopy(self.registry["providers"]["groq"])
        policy = ExecutionPolicy.from_evidence(
            scope="PRODUCTION",
            provider_id="groq",
            model_id="qwen/qwen3.8-27b",
            evidence={**ready_evidence(), "production_active": True},
            production_approved=True,
        )
        with self.assertRaisesRegex(ExecutionScopeError, "REGISTRY_ACTIVATION"):
            authorize_execution(config, policy)

    def test_trial_credits_are_not_zero_cost_free_only_evidence(self):
        evidence = ready_evidence()
        evidence["free_evidence_type"] = "TRIAL_CREDIT_WITH_HARD_CAP"
        self.assertFalse(evaluate_zero_cost(evidence)["zero_cost_verified"])
        self.assertIn("TRIAL_CREDIT_NOT_FREE_ONLY_SAFE", evaluate_zero_cost(evidence)["blockers"])

    def test_historical_evidence_cannot_make_staging_ready(self):
        evidence = ready_evidence()
        evidence["current"] = False
        result = staging_evidence(
            evidence,
            provider_id="groq",
            model_id="qwen/qwen3.8-27b",
            model_family="Qwen",
        )
        self.assertFalse(result["technically_ready"])
        self.assertIn("CURRENT_EVIDENCE_REQUIRED", result["blockers"])

    def test_unknown_account_eligibility_cannot_make_zero_cost_ready(self):
        evidence = ready_evidence()
        evidence.pop("current_account_eligible")
        result = evaluate_zero_cost(evidence)
        self.assertFalse(result["zero_cost_verified"])
        self.assertIn("CURRENT_ACCOUNT_ELIGIBILITY_UNKNOWN", result["blockers"])


if __name__ == "__main__":
    unittest.main()
