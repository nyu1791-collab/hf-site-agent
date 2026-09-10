import unittest

from scripts.ai_army_coordination import build_coordination_packet


class AIArmyCoordinationTests(unittest.TestCase):
    def test_google_blocker_uses_one_nvidia_lead_and_zero_google_calls(self):
        packet = build_coordination_packet(
            {
                "state": "ACCOUNT_EVIDENCE_REQUIRED",
                "live_ready": False,
                "required_evidence": ["CURRENT_GOOGLE_ACCOUNT_TIER"],
            },
            {"live_staging": False},
            source_head="a" * 40,
        )
        self.assertEqual(packet["state"], "NVIDIA_LEAD_ONLY")
        self.assertEqual(packet["call_policy"]["recommended_nvidia_calls_this_stage"], 1)
        self.assertEqual(packet["call_policy"]["recommended_google_calls_this_stage"], 0)
        self.assertFalse(packet["call_policy"]["repeat_nvidia_for_google_account_blocker"])
        self.assertTrue(packet["google"]["external_blocker"])

    def test_two_agent_ready_assigns_google_executor_and_nvidia_reviewer(self):
        packet = build_coordination_packet(
            {"state": "READY_FOR_TWO_AGENT_STAGING", "live_ready": True, "required_evidence": []},
            {"live_staging": False},
        )
        self.assertEqual(packet["state"], "TWO_AGENT_STAGING_READY")
        self.assertEqual(packet["next_action"], "RUN_GOOGLE_EXECUTOR_NVIDIA_REVIEWER")
        self.assertEqual(packet["roles"]["google"]["primary"], "EXECUTOR")
        self.assertEqual(packet["roles"]["nvidia"]["primary"], "INDEPENDENT_REVIEWER")
        self.assertEqual(packet["call_policy"]["recommended_google_calls_this_stage"], 1)
        self.assertEqual(packet["call_policy"]["recommended_nvidia_calls_this_stage"], 1)

    def test_operational_two_agent_result_suppresses_extra_calls(self):
        packet = build_coordination_packet(
            {"state": "READY_FOR_TWO_AGENT_STAGING", "live_ready": True},
            {
                "live_staging": {
                    "operational": True,
                    "live_model_family_count": 2,
                    "executor_provider": "google",
                    "reviewer_provider": "nvidia",
                    "family_separation_pass": True,
                }
            },
        )
        self.assertEqual(packet["state"], "TWO_AGENT_OPERATIONAL")
        self.assertEqual(packet["call_policy"]["recommended_nvidia_calls_this_stage"], 0)
        self.assertEqual(packet["call_policy"]["recommended_google_calls_this_stage"], 0)
        self.assertTrue(packet["live_two_agent"]["family_separation_pass"])

    def test_safety_is_always_fail_closed(self):
        packet = build_coordination_packet({}, {})
        self.assertTrue(packet["safety"]["free_only"])
        self.assertFalse(packet["safety"]["paid_execution_allowed"])
        self.assertFalse(packet["safety"]["paid_fallback_allowed"])
        self.assertFalse(packet["safety"]["production_activation_allowed"])
        self.assertFalse(packet["handoff_contract"]["external_models_may_write_repository"])


if __name__ == "__main__":
    unittest.main()
