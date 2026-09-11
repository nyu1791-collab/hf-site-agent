import json
import unittest
from pathlib import Path

from scripts.media_agent_runtime import (
    DEFAULT_CONFIG,
    build_connector_state,
    build_media_mission,
    load_config,
    select_transcription_route,
)


class MediaAgentRuntimeTests(unittest.TestCase):
    def test_config_preserves_publish_and_paid_boundaries(self):
        config = load_config(DEFAULT_CONFIG)
        self.assertEqual(config["schema_version"], "media-agent-organization-v1")
        self.assertTrue(config["hard_boundaries"]["publish_requires_human_approval"])
        self.assertFalse(config["hard_boundaries"]["generic_paid_fallback"])
        self.assertFalse(config["hard_boundaries"]["auto_top_up"])
        self.assertFalse(config["hard_boundaries"]["external_ai_direct_repository_write"])
        self.assertIn("deepseek", config["upper_agent_policy"])
        self.assertIn("nvidia", config["upper_agent_policy"])

    def test_youtube_connection_does_not_imply_x_or_instagram(self):
        plan = build_media_mission(
            target_platforms=["youtube", "twitter", "instagram"],
            accounts=[{"platform": "youtube", "needs_reconnect": False}],
            free_gpu_worker_available=True,
        )
        by_id = {task["task_id"]: task for task in plan["tasks"]}
        self.assertEqual(by_id["publish_youtube"]["state"], "APPROVAL_REQUIRED")
        self.assertEqual(by_id["publish_twitter"]["state"], "CONNECTION_REQUIRED")
        self.assertEqual(by_id["publish_instagram"]["state"], "CONNECTION_REQUIRED")
        self.assertFalse(plan["direct_publish_executed"])

    def test_human_approval_only_unlocks_connected_platform(self):
        plan = build_media_mission(
            target_platforms=["youtube", "instagram"],
            accounts=[{"platform": "youtube", "needs_reconnect": False}],
            free_gpu_worker_available=True,
            human_publish_approval=True,
        )
        by_id = {task["task_id"]: task for task in plan["tasks"]}
        self.assertEqual(by_id["publish_youtube"]["state"], "READY_FOR_CONNECTOR")
        self.assertEqual(by_id["publish_instagram"]["state"], "CONNECTION_REQUIRED")

    def test_reconnect_required_account_is_not_ready(self):
        state = build_connector_state(accounts=[{"platform": "youtube", "needs_reconnect": True}])
        self.assertEqual(state["post_bridge"]["platforms"]["youtube"], "CONNECTION_REQUIRED")

    def test_transcription_prefers_free_gpu_then_verified_free_quota(self):
        state = build_connector_state(free_gpu_worker_available=True, groq_free_quota_verified=True)
        self.assertEqual(select_transcription_route(state), "FREE_GPU_WHISPER")
        state = build_connector_state(groq_free_quota_verified=True)
        self.assertEqual(select_transcription_route(state), "GROQ_WHISPER_FREE_QUOTA")

    def test_descript_can_supply_transcription_without_paid_api_activation(self):
        state = build_connector_state(connected_plugins=["Descript"])
        self.assertEqual(select_transcription_route(state), "DESCRIPT_CONNECTOR")

    def test_low_cost_groq_requires_explicit_budget_flag(self):
        blocked = build_connector_state()
        self.assertEqual(select_transcription_route(blocked), "BLOCKED_NEEDS_TRANSCRIPTION_ROUTE")
        approved = build_connector_state(low_cost_audio_approved=True)
        self.assertEqual(select_transcription_route(approved), "GROQ_WHISPER_LOW_COST_APPROVED")

    def test_paid_video_connectors_are_not_auto_enabled(self):
        state = build_connector_state(connected_plugins=["fal", "runway"], paid_media_approved=False)
        self.assertFalse(state["creative_generation"]["fal"])
        self.assertFalse(state["creative_generation"]["runway"])
        self.assertFalse(state["video_editing"]["runway"])

    def test_plan_contains_full_feedback_loop_and_safe_boundaries(self):
        plan = build_media_mission(
            target_platforms=["youtube"],
            accounts=[{"platform": "youtube", "needs_reconnect": False}],
            free_gpu_worker_available=True,
            connected_plugins=["descript"],
        )
        roles = [task["owner_role"] for task in plan["tasks"]]
        for role in (
            "SOCIAL_INTELLIGENCE_AGENT",
            "CONTENT_STRATEGIST",
            "SCRIPT_AGENT",
            "TRANSCRIPTION_AGENT",
            "CLIP_EDITOR_AGENT",
            "CAPTION_LOCALIZATION_AGENT",
            "THUMBNAIL_CREATIVE_AGENT",
            "RIGHTS_SAFETY_AGENT",
            "PUBLISHING_AGENT",
            "ANALYTICS_AGENT",
            "MONETIZATION_AGENT",
        ):
            self.assertIn(role, roles)
        self.assertFalse(plan["hard_boundaries"]["generic_paid_fallback"])
        self.assertFalse(plan["hard_boundaries"]["auto_top_up"])
        self.assertFalse(plan["production_active"])

    def test_config_json_is_valid(self):
        data = json.loads(Path(DEFAULT_CONFIG).read_text(encoding="utf-8"))
        self.assertIsInstance(data.get("pipeline"), list)
        self.assertGreaterEqual(len(data["pipeline"]), 10)


if __name__ == "__main__":
    unittest.main()
