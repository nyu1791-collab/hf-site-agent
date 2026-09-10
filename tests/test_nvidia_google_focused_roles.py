import unittest

import scripts.live_staging_runner as live_runner
import scripts.run_live_staging_from_probe as staging
import scripts.run_nvidia_google_staging_focused as focused


class FocusedRoleTests(unittest.TestCase):
    def setUp(self):
        self.executor_preference = staging.EXECUTOR_PREFERENCE
        self.reviewer_preference = staging.REVIEWER_PREFERENCE
        self.plan_builder = staging.build_minimal_staging_plan
        self.bounds = staging.AutonomousBounds
        self.max_output_tokens = live_runner.MAX_OUTPUT_TOKENS
        self.max_prompt_chars = live_runner.MAX_PROMPT_CHARS
        self.max_response_chars = live_runner.MAX_RESPONSE_CHARS

    def tearDown(self):
        staging.EXECUTOR_PREFERENCE = self.executor_preference
        staging.REVIEWER_PREFERENCE = self.reviewer_preference
        staging.build_minimal_staging_plan = self.plan_builder
        staging.AutonomousBounds = self.bounds
        live_runner.MAX_OUTPUT_TOKENS = self.max_output_tokens
        live_runner.MAX_PROMPT_CHARS = self.max_prompt_chars
        live_runner.MAX_RESPONSE_CHARS = self.max_response_chars

    def test_focused_roles_prefer_google_executor_and_nvidia_reviewer(self):
        focused._install_focused_roles()
        self.assertEqual(staging.EXECUTOR_PREFERENCE[0], focused.GOOGLE_EXECUTOR)
        self.assertEqual(staging.REVIEWER_PREFERENCE[0], focused.NVIDIA_REVIEWER)
        self.assertNotEqual(staging.MODEL_FAMILIES[focused.GOOGLE_EXECUTOR], staging.MODEL_FAMILIES[focused.NVIDIA_REVIEWER])

    def test_focused_quality_budgets_are_expanded(self):
        focused._install_focused_roles()
        self.assertEqual(live_runner.MAX_OUTPUT_TOKENS, 2_048)
        self.assertEqual(live_runner.MAX_PROMPT_CHARS, 28_000)
        self.assertEqual(live_runner.MAX_RESPONSE_CHARS, 32_000)
        self.assertEqual(focused.FOCUSED_REQUEST_BUDGET, 10)
        self.assertEqual(focused.FOCUSED_TOKEN_BUDGET, 12_288)

    def test_probe_reuse_adapter_does_not_issue_second_capability_network_call(self):
        class FakeAdapter:
            provider_id = "google"
            config = {"provider_id": "google"}

            def capability_probe(self, model, capability):
                raise AssertionError("redundant capability network call")

        adapter = focused._ProbeReuseAdapter(FakeAdapter())
        result = adapter.capability_probe("gemini-3.8-flash", "structured_output")
        self.assertEqual(result["status"], "CAPABILITY_OK")
        self.assertEqual(result["source"], "REUSED_FRESH_EXACT_MODEL_PROBE")
        self.assertFalse(result["network_call"])


if __name__ == "__main__":
    unittest.main()
