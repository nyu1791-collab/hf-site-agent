import unittest

from scripts.provider_readiness import evaluate_model_readiness, evaluate_provider_readiness


def evidence(**updates):
    value = {
        "provider": "google",
        "model_id": "gemini-3.8-flash",
        "lifecycle": "GA",
        "model_verified": True,
        "free_verified": True,
        "auth_ok": True,
        "probe_pass": True,
        "capability_pass": True,
        "cost_safe": True,
        "quota_safe": True,
        "circuit_ok": True,
        "secret_guard_ok": True,
        "estimated_cost": 0,
        "active": False,
    }
    value.update(updates)
    return value


class ProviderReadinessTests(unittest.TestCase):
    def test_missing_or_unknown_evidence_is_not_ready(self):
        result = evaluate_model_readiness({"provider": "google", "model_id": "gemini-3.8-flash"})
        self.assertFalse(result["ready"])
        self.assertIn("LIFECYCLE_NOT_PROMOTABLE", result["blockers"])
        self.assertIn("FREE_VERIFIED_REQUIRED", result["blockers"])
        self.assertFalse(result["active"])

    def test_complete_zero_cost_evidence_is_ready_but_not_active(self):
        result = evaluate_model_readiness(evidence())
        self.assertTrue(result["ready"])
        self.assertFalse(result["active"])
        provider = evaluate_provider_readiness("google", [evidence()])
        self.assertTrue(provider["ready"])
        self.assertFalse(provider["active"])

    def test_nonzero_cost_or_preview_lifecycle_blocks_readiness(self):
        result = evaluate_model_readiness(evidence(estimated_cost=0.01))
        self.assertFalse(result["ready"])
        self.assertIn("ZERO_COST_REQUIRED", result["blockers"])
        result = evaluate_model_readiness(evidence(lifecycle="PREVIEW"))
        self.assertFalse(result["ready"])
        self.assertIn("LIFECYCLE_NOT_PROMOTABLE", result["blockers"])

    def test_provider_only_considers_same_provider_and_never_activates(self):
        result = evaluate_provider_readiness("google", [
            evidence(provider="nvidia", model_id="other"),
            evidence(),
        ])
        self.assertEqual(result["models_considered"], 1)
        self.assertEqual(result["ready_models"], ["gemini-3.8-flash"])
        self.assertFalse(result["active"])


if __name__ == "__main__":
    unittest.main()
