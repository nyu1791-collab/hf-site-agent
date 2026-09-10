import unittest

from scripts.ai_army_coordination import build_coordination_packet


class AIArmyCoordinationTests(unittest.TestCase):
    @staticmethod
    def settled_google_report(*, google_calls=3, nvidia_calls=0, revision_count=0, unsettled=0):
        providers = {"google": google_calls}
        if nvidia_calls:
            providers["nvidia"] = nvidia_calls
        return {
            "status": "blocked",
            "runtime": {"stop_reason": "PROVIDER_INTERRUPTED", "revision_count": revision_count},
            "budget": {"requests_used": google_calls, "unsettled_requests": unsettled},
            "live_staging": {
                "executor_provider": "google",
                "reviewer_provider": "nvidia",
                "family_separation_pass": True,
                "external_model_calls": google_calls + nvidia_calls,
                "providers": providers,
            },
            "safety": {
                "paid_execution_count": 0,
                "paid_fallback_count": 0,
                "production_active": False,
                "secret_values_displayed": 0,
                "secret_values_logged": 0,
                "secret_values_persisted": 0,
                "secret_values_returned_to_model": 0,
            },
        }

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
            {},
        )
        self.assertEqual(packet["state"], "TWO_AGENT_STAGING_READY")
        self.assertEqual(packet["next_action"], "RUN_GOOGLE_EXECUTOR_NVIDIA_REVIEWER")
        self.assertEqual(packet["roles"]["google"]["primary"], "COMMANDER_EXECUTOR")
        self.assertEqual(packet["roles"]["nvidia"]["primary"], "COMMANDER_INDEPENDENT_REVIEWER")
        self.assertEqual(packet["call_policy"]["recommended_google_calls_this_stage"], 1)
        self.assertEqual(packet["call_policy"]["recommended_nvidia_calls_this_stage"], 1)

    def test_failed_two_agent_attempt_suppresses_unplanned_fallback_calls(self):
        packet = build_coordination_packet(
            {"state": "READY_FOR_TWO_AGENT_STAGING", "live_ready": True},
            {
                "status": "blocked",
                "stop_reason": "CAPABILITY_BENCHMARK_FAILED",
                "live_staging": False,
            },
        )
        self.assertEqual(packet["state"], "TWO_AGENT_ATTEMPT_FAILED")
        self.assertEqual(packet["next_action"], "STOP_AND_REVIEW_TWO_AGENT_FAILURE")
        self.assertEqual(packet["call_policy"]["recommended_nvidia_calls_this_stage"], 0)
        self.assertEqual(packet["call_policy"]["recommended_google_calls_this_stage"], 0)
        self.assertFalse(packet["call_policy"]["extra_fallback_after_two_agent_attempt"])

    def test_settled_google_outage_authorizes_one_planned_nvidia_degraded_lead(self):
        packet = build_coordination_packet(
            {"state": "READY_FOR_TWO_AGENT_STAGING", "live_ready": True},
            self.settled_google_report(),
        )
        self.assertEqual(packet["state"], "GOOGLE_PROVIDER_DEGRADED_NVIDIA_LEAD")
        self.assertEqual(packet["next_action"], "RUN_AT_MOST_ONE_GUARDED_NVIDIA_LEAD_CALL")
        self.assertEqual(packet["call_policy"]["recommended_google_calls_this_stage"], 0)
        self.assertEqual(packet["call_policy"]["recommended_nvidia_calls_this_stage"], 1)
        self.assertTrue(packet["call_policy"]["planned_degraded_nvidia_lead"])
        self.assertTrue(packet["call_policy"]["safe_independent_lane_continuation"])
        self.assertTrue(packet["google"]["conclusive_provider_constraint"])
        self.assertTrue(packet["roles"]["nvidia"]["degraded_lead_authorized"])
        self.assertFalse(packet["roles"]["nvidia"]["reviewer_already_participated"])

    def test_settled_google_revision_failure_continues_workers_without_second_nvidia_call(self):
        packet = build_coordination_packet(
            {"state": "READY_FOR_TWO_AGENT_STAGING", "live_ready": True},
            self.settled_google_report(google_calls=6, nvidia_calls=1, revision_count=1),
        )
        self.assertEqual(packet["state"], "GOOGLE_REVISION_DEGRADED_REVIEWER_PRESENT")
        self.assertEqual(packet["next_action"], "CONTINUE_INDEPENDENT_WORKER_BOOTSTRAP")
        self.assertEqual(packet["call_policy"]["recommended_google_calls_this_stage"], 0)
        self.assertEqual(packet["call_policy"]["recommended_nvidia_calls_this_stage"], 0)
        self.assertFalse(packet["call_policy"]["planned_degraded_nvidia_lead"])
        self.assertTrue(packet["call_policy"]["safe_independent_lane_continuation"])
        self.assertTrue(packet["google"]["conclusive_provider_constraint"])
        self.assertTrue(packet["roles"]["nvidia"]["reviewer_already_participated"])
        self.assertFalse(packet["roles"]["nvidia"]["degraded_lead_authorized"])
        self.assertEqual(packet["live_two_agent"]["revision_count"], 1)

    def test_nvidia_call_without_revision_proof_does_not_misclassify_failure_as_google(self):
        packet = build_coordination_packet(
            {"state": "READY_FOR_TWO_AGENT_STAGING", "live_ready": True},
            self.settled_google_report(google_calls=3, nvidia_calls=1, revision_count=0),
        )
        self.assertEqual(packet["state"], "TWO_AGENT_ATTEMPT_FAILED")
        self.assertEqual(packet["next_action"], "STOP_AND_REVIEW_TWO_AGENT_FAILURE")
        self.assertFalse(packet["call_policy"]["safe_independent_lane_continuation"])

    def test_unsettled_google_interruption_does_not_authorize_degraded_lead(self):
        packet = build_coordination_packet(
            {"state": "READY_FOR_TWO_AGENT_STAGING", "live_ready": True},
            self.settled_google_report(google_calls=3, unsettled=1),
        )
        self.assertEqual(packet["state"], "TWO_AGENT_ATTEMPT_FAILED")
        self.assertEqual(packet["next_action"], "STOP_AND_REVIEW_TWO_AGENT_FAILURE")
        self.assertEqual(packet["call_policy"]["recommended_nvidia_calls_this_stage"], 0)
        self.assertFalse(packet["call_policy"]["planned_degraded_nvidia_lead"])
        self.assertFalse(packet["call_policy"]["safe_independent_lane_continuation"])

    def test_operational_two_agent_result_suppresses_extra_stage_calls(self):
        packet = build_coordination_packet(
            {"state": "READY_FOR_TWO_AGENT_STAGING", "live_ready": True},
            {
                "status": "completed",
                "live_staging": {
                    "operational": True,
                    "live_model_family_count": 2,
                    "executor_provider": "google",
                    "reviewer_provider": "nvidia",
                    "family_separation_pass": True,
                },
            },
        )
        self.assertEqual(packet["state"], "TWO_AGENT_OPERATIONAL")
        self.assertEqual(packet["call_policy"]["recommended_nvidia_calls_this_stage"], 0)
        self.assertEqual(packet["call_policy"]["recommended_google_calls_this_stage"], 0)
        self.assertTrue(packet["live_two_agent"]["family_separation_pass"])

    def test_performance_policy_is_adaptive_and_same_provider_safe(self):
        packet = build_coordination_packet({}, {})
        policy = packet["performance_policy"]
        self.assertEqual(policy["normal"]["executor_attempts"], 1)
        self.assertEqual(policy["important"]["executor_attempts"], 2)
        self.assertEqual(policy["critical"]["executor_attempts"], 3)
        self.assertTrue(policy["default_is_not_redundant"])
        self.assertEqual(policy["critical"]["output_token_ceiling"], 12_288)
        self.assertEqual(policy["mission_token_ceiling"], 81_920)
        self.assertEqual(policy["important"]["execution"], "SERIAL_SAME_PROVIDER_BEST_OF_N")
        self.assertFalse(packet["call_policy"]["same_provider_independent_attempts_parallel"])
        self.assertIn("CHECKPOINT", policy["provider_interruption_rule"])
        self.assertIn("SETTLED_GOOGLE_CONSTRAINT", policy["provider_interruption_rule"])

    def test_project_continuation_stops_at_project_not_action_boundary(self):
        packet = build_coordination_packet({}, {})
        policy = packet["project_continuation_policy"]
        self.assertFalse(policy["stop_between_actions"])
        self.assertFalse(policy["stop_between_mission_phases"])
        self.assertEqual(policy["current_stop_scope"], "PROJECT_BOUNDARY")
        self.assertEqual(policy["after_project_acceptance"], "PREDICT_NEXT_PROJECT")
        self.assertTrue(policy["auto_continue_safe_followups"])
        self.assertEqual(policy["max_auto_followup_projects_per_carrier"], 3)
        self.assertFalse(policy["infinite_loop_allowed"])

    def test_subordinate_model_plan_keeps_commanders_and_benchmarks_workers(self):
        packet = build_coordination_packet({}, {})
        plan = packet["subordinate_model_plan"]
        self.assertEqual(plan["google_corps"]["commander"], "gemini-3.8-flash")
        self.assertEqual(plan["nvidia_corps"]["commander"], "nvidia/nemotron-3.5-lightning-30b-a3b")
        self.assertIn("RUN_SMALL_CAPABILITY_BENCHMARK", plan["admission_sequence"])
        self.assertIn("measured_latency", plan["selection_metrics"])
        self.assertIn("CANARY_ON_SECOND_SAMPLE_IN_STAGING", plan["admission_sequence"])
        self.assertTrue(plan["commander_override"])
        self.assertFalse(plan["worker_direct_repository_write"])

    def test_minimum_guards_only_cover_hard_boundaries(self):
        packet = build_coordination_packet({}, {})
        guards = packet["minimum_guards"]
        self.assertFalse(guards["paid_fallback_allowed"])
        self.assertFalse(guards["secret_exposure_allowed"])
        self.assertFalse(guards["production_activation_allowed"])
        self.assertFalse(guards["repository_write_by_external_model_allowed"])
        self.assertFalse(guards["duplicate_same_request_allowed"])
        self.assertFalse(packet["handoff_contract"]["external_models_may_write_repository"])


if __name__ == "__main__":
    unittest.main()
