import unittest

import scripts.live_staging_runner as live_runner
import scripts.run_live_staging_from_probe as staging
import scripts.run_nvidia_google_staging_focused as focused


class FocusedRoleTests(unittest.TestCase):
    def setUp(self):
        self.executor_preference = staging.EXECUTOR_PREFERENCE
        self.reviewer_preference = staging.REVIEWER_PREFERENCE
        self.max_output_tokens = live_runner.MAX_OUTPUT_TOKENS

    def tearDown(self):
        staging.EXECUTOR_PREFERENCE = self.executor_preference
        staging.REVIEWER_PREFERENCE = self.reviewer_preference
        live_runner.MAX_OUTPUT_TOKENS = self.max_output_tokens

    def test_focused_roles_prefer_google_executor_and_nvidia_reviewer(self):
        focused._install_focused_roles()
        self.assertEqual(staging.EXECUTOR_PREFERENCE[0], focused.GOOGLE_EXECUTOR)
        self.assertEqual(staging.REVIEWER_PREFERENCE[0], focused.NVIDIA_REVIEWER)
        self.assertNotEqual(staging.MODEL_FAMILIES[focused.GOOGLE_EXECUTOR], staging.MODEL_FAMILIES[focused.NVIDIA_REVIEWER])

    def test_focused_output_budget_is_larger_but_bounded(self):
        focused._install_focused_roles()
        self.assertGreater(live_runner.MAX_OUTPUT_TOKENS, 256)
        self.assertLessEqual(live_runner.MAX_OUTPUT_TOKENS, 1024)
        self.assertEqual(live_runner.MAX_OUTPUT_TOKENS, focused.FOCUSED_MAX_OUTPUT_TOKENS)


if __name__ == "__main__":
    unittest.main()
