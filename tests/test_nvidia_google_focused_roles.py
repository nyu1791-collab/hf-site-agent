import unittest

import scripts.autonomous_mission as autonomous_mission
import scripts.live_staging_runner as live_runner
import scripts.run_live_staging_from_probe as staging
import scripts.run_nvidia_google_staging_focused as focused
from scripts.adaptive_multi_attempt import build_adaptive_executor_reviewer_callbacks
from scripts.adaptive_performance_policy import CRITICAL, IMPORTANT, NORMAL, classify_importance


class FocusedRoleTests(unittest.TestCase):
    def setUp(self):
        self.executor_preference = staging.EXECUTOR_PREFERENCE
        self.reviewer_preference = staging.REVIEWER_PREFERENCE
        self.max_output_tokens = live_runner.MAX_OUTPUT_TOKENS
        self.max_prompt_chars = live_runner.MAX_PROMPT_CHARS
        self.max_response_chars = live_runner.MAX_RESPONSE_CHARS
        self.callbacks = live_runner.build_executor_reviewer_callbacks
        self.live_safe_json = live_runner.safe_json
        self.auto_safe_json = autonomous_mission.safe_json

    def tearDown(self):
        staging.EXECUTOR_PREFERENCE = self.executor_preference
        staging.REVIEWER_PREFERENCE = self.reviewer_preference
        live_runner.MAX_OUTPUT_TOKENS = self.max_output_tokens
        live_runner.MAX_PROMPT_CHARS = self.max_prompt_chars
        live_runner.MAX_RESPONSE_CHARS = self.max_response_chars
        live_runner.build_executor_reviewer_callbacks = self.callbacks
        live_runner.safe_json = self.live_safe_json
        autonomous_mission.safe_json = self.auto_safe_json

    def test_focused_roles_prefer_google_executor_and_nvidia_reviewer(self):
        focused._install_focused_roles()
        self.assertEqual(staging.EXECUTOR_PREFERENCE[0], focused.GOOGLE_EXECUTOR)
        self.assertEqual(staging.REVIEWER_PREFERENCE[0], focused.NVIDIA_REVIEWER)
        self.assertNotEqual(staging.MODEL_FAMILIES[focused.GOOGLE_EXECUTOR], staging.MODEL_FAMILIES[focused.NVIDIA_REVIEWER])

    def test_focused_profile_raises_context_output_and_loop_budget(self):
        focused._install_focused_roles()
        self.assertEqual(live_runner.MAX_OUTPUT_TOKENS, 12_288)
        self.assertGreaterEqual(live_runner.MAX_PROMPT_CHARS, 100_000)
        self.assertGreaterEqual(live_runner.MAX_RESPONSE_CHARS, 120_000)
        self.assertEqual(live_runner.build_executor_reviewer_callbacks, build_adaptive_executor_reviewer_callbacks)

    def test_importance_policy_spends_redundancy_only_when_justified(self):
        self.assertEqual(classify_importance(role="small formatting task"), "NORMAL")
        self.assertEqual(classify_importance(role="repository implementation"), "IMPORTANT")
        self.assertEqual(classify_importance(role="concurrency migration"), "CRITICAL")
        self.assertEqual(NORMAL.attempts, 1)
        self.assertEqual(IMPORTANT.attempts, 2)
        self.assertEqual(CRITICAL.attempts, 3)
        self.assertGreater(CRITICAL.output_tokens, IMPORTANT.output_tokens)
        self.assertGreater(IMPORTANT.output_tokens, NORMAL.output_tokens)

    def test_probe_reuse_does_not_make_an_extra_network_capability_call(self):
        class FakeAdapter:
            provider_id = "google"
            def capability_probe(self, *_args, **_kwargs):
                raise AssertionError("underlying network probe must not run")

        proxy = focused._ProbeReuseAdapter(FakeAdapter())
        result = proxy.capability_probe("gemini-3.8-flash", "structured_output")
        self.assertEqual(result["status"], "CAPABILITY_OK")
        self.assertFalse(result["network_call"])


if __name__ == "__main__":
    unittest.main()
