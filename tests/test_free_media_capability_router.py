import unittest

from scripts.free_media_capability_router import build_capability_plan, load_config


def ready(**extra):
    row = {
        "available": True,
        "free_verified": True,
        "quota_safe": True,
        "runtime_present": True,
        "service_terms_ok": True,
        "commercial_license_verified": True,
    }
    row.update(extra)
    return row


class FreeMediaCapabilityRouterTests(unittest.TestCase):
    def test_multi_role_reuse_is_allowed_for_capable_free_model(self):
        evidence = {
            "QWEN_TEXT_FREE_RUNTIME": ready(),
            "WHISPER_LARGE_V3_TURBO_FREE_RUNTIME": ready(),
            "KOKORO_82M_FREE_RUNTIME": ready(),
            "FFMPEG_DETERMINISTIC": ready(),
        }
        plan = build_capability_plan(
            evidence,
            work_types=["SCRIPT_DRAFT", "SUBTITLE_DRAFT", "TTS_JA", "SUBTITLE_ALIGNMENT", "AUDIO_QA", "DETERMINISTIC_EDIT"],
        )
        self.assertEqual(plan["assignments"]["SCRIPT_DRAFT"]["candidate_id"], "QWEN_TEXT_FREE_RUNTIME")
        self.assertEqual(plan["assignments"]["SUBTITLE_DRAFT"]["candidate_id"], "QWEN_TEXT_FREE_RUNTIME")
        self.assertEqual(plan["assignments"]["SUBTITLE_ALIGNMENT"]["candidate_id"], "WHISPER_LARGE_V3_TURBO_FREE_RUNTIME")
        self.assertEqual(plan["assignments"]["AUDIO_QA"]["candidate_id"], "WHISPER_LARGE_V3_TURBO_FREE_RUNTIME")
        self.assertGreaterEqual(plan["multi_role_reuse"]["QWEN_TEXT_FREE_RUNTIME"], 2)
        self.assertGreaterEqual(plan["multi_role_reuse"]["WHISPER_LARGE_V3_TURBO_FREE_RUNTIME"], 2)

    def test_independent_fact_check_prefers_different_provider_when_available(self):
        cfg = load_config()
        # Add fact-check capability to Google in this test so the router has a
        # genuine independent alternative to a Qwen producer.
        cfg["candidates"]["GOOGLE_GEMINI_FREE_MULTIMODAL"]["capabilities"] += [
            "citation_review", "claim_verification", "counterargument", "reasoning"
        ]
        cfg["candidates"]["GOOGLE_GEMINI_FREE_MULTIMODAL"]["work_types"] += ["FACT_CHECK"]
        evidence = {
            "QWEN_TEXT_FREE_RUNTIME": ready(),
            "GOOGLE_GEMINI_FREE_MULTIMODAL": ready(),
        }
        plan = build_capability_plan(evidence, work_types=["SCRIPT_DRAFT", "FACT_CHECK"], config=cfg)
        self.assertEqual(plan["assignments"]["SCRIPT_DRAFT"]["candidate_id"], "QWEN_TEXT_FREE_RUNTIME")
        self.assertEqual(plan["assignments"]["FACT_CHECK"]["provider"], "google")

    def test_unverified_free_route_never_executes(self):
        evidence = {
            "COSYVOICE2_0_5B_FREE_RUNTIME": {
                "available": True,
                "free_verified": False,
                "runtime_present": True,
            }
        }
        plan = build_capability_plan(evidence, work_types=["TTS_JA"])
        self.assertEqual(plan["status"], "BLOCKED_FREE_ROUTE_UNAVAILABLE")
        self.assertEqual(plan["assignments"]["TTS_JA"]["status"], "BLOCKED")

    def test_license_unknown_sensevoice_is_blocked(self):
        evidence = {
            "SENSEVOICE_SMALL_FREE_RUNTIME": ready(commercial_license_verified=False),
        }
        plan = build_capability_plan(evidence, work_types=["SUBTITLE_ALIGNMENT"])
        attempt = next(row for row in plan["evaluations"]["SUBTITLE_ALIGNMENT"] if row["candidate_id"] == "SENSEVOICE_SMALL_FREE_RUNTIME")
        self.assertFalse(attempt["ready"])
        self.assertIn("commercial_license_verified", attempt["failures"])

    def test_paid_tools_remain_disabled(self):
        plan = build_capability_plan({}, work_types=["TTS_JA"])
        boundaries = plan["hard_boundaries"]
        self.assertIn("fal", boundaries["paid_media_tools_disabled"])
        self.assertIn("descript_ai", boundaries["paid_media_tools_disabled"])
        self.assertFalse(boundaries["generic_paid_fallback"])
        self.assertFalse(boundaries["auto_top_up"])


if __name__ == "__main__":
    unittest.main()
