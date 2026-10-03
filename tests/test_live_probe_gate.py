import json
import unittest

from scripts.live_probe_gate import evaluate_probe_gate


class LiveProbeGateTests(unittest.TestCase):
    def test_default_or_unknown_evidence_is_blocked(self):
        result = evaluate_probe_gate(
            provider="google",
            expected_model_id="gemini-3.8-flash",
            selected_model_id=None,
            evidence={
                "secret_present": False,
                "model_verified": False,
                "free_verified": False,
                "quota_verified": False,
                "endpoint_verified": False,
                "capabilities_verified": False,
                "estimated_cost": "UNKNOWN",
                "paid_fallback": False,
                "auto_top_up": False,
                "max_retries": 0,
            },
        )
        self.assertFalse(result["allowed"])
        self.assertIn("ESTIMATED_COST_UNKNOWN", result["blockers"])
        self.assertIn("EXPLICIT_PROBE_APPROVAL_REQUIRED", result["blockers"])
        self.assertEqual(result["live_probe_count"], 0)

    def test_all_redacted_preconditions_allow_only_a_separate_probe(self):
        result = evaluate_probe_gate(
            provider="groq",
            expected_model_id="qwen/qwen3.8-27b",
            selected_model_id="qwen/qwen3.8-27b",
            evidence={
                "secret_present": True,
                "model_verified": True,
                "free_verified": True,
                "quota_verified": True,
                "endpoint_verified": True,
                "capabilities_verified": True,
                "estimated_cost": 0,
                "paid_fallback": False,
                "auto_top_up": False,
                "max_retries": 0,
            },
            explicit_approval=True,
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(result["status"], "READY_FOR_EXPLICIT_PROBE")
        self.assertFalse(result["live_probe_executed"])
        self.assertEqual(result["live_probe_count"], 0)

    def test_paid_or_retry_conditions_are_never_overridden_by_model_match(self):
        result = evaluate_probe_gate(
            provider="openrouter",
            expected_model_id="z-ai/glm-5.3-flash:free",
            selected_model_id="z-ai/glm-5.3-flash:free",
            evidence={
                "secret_present": True,
                "model_verified": True,
                "free_verified": True,
                "quota_verified": True,
                "endpoint_verified": True,
                "capabilities_verified": True,
                "estimated_cost": 0,
                "paid_fallback": True,
                "auto_top_up": False,
                "max_retries": 1,
            },
            explicit_approval=True,
        )
        self.assertFalse(result["allowed"])
        self.assertIn("PAID_FALLBACK_NOT_DISABLED", result["blockers"])
        self.assertIn("RETRY_MUST_BE_ZERO", result["blockers"])
        encoded = json.dumps(result)
        self.assertNotIn("api_key", encoded.lower())
        self.assertNotIn("authorization", encoded.lower())


if __name__ == "__main__":
    unittest.main()
