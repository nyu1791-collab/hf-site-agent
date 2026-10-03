import json
import unittest
from pathlib import Path

from scripts.media_agent_runtime import (
    CONNECTOR_STATE_TTL_SECONDS,
    DEFAULT_CONFIG,
    build_connector_state,
    build_media_mission,
    load_config,
    select_edit_route,
    select_generation_route,
    select_transcription_route,
    validate_platform_metadata,
)


def platform_metadata(*platforms: str, synthetic: bool = False):
    result = {}
    for platform in platforms:
        canonical = "twitter" if platform == "x" else platform
        if canonical == "youtube":
            result[canonical] = {
                "title": "Validated title",
                "caption": "Validated caption",
                "media_ready": True,
                "media_count": 1,
                "media_type": "video",
                "contains_synthetic_media": bool(synthetic),
            }
        elif canonical == "instagram":
            result[canonical] = {
                "caption": "Validated caption",
                "media_ready": True,
                "media_count": 1,
            }
        elif canonical == "twitter":
            result[canonical] = {"caption": "Validated caption"}
    return result


class MediaAgentRuntimeTests(unittest.TestCase):
    def test_config_preserves_publish_and_paid_boundaries(self):
        config = load_config(DEFAULT_CONFIG)
        self.assertEqual(config["schema_version"], "media-agent-organization-v1")
        self.assertTrue(config["hard_boundaries"]["publish_requires_human_approval"])
        self.assertFalse(config["hard_boundaries"]["generic_paid_fallback"])
        self.assertFalse(config["hard_boundaries"]["auto_top_up"])
        self.assertFalse(config["hard_boundaries"]["external_ai_direct_repository_write"])
        self.assertTrue(config["principles"]["service_connectors_are_not_reasoning_agent_identities"])
        self.assertTrue(config["principles"]["installed_plugin_does_not_imply_paid_execution_approval"])
        self.assertIn("GENERATIVE_MEDIA_AGENT", config["roles"])
        self.assertIn("DESCRIPT_CONNECTOR", config["connectors"])
        self.assertIn("FAL_CONNECTOR_APPROVED", config["connectors"])
        self.assertIn("RUNWAY_CONNECTOR_APPROVED", config["connectors"])
        self.assertIn("deepseek", config["upper_agent_policy"])
        self.assertIn("nvidia", config["upper_agent_policy"])

    def test_youtube_connection_does_not_imply_x_or_instagram(self):
        plan = build_media_mission(
            target_platforms=["youtube", "x", "instagram"],
            accounts=[{"platform": "youtube", "needs_reconnect": False}],
            free_gpu_worker_available=True,
            rights_status="verified",
            platform_metadata=platform_metadata("youtube", "x", "instagram"),
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
            rights_status="verified",
            platform_metadata=platform_metadata("youtube", "instagram"),
        )
        by_id = {task["task_id"]: task for task in plan["tasks"]}
        self.assertEqual(by_id["publish_youtube"]["state"], "READY_FOR_CONNECTOR")
        self.assertEqual(by_id["publish_instagram"]["state"], "CONNECTION_REQUIRED")

    def test_reconnect_required_account_is_not_ready(self):
        state = build_connector_state(accounts=[{"platform": "youtube", "needs_reconnect": True}])
        self.assertEqual(state["post_bridge"]["platforms"]["youtube"], "CONNECTION_REQUIRED")

    def test_stale_connector_snapshot_blocks_routes(self):
        state = build_connector_state(
            accounts=[{"platform": "youtube", "needs_reconnect": False}],
            connected_plugins=["descript", "fal", "runway"],
            connector_snapshot_age_seconds=CONNECTOR_STATE_TTL_SECONDS + 1,
            paid_media_approved=True,
        )
        self.assertFalse(state["snapshot"]["fresh"])
        self.assertEqual(state["post_bridge"]["platforms"]["youtube"], "CONNECTION_STATE_STALE")
        self.assertFalse(state["transcription"]["descript"])
        self.assertFalse(state["creative_generation"]["fal"])
        self.assertFalse(state["creative_generation"]["runway"])

    def test_transcription_prefers_free_gpu_then_verified_free_quota(self):
        state = build_connector_state(free_gpu_worker_available=True, groq_free_quota_verified=True)
        self.assertEqual(select_transcription_route(state), "FREE_GPU_WHISPER")
        state = build_connector_state(groq_free_quota_verified=True)
        self.assertEqual(select_transcription_route(state), "GROQ_WHISPER_FREE_QUOTA")

    def test_descript_can_supply_transcription_without_paid_api_activation(self):
        state = build_connector_state(connected_plugins=["Descript"])
        self.assertTrue(state["plugin_state"]["descript"])
        self.assertEqual(select_transcription_route(state), "DESCRIPT_CONNECTOR")

    def test_low_cost_groq_requires_explicit_budget_flag(self):
        blocked = build_connector_state()
        self.assertEqual(select_transcription_route(blocked), "BLOCKED_NEEDS_TRANSCRIPTION_ROUTE")
        approved = build_connector_state(low_cost_audio_approved=True)
        self.assertEqual(select_transcription_route(approved), "GROQ_WHISPER_LOW_COST_APPROVED")

    def test_paid_video_connectors_are_not_auto_enabled(self):
        state = build_connector_state(connected_plugins=["fal", "runway"], paid_media_approved=False)
        self.assertTrue(state["plugin_state"]["fal"])
        self.assertTrue(state["plugin_state"]["runway"])
        self.assertFalse(state["creative_generation"]["fal"])
        self.assertFalse(state["creative_generation"]["runway"])
        self.assertFalse(state["video_editing"]["fal"])
        self.assertFalse(state["video_editing"]["runway"])
        self.assertEqual(select_generation_route(state), "BLOCKED_NEEDS_APPROVED_MEDIA_GENERATION_ROUTE")

    def test_paid_generation_route_remains_blocked_even_when_caller_passes_approval_flag(self):
        state = build_connector_state(connected_plugins=["fal", "runway"], paid_media_approved=True)
        self.assertEqual(select_generation_route(state), "BLOCKED_NEEDS_APPROVED_MEDIA_GENERATION_ROUTE")
        self.assertEqual(select_generation_route(state, advanced_video_required=True), "BLOCKED_NEEDS_APPROVED_MEDIA_GENERATION_ROUTE")

    def test_descript_precedes_paid_semantic_editors(self):
        state = build_connector_state(connected_plugins=["descript", "fal", "runway"], paid_media_approved=True)
        self.assertEqual(select_edit_route(state, semantic_edit_required=True), ["FFMPEG_DETERMINISTIC", "DESCRIPT_CONNECTOR"])

    def test_generation_task_blocks_without_explicit_paid_media_approval(self):
        plan = build_media_mission(
            target_platforms=["youtube"],
            accounts=[{"platform": "youtube", "needs_reconnect": False}],
            connected_plugins=["descript", "fal", "runway"],
            generative_media_required=True,
            paid_media_approved=False,
            rights_status="verified",
            platform_metadata=platform_metadata("youtube"),
        )
        by_id = {task["task_id"]: task for task in plan["tasks"]}
        self.assertEqual(by_id["generate_assets"]["state"], "BLOCKED")
        self.assertEqual(plan["selected_routes"]["generation"], "BLOCKED_NEEDS_APPROVED_MEDIA_GENERATION_ROUTE")
        self.assertFalse(plan["hard_boundaries"]["installed_plugin_implies_paid_execution_approval"])

    def test_generation_task_stays_blocked_for_paid_media_even_when_advanced_and_approved(self):
        plan = build_media_mission(
            target_platforms=["youtube"],
            accounts=[{"platform": "youtube", "needs_reconnect": False}],
            connected_plugins=["fal", "runway"],
            generative_media_required=True,
            advanced_video_required=True,
            paid_media_approved=True,
            rights_status="verified",
            platform_metadata=platform_metadata("youtube"),
        )
        by_id = {task["task_id"]: task for task in plan["tasks"]}
        self.assertEqual(by_id["generate_assets"]["state"], "BLOCKED")
        self.assertEqual(plan["selected_routes"]["generation"], "BLOCKED_NEEDS_APPROVED_MEDIA_GENERATION_ROUTE")

    def test_rights_and_disclosure_gate_publish(self):
        unknown = build_media_mission(
            target_platforms=["youtube"],
            accounts=[{"platform": "youtube", "needs_reconnect": False}],
            human_publish_approval=True,
            platform_metadata=platform_metadata("youtube"),
        )
        self.assertEqual({t["task_id"]: t for t in unknown["tasks"]}["publish_youtube"]["state"], "RIGHTS_REVIEW_REQUIRED")
        synthetic = build_media_mission(
            target_platforms=["youtube"],
            accounts=[{"platform": "youtube", "needs_reconnect": False}],
            human_publish_approval=True,
            rights_status="verified",
            synthetic_media=True,
            synthetic_disclosure_ready=False,
            platform_metadata=platform_metadata("youtube", synthetic=True),
        )
        self.assertEqual({t["task_id"]: t for t in synthetic["tasks"]}["publish_youtube"]["state"], "RIGHTS_REVIEW_REQUIRED")

    def test_youtube_synthetic_metadata_requires_disclosure_field(self):
        metadata = platform_metadata("youtube")
        validation = validate_platform_metadata("youtube", metadata["youtube"], synthetic_media=True)
        self.assertFalse(validation["ready"])
        self.assertIn("youtube synthetic media disclosure metadata is required", validation["errors"])

    def test_missing_platform_metadata_blocks_publish(self):
        plan = build_media_mission(
            target_platforms=["youtube"],
            accounts=[{"platform": "youtube", "needs_reconnect": False}],
            human_publish_approval=True,
            rights_status="verified",
            platform_metadata={"youtube": {"caption": "only caption"}},
        )
        by_id = {task["task_id"]: task for task in plan["tasks"]}
        self.assertEqual(by_id["metadata_youtube"]["state"], "BLOCKED")
        self.assertEqual(by_id["publish_youtube"]["state"], "METADATA_REQUIRED")

    def test_complete_package_rights_approval_and_connection_unlock_connector_only(self):
        plan = build_media_mission(
            target_platforms=["youtube"],
            accounts=[{"platform": "youtube", "needs_reconnect": False}],
            human_publish_approval=True,
            rights_status="verified",
            platform_metadata=platform_metadata("youtube"),
        )
        by_id = {task["task_id"]: task for task in plan["tasks"]}
        self.assertTrue(plan["connector_state"]["snapshot"]["fresh"])
        self.assertTrue(plan["rights_gate"]["ready"])
        self.assertTrue(plan["metadata_validation"]["youtube"]["ready"])
        self.assertEqual(by_id["publish_youtube"]["state"], "READY_FOR_CONNECTOR")
        self.assertFalse(plan["direct_publish_executed"])

    def test_plan_contains_closed_feedback_loop_and_safe_boundaries(self):
        plan = build_media_mission(
            target_platforms=["youtube"],
            accounts=[{"platform": "youtube", "needs_reconnect": False}],
            free_gpu_worker_available=True,
            connected_plugins=["descript"],
            rights_status="verified",
            platform_metadata=platform_metadata("youtube"),
        )
        by_id = {task["task_id"]: task for task in plan["tasks"]}
        roles = [task["owner_role"] for task in plan["tasks"]]
        for role in (
            "SOCIAL_INTELLIGENCE_AGENT", "CONTENT_STRATEGIST", "SCRIPT_AGENT", "GENERATIVE_MEDIA_AGENT",
            "TRANSCRIPTION_AGENT", "CLIP_EDITOR_AGENT", "CAPTION_LOCALIZATION_AGENT", "THUMBNAIL_CREATIVE_AGENT",
            "RIGHTS_SAFETY_AGENT", "PUBLISHING_AGENT", "ANALYTICS_AGENT", "MONETIZATION_AGENT",
        ):
            self.assertIn(role, roles)
        self.assertEqual(by_id["strategy_feedback"]["depends_on"], ["monetization"])
        self.assertEqual(by_id["strategy_feedback"]["owner_role"], "CONTENT_STRATEGIST")
        self.assertFalse(plan["hard_boundaries"]["generic_paid_fallback"])
        self.assertFalse(plan["hard_boundaries"]["auto_top_up"])
        self.assertFalse(plan["hard_boundaries"]["installed_plugin_implies_paid_execution_approval"])
        self.assertFalse(plan["production_active"])

    def test_config_json_is_valid(self):
        data = json.loads(Path(DEFAULT_CONFIG).read_text(encoding="utf-8"))
        self.assertIsInstance(data.get("pipeline"), list)
        self.assertGreaterEqual(len(data["pipeline"]), 12)


if __name__ == "__main__":
    unittest.main()
