import unittest

from scripts.openrouter_worker_mission import ALLOWED_PATHS, build_mission_packet
import scripts.run_nvidia_google_staging_focused as focused
from scripts.adaptive_performance_policy import profile_for_task


class OpenRouterWorkerMissionTests(unittest.TestCase):
    def test_trial_is_google_best_of_two_then_nvidia_review(self):
        packet = build_mission_packet(source_head="a" * 40)
        self.assertEqual(packet["importance"], "IMPORTANT")
        self.assertEqual(packet["adaptive_redundancy"]["executor_attempts"], 2)
        self.assertFalse(packet["adaptive_redundancy"]["same_provider_attempts_parallel"])
        self.assertIn("GOOGLE_GEMINI_EXECUTOR", packet["chain_of_command"])
        self.assertIn("NVIDIA_NEMOTRON_REVIEWER", packet["chain_of_command"])

    def test_worker_roles_are_role_scoped_and_dynamic(self):
        packet = build_mission_packet()
        self.assertEqual(
            set(packet["worker_roles"]),
            {"GENERAL_WORKER", "CODING_WORKER", "REVIEW_WORKER", "FAST_WORKER"},
        )
        benchmark = packet["benchmark_contract"]
        self.assertFalse(benchmark["generic_router_allowed"])
        self.assertFalse(benchmark["fixed_model_ids_allowed"])
        self.assertTrue(benchmark["benchmark_only_after_exact_free_probe"])
        self.assertEqual(benchmark["max_candidates_per_role"], 3)
        self.assertEqual(benchmark["latency_source"], "LOCAL_MONOTONIC_WALL_CLOCK")

    def test_project_contract_continues_until_project_boundary(self):
        packet = build_mission_packet()
        policy = packet["project_continuation_contract"]
        self.assertFalse(policy["stop_between_substeps"])
        self.assertEqual(policy["current_stop_scope"], "PROJECT_BOUNDARY")
        self.assertEqual(policy["provider_usage_uncertain"], "CHECKPOINT_WITHOUT_REPLAY")
        self.assertEqual(policy["after_project_acceptance"], "PREDICT_NEXT_PROJECT")
        self.assertTrue(policy["auto_continue_safe_followups"])

    def test_mission_scope_is_existing_worker_subsystem_only(self):
        packet = build_mission_packet()
        self.assertEqual(set(packet["allowed_paths"]), set(ALLOWED_PATHS))
        self.assertIn("scripts/worker_selection.py", ALLOWED_PATHS)
        self.assertIn("scripts/probe_free_workers.py", ALLOWED_PATHS)
        self.assertIn("scripts/continuous_project_loop.py", ALLOWED_PATHS)
        self.assertNotIn("config/model_registry.json", ALLOWED_PATHS)

    def test_focused_carrier_defaults_to_important_openrouter_trial(self):
        profile = profile_for_task(
            role=focused.DEFAULT_TRIAL_ROLE,
            risk_level="LOW",
            complexity_level=1,
            metadata={"objective": focused.DEFAULT_TRIAL_OBJECTIVE},
        )
        self.assertEqual(profile.name, "IMPORTANT")
        self.assertEqual(profile.attempts, 2)
        self.assertEqual(profile.output_tokens, 8_192)
        self.assertEqual(profile.mission_token_budget, 49_152)


if __name__ == "__main__":
    unittest.main()
