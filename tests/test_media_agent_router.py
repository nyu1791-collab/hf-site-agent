import unittest

from scripts import media_agent_router as router


class MediaAgentRouterTests(unittest.TestCase):
    def test_config_is_valid_and_connectors_are_enabled(self):
        config = router.load_config()
        self.assertEqual(router.validate_config(config), [])
        for name in ("descript", "fal", "runway", "post_bridge"):
            self.assertEqual(config["connectors"][name]["status"], "INSTALLED_ENABLED")

    def test_transcription_routes_to_descript(self):
        result = router.route_task("transcribe")
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["role"], "TRANSCRIPTION_AGENT")
        self.assertEqual(result["connector"], "descript")
        self.assertFalse(result["publish_executed"])

    def test_generation_routes_to_fal(self):
        result = router.route_task("generate")
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["role"], "GENERATIVE_MEDIA_AGENT")
        self.assertEqual(result["connector"], "fal")

    def test_advanced_video_routes_to_runway(self):
        result = router.route_task("advanced_video")
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["role"], "ADVANCED_VIDEO_AGENT")
        self.assertEqual(result["connector"], "runway")

    def test_publish_blocks_unconnected_social_account(self):
        result = router.route_task(
            "publish",
            platform="instagram",
            connected_accounts={"youtube"},
            human_publish_approval=True,
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["stop_reason"], "SOCIAL_ACCOUNT_NOT_CONNECTED")
        self.assertFalse(result["publish_executed"])

    def test_publish_waits_for_human_approval_even_when_connected(self):
        result = router.route_task(
            "publish",
            platform="youtube",
            connected_accounts={"youtube"},
            human_publish_approval=False,
        )
        self.assertEqual(result["status"], "AWAITING_HUMAN_APPROVAL")
        self.assertTrue(result["human_approval_required"])
        self.assertFalse(result["publish_executed"])

    def test_publish_approval_only_authorizes_later_connector_action(self):
        result = router.route_task(
            "publish",
            platform="youtube",
            connected_accounts={"youtube"},
            human_publish_approval=True,
        )
        self.assertEqual(result["status"], "AUTHORIZED_FOR_CONNECTOR_ACTION")
        self.assertFalse(result["publish_executed"])
        self.assertFalse(result["generic_paid_fallback"])
        self.assertFalse(result["auto_top_up"])

    def test_unknown_task_fails_closed(self):
        result = router.route_task("do_everything")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["stop_reason"], "UNKNOWN_MEDIA_TASK")

    def test_pipeline_plan_preserves_publish_boundary(self):
        plan = router.build_pipeline_plan(connected_accounts={"youtube"})
        self.assertEqual(plan["status"], "READY")
        self.assertTrue(plan["publish_requires_human_approval"])
        self.assertIn("PUBLISHING_AGENT", plan["pipeline"])
        self.assertFalse(plan["repository_write"])
        self.assertFalse(plan["generic_paid_fallback"])


if __name__ == "__main__":
    unittest.main()
