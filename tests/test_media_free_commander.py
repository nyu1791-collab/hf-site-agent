import unittest

from scripts.media_free_commander import (
    DEFAULT_CONFIG,
    build_free_media_plan,
    load_config,
    normalize_evidence,
    select_analysis_route,
    select_image_route,
    select_video_route,
)


def ready_route(*, credential=False, runtime=False):
    row = {
        "available": True,
        "exact_model_verified": True,
        "free_verified": True,
        "quota_safe": True,
    }
    if credential:
        row["credential_present"] = True
    if runtime:
        row["runtime_present"] = True
    return row


def common_evidence():
    return {
        "GOOGLE_GEMINI_FREE_MULTIMODAL": {
            "available": True,
            "free_verified": True,
            "quota_safe": True,
            "credential_present": True,
        },
        "FFMPEG_DETERMINISTIC": {"available": True},
    }


class FreeMediaCommanderTests(unittest.TestCase):
    def test_config_has_requested_media_division(self):
        config = load_config(DEFAULT_CONFIG)
        self.assertEqual(config["schema_version"], "free-media-mesh-v1")
        self.assertIn("GOOGLE_MEDIA_ANALYST", config["roles"])
        self.assertIn("CLOUDFLARE_FLUX_FREE", config["routes"])
        self.assertIn("SILICONFLOW_KOLORS_FREE", config["routes"])
        self.assertIn("QWEN_IMAGE_FREE_GPU", config["routes"])
        self.assertIn("DEEPSEEK_JANUS_FREE_GPU", config["routes"])
        self.assertIn("NVIDIA_COSMOS_FREE", config["routes"])
        self.assertIn("WAN_VIDEO_FREE_GPU", config["routes"])
        self.assertFalse(config["policy"]["generic_paid_fallback"])
        self.assertFalse(config["policy"]["auto_top_up"])
        self.assertFalse(config["roles"]["GOOGLE_MEDIA_ANALYST"]["generation_allowed"])

    def test_google_is_analysis_lane_not_generation_lane(self):
        config = load_config(DEFAULT_CONFIG)
        evidence = normalize_evidence(common_evidence())
        analysis = select_analysis_route(config, evidence)
        self.assertTrue(analysis["ready"])
        self.assertEqual(analysis["selected"], "GOOGLE_GEMINI_FREE_MULTIMODAL")
        self.assertNotIn("GOOGLE_GEMINI_FREE_MULTIMODAL", config["roles"]["IMAGE_GENERATION_LEAD"]["route_order"])
        self.assertNotIn("GOOGLE_GEMINI_FREE_MULTIMODAL", config["roles"]["VIDEO_GENERATION_LEAD"]["route_order"])

    def test_image_priority_cloudflare_then_siliconflow_then_qwen_then_deepseek(self):
        config = load_config(DEFAULT_CONFIG)
        evidence = common_evidence()
        evidence["CLOUDFLARE_FLUX_FREE"] = ready_route(credential=True)
        evidence["SILICONFLOW_KOLORS_FREE"] = ready_route(credential=True)
        evidence["QWEN_IMAGE_FREE_GPU"] = ready_route(runtime=True)
        evidence["DEEPSEEK_JANUS_FREE_GPU"] = ready_route(runtime=True)
        route = select_image_route(config, normalize_evidence(evidence))
        self.assertEqual(route["selected"], "CLOUDFLARE_FLUX_FREE")

        evidence["CLOUDFLARE_FLUX_FREE"]["quota_safe"] = False
        route = select_image_route(config, normalize_evidence(evidence))
        self.assertEqual(route["selected"], "SILICONFLOW_KOLORS_FREE")

        evidence["SILICONFLOW_KOLORS_FREE"]["free_verified"] = False
        route = select_image_route(config, normalize_evidence(evidence))
        self.assertEqual(route["selected"], "QWEN_IMAGE_FREE_GPU")

        evidence["QWEN_IMAGE_FREE_GPU"]["runtime_present"] = False
        route = select_image_route(config, normalize_evidence(evidence))
        self.assertEqual(route["selected"], "DEEPSEEK_JANUS_FREE_GPU")

    def test_video_priority_nvidia_then_wan(self):
        config = load_config(DEFAULT_CONFIG)
        evidence = common_evidence()
        evidence["NVIDIA_COSMOS_FREE"] = ready_route(credential=True)
        evidence["WAN_VIDEO_FREE_GPU"] = ready_route(runtime=True)
        route = select_video_route(config, normalize_evidence(evidence))
        self.assertEqual(route["selected"], "NVIDIA_COSMOS_FREE")

        evidence["NVIDIA_COSMOS_FREE"]["free_verified"] = False
        route = select_video_route(config, normalize_evidence(evidence))
        self.assertEqual(route["selected"], "WAN_VIDEO_FREE_GPU")

    def test_unverified_free_route_never_executes(self):
        evidence = common_evidence()
        evidence["CLOUDFLARE_FLUX_FREE"] = {
            "available": True,
            "exact_model_verified": True,
            "free_verified": False,
            "quota_safe": True,
            "credential_present": True,
        }
        plan = build_free_media_plan(asset_type="image", evidence=evidence)
        self.assertEqual(plan["status"], "BLOCKED_FREE_ROUTE_UNAVAILABLE")
        self.assertIsNone(plan["selected_routes"]["generation"])
        self.assertFalse(plan["hard_boundaries"]["unverified_route_can_execute"])

    def test_complete_image_plan_uses_free_routes_only(self):
        evidence = common_evidence()
        evidence["CLOUDFLARE_FLUX_FREE"] = ready_route(credential=True)
        plan = build_free_media_plan(asset_type="image", evidence=evidence)
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["selected_routes"]["analysis"], "GOOGLE_GEMINI_FREE_MULTIMODAL")
        self.assertEqual(plan["selected_routes"]["generation"], "CLOUDFLARE_FLUX_FREE")
        self.assertEqual(plan["selected_routes"]["post_process"], "FFMPEG_DETERMINISTIC")
        self.assertEqual(plan["selected_routes"]["quality_review"], "GOOGLE_GEMINI_FREE_MULTIMODAL")
        self.assertFalse(plan["hard_boundaries"]["generic_paid_fallback"])
        self.assertFalse(plan["hard_boundaries"]["auto_top_up"])
        self.assertFalse(plan["execution_contract"]["planner_spends_money"])
        self.assertFalse(plan["execution_contract"]["planner_publishes"])

    def test_complete_video_plan_uses_nvidia_and_can_fallback_to_wan(self):
        evidence = common_evidence()
        evidence["NVIDIA_COSMOS_FREE"] = ready_route(credential=True)
        evidence["WAN_VIDEO_FREE_GPU"] = ready_route(runtime=True)
        plan = build_free_media_plan(asset_type="video", evidence=evidence)
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["selected_routes"]["generation"], "NVIDIA_COSMOS_FREE")

        evidence["NVIDIA_COSMOS_FREE"]["quota_safe"] = False
        plan = build_free_media_plan(asset_type="video", evidence=evidence)
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["selected_routes"]["generation"], "WAN_VIDEO_FREE_GPU")


if __name__ == "__main__":
    unittest.main()
