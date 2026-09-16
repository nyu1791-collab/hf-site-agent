import unittest

from scripts.nvidia_google_bugfix_mission import ALLOWED_PATHS, build_bugfix_project


class NvidiaGoogleBugfixMissionTests(unittest.TestCase):
    def test_project_targets_known_orchestration_defects(self):
        packet = build_bugfix_project(source_head="a" * 40)
        self.assertEqual(packet["project_id"], "orchestration-bugfix-cycle-v1")
        self.assertEqual(packet["importance"], "CRITICAL")
        defect_ids = {item["id"] for item in packet["defects"]}
        self.assertEqual(
            defect_ids,
            {
                "BUG-CAPABILITY-NETWORK-CALL-ACCOUNTING",
                "BUG-FOCUSED-TIMEOUT",
                "BUG-REPOSITORY-CONTEXT-STARVATION",
            },
        )

    def test_google_executes_and_nvidia_reviews_until_project_boundary(self):
        packet = build_bugfix_project()
        contract = packet["execution_contract"]
        self.assertTrue(contract["project_boundary_not_action_boundary"])
        self.assertTrue(contract["same_project_revision_loop"])
        self.assertTrue(contract["checkpoint_on_uncertain_provider_usage"])
        self.assertTrue(contract["no_replay_after_uncertain_provider_usage"])
        self.assertEqual(contract["google_executor_attempts"], 3)
        self.assertTrue(contract["google_same_provider_attempts_serialized"])
        self.assertIn("GOOGLE_GEMINI_EXECUTOR", packet["chain_of_command"])
        self.assertIn("NVIDIA_NEMOTRON_REVIEWER", packet["chain_of_command"])
        self.assertIn("A single action completion is never project completion", packet["completion_rule"])

    def test_scope_is_minimal_and_hard_boundaries_remain(self):
        packet = build_bugfix_project()
        self.assertEqual(set(packet["allowed_paths"]), set(ALLOWED_PATHS))
        self.assertIn("scripts/run_live_staging_from_probe.py", ALLOWED_PATHS)
        self.assertIn("scripts/live_staging_runner.py", ALLOWED_PATHS)
        self.assertNotIn("config/provider_registry.json", ALLOWED_PATHS)
        boundaries = packet["hard_boundaries"]
        for value in boundaries.values():
            self.assertFalse(value)

    def test_acceptance_explicitly_removes_magic_call_budget_and_context_starvation(self):
        acceptance = "\n".join(build_bugfix_project()["acceptance"])
        self.assertIn("zero external-call budget", acceptance)
        self.assertIn("no literal command budget of 6", acceptance)
        self.assertIn("finite long-reasoning-compatible timeout", acceptance)
        self.assertIn("targeted repository context", acceptance)
        self.assertIn("Google same-provider Best-of-N attempts remain serialized", acceptance)


if __name__ == "__main__":
    unittest.main()
