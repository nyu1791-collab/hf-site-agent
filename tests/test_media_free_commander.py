import unittest

from scripts.media_free_commander import (
    DEFAULT_CONFIG,
    build_free_media_plan,
    load_config,
    normalize_evidence,
    route_readiness,
    select_analysis_route,
    select_image_route,
    select_video_route,
)


NOW = 1_900_000_000.0
CONFIG = load_config(DEFAULT_CONFIG)


def evidence_row(route_id: str, *, credential=False, runtime=False, observed_at=NOW):
    route = CONFIG["routes"][route_id]
    row = {
        "evidence_schema_version": CONFIG["evidence_contract"]["schema_version"],
        "source": route["evidence_source"],
        "route_id": route_id,
        "provider": route["provider"],
        "observed_at_epoch": observed_at,
        "available": True,
        "exact_model_verified": True,
        "free_verified": True,
        "quota_safe": True,
    }
    if route.get("model_family"):
        row["model_family"] = route["model_family"]
    if credential:
        row["credential_present"] = True
    if runtime:
        row["runtime_present"] = True
    return row


def common_evidence():
    return {
        "GOOGLE_GEMINI_FREE_MULTIMODAL": evidence_row("GOOGLE_GEMINI_FREE_MULTIMODAL", credential=True),
        "FFMPEG_DETERMINISTIC": evidence_row("FFMPEG_DETERMINISTIC"),
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
        self.assertTrue(config["policy"]["fresh_provenance_bound_evidence_required"])
        self.assertFalse(config["evidence_contract"]["cryptographic_authentication_provided"])
        self.assertTrue(config["evidence_contract"]["trusted_internal_producer_required"])
        self.assertFalse(config["roles"]["GOOGLE_MEDIA_ANALYST"]["generation_allowed"])

    def test_google_is_analysis_lane_not_generation_lane(self):
        config = load_config(DEFAULT_CONFIG)
        evidence = normalize_evidence(common_evidence(), config=config, now_epoch_seconds=NOW)
        analysis = select_analysis_route(config, evidence)
        self.assertTrue(analysis["ready"])
        self.assertEqual(analysis["selected"], "GOOGLE_GEMINI_FREE_MULTIMODAL")
        self.assertNotIn("GOOGLE_GEMINI_FREE_MULTIMODAL", config["roles"]["IMAGE_GENERATION_LEAD"]["route_order"])
        self.assertNotIn("GOOGLE_GEMINI_FREE_MULTIMODAL", config["roles"]["VIDEO_GENERATION_LEAD"]["route_order"])

    def test_image_priority_cloudflare_then_siliconflow_then_qwen_then_deepseek(self):
        config = load_config(DEFAULT_CONFIG)
        evidence = common_evidence()
        evidence["CLOUDFLARE_FLUX_FREE"] = evidence_row("CLOUDFLARE_FLUX_FREE", credential=True)
        evidence["SILICONFLOW_KOLORS_FREE"] = evidence_row("SILICONFLOW_KOLORS_FREE", credential=True)
        evidence["QWEN_IMAGE_FREE_GPU"] = evidence_row("QWEN_IMAGE_FREE_GPU", runtime=True)
        evidence["DEEPSEEK_JANUS_FREE_GPU"] = evidence_row("DEEPSEEK_JANUS_FREE_GPU", runtime=True)
        route = select_image_route(config, normalize_evidence(evidence, config=config, now_epoch_seconds=NOW))
        self.assertEqual(route["selected"], "CLOUDFLARE_FLUX_FREE")

        evidence["CLOUDFLARE_FLUX_FREE"]["quota_safe"] = False
        route = select_image_route(config, normalize_evidence(evidence, config=config, now_epoch_seconds=NOW))
        self.assertEqual(route["selected"], "SILICONFLOW_KOLORS_FREE")

        evidence["SILICONFLOW_KOLORS_FREE"]["free_verified"] = False
        route = select_image_route(config, normalize_evidence(evidence, config=config, now_epoch_seconds=NOW))
        self.assertEqual(route["selected"], "QWEN_IMAGE_FREE_GPU")

        evidence["QWEN_IMAGE_FREE_GPU"]["runtime_present"] = False
        route = select_image_route(config, normalize_evidence(evidence, config=config, now_epoch_seconds=NOW))
        self.assertEqual(route["selected"], "DEEPSEEK_JANUS_FREE_GPU")

    def test_video_priority_nvidia_then_wan(self):
        config = load_config(DEFAULT_CONFIG)
        evidence = common_evidence()
        evidence["NVIDIA_COSMOS_FREE"] = evidence_row("NVIDIA_COSMOS_FREE", credential=True)
        evidence["WAN_VIDEO_FREE_GPU"] = evidence_row("WAN_VIDEO_FREE_GPU", runtime=True)
        route = select_video_route(config, normalize_evidence(evidence, config=config, now_epoch_seconds=NOW))
        self.assertEqual(route["selected"], "NVIDIA_COSMOS_FREE")

        evidence["NVIDIA_COSMOS_FREE"]["free_verified"] = False
        route = select_video_route(config, normalize_evidence(evidence, config=config, now_epoch_seconds=NOW))
        self.assertEqual(route["selected"], "WAN_VIDEO_FREE_GPU")

    def test_bare_boolean_evidence_never_executes(self):
        raw = {
            "CLOUDFLARE_FLUX_FREE": {
                "available": True,
                "exact_model_verified": True,
                "free_verified": True,
                "quota_safe": True,
                "credential_present": True,
            }
        }
        normalized = normalize_evidence(raw, config=CONFIG, now_epoch_seconds=NOW)
        readiness = route_readiness(CONFIG, "CLOUDFLARE_FLUX_FREE", normalized)
        self.assertFalse(readiness["ready"])
        self.assertIn("evidence_contract", readiness["missing"])
        self.assertIn("fresh_observation", readiness["evidence_failures"])
        self.assertIn("trusted_source", readiness["evidence_failures"])

    def test_stale_evidence_fails_closed(self):
        raw = {"CLOUDFLARE_FLUX_FREE": evidence_row("CLOUDFLARE_FLUX_FREE", credential=True, observed_at=NOW - 3601)}
        normalized = normalize_evidence(raw, config=CONFIG, now_epoch_seconds=NOW)
        readiness = route_readiness(CONFIG, "CLOUDFLARE_FLUX_FREE", normalized)
        self.assertFalse(readiness["ready"])
        self.assertFalse(readiness["evidence_fresh"])
        self.assertIn("fresh_observation", readiness["evidence_failures"])

    def test_provider_model_and_source_binding_fail_closed(self):
        for field, value, expected_failure in (
            ("provider", "wrong-provider", "provider_binding"),
            ("model_family", "wrong-model", "model_family_binding"),
            ("source", "UNTRUSTED_SOURCE", "trusted_source"),
            ("route_id", "OTHER_ROUTE", "route_binding"),
        ):
            with self.subTest(field=field):
                row = evidence_row("CLOUDFLARE_FLUX_FREE", credential=True)
                row[field] = value
                normalized = normalize_evidence({"CLOUDFLARE_FLUX_FREE": row}, config=CONFIG, now_epoch_seconds=NOW)
                readiness = route_readiness(CONFIG, "CLOUDFLARE_FLUX_FREE", normalized)
                self.assertFalse(readiness["ready"])
                self.assertIn(expected_failure, readiness["evidence_failures"])

    def test_unverified_free_route_never_executes(self):
        evidence = common_evidence()
        evidence["CLOUDFLARE_FLUX_FREE"] = evidence_row("CLOUDFLARE_FLUX_FREE", credential=True)
        evidence["CLOUDFLARE_FLUX_FREE"]["free_verified"] = False
        plan = build_free_media_plan(asset_type="image", evidence=evidence, now_epoch_seconds=NOW)
        self.assertEqual(plan["status"], "BLOCKED_FREE_ROUTE_UNAVAILABLE")
        self.assertIsNone(plan["selected_routes"]["generation"])
        self.assertFalse(plan["hard_boundaries"]["unverified_route_can_execute"])

    def test_complete_image_plan_uses_free_routes_only(self):
        evidence = common_evidence()
        evidence["CLOUDFLARE_FLUX_FREE"] = evidence_row("CLOUDFLARE_FLUX_FREE", credential=True)
        plan = build_free_media_plan(asset_type="image", evidence=evidence, now_epoch_seconds=NOW)
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["selected_routes"]["analysis"], "GOOGLE_GEMINI_FREE_MULTIMODAL")
        self.assertEqual(plan["selected_routes"]["generation"], "CLOUDFLARE_FLUX_FREE")
        self.assertEqual(plan["selected_routes"]["post_process"], "FFMPEG_DETERMINISTIC")
        self.assertEqual(plan["selected_routes"]["quality_review"], "GOOGLE_GEMINI_FREE_MULTIMODAL")
        self.assertFalse(plan["hard_boundaries"]["stale_or_unbound_evidence_can_execute"])
        self.assertFalse(plan["hard_boundaries"]["generic_paid_fallback"])
        self.assertFalse(plan["hard_boundaries"]["auto_top_up"])
        self.assertFalse(plan["execution_contract"]["planner_spends_money"])
        self.assertFalse(plan["execution_contract"]["planner_publishes"])
        self.assertTrue(plan["execution_contract"]["provenance_binding_required"])
        self.assertTrue(plan["execution_contract"]["trusted_internal_producer_required"])
        self.assertFalse(plan["execution_contract"]["cryptographic_evidence_authentication"])

    def test_complete_video_plan_uses_nvidia_and_can_fallback_to_wan(self):
        evidence = common_evidence()
        evidence["NVIDIA_COSMOS_FREE"] = evidence_row("NVIDIA_COSMOS_FREE", credential=True)
        evidence["WAN_VIDEO_FREE_GPU"] = evidence_row("WAN_VIDEO_FREE_GPU", runtime=True)
        plan = build_free_media_plan(asset_type="video", evidence=evidence, now_epoch_seconds=NOW)
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["selected_routes"]["generation"], "NVIDIA_COSMOS_FREE")

        evidence["NVIDIA_COSMOS_FREE"]["quota_safe"] = False
        plan = build_free_media_plan(asset_type="video", evidence=evidence, now_epoch_seconds=NOW)
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["selected_routes"]["generation"], "WAN_VIDEO_FREE_GPU")


if __name__ == "__main__":
    unittest.main()
