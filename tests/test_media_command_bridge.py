import unittest

from scripts.media_command_bridge import build_integrated_media_mission


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


def base_evidence():
    return {
        "GOOGLE_GEMINI_FREE_MULTIMODAL": {
            "available": True,
            "free_verified": True,
            "quota_safe": True,
            "credential_present": True,
        },
        "FFMPEG_DETERMINISTIC": {"available": True},
    }


def ready_api_route():
    return {
        "available": True,
        "exact_model_verified": True,
        "free_verified": True,
        "quota_safe": True,
        "credential_present": True,
    }


def ready_gpu_route():
    return {
        "available": True,
        "exact_model_verified": True,
        "free_verified": True,
        "quota_safe": True,
        "runtime_present": True,
    }


class MediaCommandBridgeTests(unittest.TestCase):
    def test_image_generation_is_inserted_before_edit_and_review_before_rights(self):
        evidence = base_evidence()
        evidence["CLOUDFLARE_FLUX_FREE"] = ready_api_route()
        plan = build_integrated_media_mission(
            target_platforms=["youtube"],
            free_media_evidence=evidence,
            generation_asset_type="image",
            generative_media_required=True,
            free_gpu_worker_available=True,
            accounts=[{"platform": "youtube", "needs_reconnect": False}],
            rights_status="verified",
            platform_metadata=platform_metadata("youtube"),
        )
        by_id = {task["task_id"]: task for task in plan["tasks"]}
        self.assertEqual(plan["schema_version"], "media-agent-integrated-free-v1")
        self.assertEqual(plan["selected_routes"]["media_analysis"], "GOOGLE_GEMINI_FREE_MULTIMODAL")
        self.assertEqual(plan["selected_routes"]["generation"], "CLOUDFLARE_FLUX_FREE")
        self.assertEqual(plan["selected_routes"]["media_quality_review"], "GOOGLE_GEMINI_FREE_MULTIMODAL")
        self.assertIn("media_analysis", by_id["strategy"]["depends_on"])
        self.assertIn("generate_assets", by_id["edit"]["depends_on"])
        self.assertIn("media_quality_review", by_id["rights"]["depends_on"])
        self.assertEqual(by_id["generate_assets"]["state"], "READY")
        self.assertEqual(by_id["publish_youtube"]["state"], "APPROVAL_REQUIRED")
        self.assertTrue(plan["hard_boundaries"]["free_media_mesh_before_paid_generation"])
        self.assertFalse(plan["hard_boundaries"]["automatic_paid_generation_fallback"])
        self.assertFalse(plan["direct_publish_executed"])
        self.assertFalse(plan["production_active"])

    def test_video_falls_back_from_unverified_nvidia_to_verified_wan(self):
        evidence = base_evidence()
        evidence["NVIDIA_COSMOS_FREE"] = ready_api_route()
        evidence["NVIDIA_COSMOS_FREE"]["free_verified"] = False
        evidence["WAN_VIDEO_FREE_GPU"] = ready_gpu_route()
        plan = build_integrated_media_mission(
            target_platforms=["youtube"],
            free_media_evidence=evidence,
            generation_asset_type="video",
            generative_media_required=True,
            free_gpu_worker_available=True,
            accounts=[{"platform": "youtube", "needs_reconnect": False}],
            rights_status="verified",
            platform_metadata=platform_metadata("youtube"),
        )
        by_id = {task["task_id"]: task for task in plan["tasks"]}
        self.assertEqual(plan["selected_routes"]["generation"], "WAN_VIDEO_FREE_GPU")
        self.assertEqual(by_id["generate_assets"]["detail"], "WAN_VIDEO_FREE_GPU")
        self.assertEqual(by_id["generate_assets"]["state"], "READY")

    def test_paid_plugins_never_become_generation_fallback_when_free_mesh_is_unavailable(self):
        evidence = base_evidence()
        plan = build_integrated_media_mission(
            target_platforms=["youtube"],
            free_media_evidence=evidence,
            generation_asset_type="video",
            generative_media_required=True,
            free_gpu_worker_available=True,
            connected_plugins=["descript", "fal", "runway"],
            paid_media_approved=True,
            accounts=[{"platform": "youtube", "needs_reconnect": False}],
            rights_status="verified",
            platform_metadata=platform_metadata("youtube"),
        )
        by_id = {task["task_id"]: task for task in plan["tasks"]}
        self.assertIsNone(plan["selected_routes"]["generation"])
        self.assertEqual(by_id["generate_assets"]["state"], "BLOCKED")
        self.assertEqual(by_id["generate_assets"]["detail"], "NO_VERIFIED_FREE_GENERATION_ROUTE")
        self.assertNotIn("FAL_CONNECTOR_APPROVED", str(by_id["generate_assets"]))
        self.assertNotIn("RUNWAY_CONNECTOR_APPROVED", str(by_id["generate_assets"]))
        self.assertFalse(plan["hard_boundaries"]["automatic_paid_generation_fallback"])
        self.assertFalse(plan["direct_publish_executed"])

    def test_generation_not_required_keeps_free_mesh_out_of_path(self):
        plan = build_integrated_media_mission(
            target_platforms=["youtube"],
            generative_media_required=False,
            free_gpu_worker_available=True,
            accounts=[{"platform": "youtube", "needs_reconnect": False}],
            rights_status="verified",
            platform_metadata=platform_metadata("youtube"),
        )
        by_id = {task["task_id"]: task for task in plan["tasks"]}
        self.assertEqual(plan["free_media_mesh"]["status"], "NOT_REQUIRED")
        self.assertEqual(plan["selected_routes"]["generation"], "NOT_REQUIRED")
        self.assertEqual(plan["selected_routes"]["media_analysis"], "NOT_REQUIRED")
        self.assertEqual(plan["selected_routes"]["media_quality_review"], "NOT_REQUIRED")
        self.assertEqual(by_id["generate_assets"]["state"], "SKIPPED_NOT_REQUIRED")

    def test_publish_still_requires_rights_metadata_connection_and_human_approval(self):
        evidence = base_evidence()
        evidence["NVIDIA_COSMOS_FREE"] = ready_api_route()
        plan = build_integrated_media_mission(
            target_platforms=["youtube"],
            free_media_evidence=evidence,
            generation_asset_type="video",
            generative_media_required=True,
            free_gpu_worker_available=True,
            accounts=[{"platform": "youtube", "needs_reconnect": False}],
            human_publish_approval=True,
            rights_status="verified",
            synthetic_media=True,
            synthetic_disclosure_ready=True,
            platform_metadata=platform_metadata("youtube", synthetic=True),
        )
        by_id = {task["task_id"]: task for task in plan["tasks"]}
        self.assertEqual(by_id["publish_youtube"]["state"], "READY_FOR_CONNECTOR")
        self.assertFalse(plan["direct_publish_executed"])
        self.assertFalse(plan["production_active"])
        self.assertTrue(plan["hard_boundaries"]["publish_requires_human_approval"])


if __name__ == "__main__":
    unittest.main()
