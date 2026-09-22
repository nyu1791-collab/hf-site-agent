import copy
import tempfile
import unittest
from pathlib import Path

from scripts.media_speed_orchestrator import plan_media_run


def _inputs(root: Path) -> dict:
    paths = {}
    for name, content in {
        "mission": "mission-v1",
        "sources": "claim-lock-v1",
        "timing": "timing-v1",
        "assets": "visual-assets-v1",
        "portraits": "portrait-v1",
    }.items():
        path = root / f"{name}.txt"
        path.write_text(content, encoding="utf-8")
        paths[name] = path
    return {
        "mission_or_script": paths["mission"],
        "source_claim_lock": paths["sources"],
        "voice_and_pronunciation": {"script": paths["mission"].read_text(), "engine": "VOICEVOX_LOCAL"},
        "measured_audio_timing": paths["timing"],
        "caption_and_font": {"timing": paths["timing"].read_text(), "contract": "FULL_SPOKEN_TEXT"},
        "rights_verified_visual_assets": paths["assets"],
        "character_shell_and_anchor": paths["portraits"],
        "renderer_font_policy_or_output_contract": {"renderer": "static-speaker-color-longform-v1", "font": "noto-cjk"},
    }


def _verified(plan: dict) -> dict:
    out = copy.deepcopy(plan)
    for row in out["stages"].values():
        if row["status"] == "PENDING":
            row["status"] = "VERIFIED"
    return out


class MediaSpeedOrchestratorTests(unittest.TestCase):
    def test_initial_plan_is_bounded_and_one_pass(self):
        with tempfile.TemporaryDirectory() as raw:
            plan = plan_media_run(_inputs(Path(raw)), use_jev=False)
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["final_encode"]["count"], 1)
        self.assertEqual(plan["final_encode"]["scene_video_intermediate_encodes"], 0)
        self.assertLessEqual(max(map(len, plan["parallel_waves"])), 3)
        self.assertEqual(plan["execution_profile"], "PARALLEL_PREP")

    def test_caption_change_reuses_voice_and_assets(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inputs = _inputs(root)
            first = _verified(plan_media_run(inputs, use_jev=False))
            timing = root / "timing.txt"
            timing.write_text("timing-v2", encoding="utf-8")
            second = plan_media_run(inputs, previous_plan=first, use_jev=False)
        self.assertIn("measured_audio_timing", second["changed_components"])
        self.assertEqual(second["stages"]["rights_verified_visual_assets"]["status"], "REUSED")
        self.assertEqual(second["stages"]["character_shell_and_toolchain_prep"]["status"], "REUSED")
        self.assertEqual(second["stages"]["voice_and_measured_timing"]["status"], "PENDING")
        self.assertEqual(second["stages"]["one_pass_final_encode"]["status"], "PENDING")
        self.assertTrue(second["cache"]["full_rerender_avoided"])

    def test_jev_typed_profile_is_admitted_but_python_keeps_final_plan(self):
        def fake_jev(**kwargs):
            self.assertEqual(kwargs["lane"], "VISION_AND_MEDIA_UNDERSTANDING")
            self.assertEqual(len(kwargs["candidate_models"]), 4)
            return {
                "status": "JEV_LEAN_DECISION_OK",
                "question_count": 2,
                "decision": {
                    "workers": ["PARALLEL_PREP"],
                    "route_shape": "PARALLEL_PAIR",
                    "action": "EXECUTE",
                    "confidence": 0.94,
                    "low_confidence": False,
                },
            }

        with tempfile.TemporaryDirectory() as raw:
            plan = plan_media_run(_inputs(Path(raw)), jev_decider=fake_jev)
        self.assertEqual(plan["execution_profile"], "PARALLEL_PREP")
        self.assertEqual(plan["profile_source"], "JEV_TYPED_PROFILE_THEN_PYTHON_ADMISSION")
        self.assertEqual(plan["jev"]["admission"], "ACCEPTED_TYPED_PROFILE")
        self.assertEqual(plan["final_encode"]["count"], 1)

    def test_jev_incompatible_profile_cannot_weaken_invalidation(self):
        def fake_jev(**kwargs):
            return {
                "status": "JEV_LEAN_DECISION_OK",
                "question_count": 2,
                "decision": {
                    "workers": ["CACHE_INCREMENTAL"],
                    "route_shape": "SINGLE",
                    "action": "EXECUTE",
                    "confidence": 0.99,
                    "low_confidence": False,
                },
            }

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inputs = _inputs(root)
            first = _verified(plan_media_run(inputs, use_jev=False))
            (root / "mission.txt").write_text("mission-v2", encoding="utf-8")
            plan = plan_media_run(inputs, previous_plan=first, jev_decider=fake_jev)
        self.assertEqual(plan["execution_profile"], "FULL_REBUILD")
        self.assertEqual(plan["jev"]["admission"], "REJECTED_BY_DETERMINISTIC_MEDIA_GUARD")
        self.assertIn("admission_and_script_lock", plan["stages_to_run"])

    def test_low_confidence_jev_falls_back_without_paid_or_extra_route(self):
        def fake_jev(**kwargs):
            return {
                "status": "JEV_LEAN_DECISION_OK",
                "question_count": 2,
                "decision": {
                    "workers": ["ESCALATE_TO_CHATGPT"],
                    "route_shape": "ESCALATE",
                    "action": "ESCALATE",
                    "confidence": 0.40,
                    "low_confidence": True,
                },
            }

        with tempfile.TemporaryDirectory() as raw:
            plan = plan_media_run(_inputs(Path(raw)), jev_decider=fake_jev)
        self.assertEqual(plan["execution_profile"], "PARALLEL_PREP")
        self.assertEqual(plan["jev"]["status"], "JEV_LEAN_DECISION_OK")
        self.assertEqual(plan["quality_gates"]["paid_or_freemium_media"], False)


if __name__ == "__main__":
    unittest.main()
