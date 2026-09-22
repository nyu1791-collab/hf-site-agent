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
    cache_root = root / "cache"
    cache_root.mkdir()
    return {
        "mission_or_script": paths["mission"],
        "source_claim_lock": paths["sources"],
        "voice_and_pronunciation": {"script": paths["mission"].read_text(), "engine": "VOICEVOX_LOCAL"},
        "measured_audio_timing": paths["timing"],
        "caption_and_font": {"timing": paths["timing"].read_text(), "contract": "FULL_SPOKEN_TEXT"},
        "rights_verified_visual_assets": paths["assets"],
        "character_shell_and_anchor": paths["portraits"],
        "renderer_font_policy_or_output_contract": {"renderer": "static-speaker-color-longform-v1", "font": "noto-cjk"},
        "cache_root": cache_root,
    }


def _verified(plan: dict) -> dict:
    out = copy.deepcopy(plan)
    root = Path(out["input_manifest"]["cache_root"]["path"])
    for name, row in out["stages"].items():
        if row["status"] == "PENDING":
            row["status"] = "VERIFIED"
            proof = root / "proofs" / name
            proof.parent.mkdir(parents=True, exist_ok=True)
            proof.write_text(name, encoding="utf-8")
            row["artifact"] = {"path": str(proof.relative_to(root)), "sha256": __import__("hashlib").sha256(proof.read_bytes()).hexdigest()}
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
        self.assertEqual(plan["parallel_waves"][0], ["admission_and_script_lock"])
        self.assertEqual(set(sum(plan["parallel_waves"], [])), set(plan["stages_to_run"]))

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

    def test_cache_reuse_requires_artifact_proof_and_honors_unknown_invalidation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inputs = _inputs(root)
            first = _verified(plan_media_run(inputs, use_jev=False))
            inputs["cache_root"] = root / "different-cache"
            inputs["cache_root"].mkdir()
            second = plan_media_run(inputs, previous_plan=first, use_jev=False)
        self.assertIn("cache_root", second["changed_components"])
        self.assertEqual(second["stages_to_run"], list(second["stages"]))

    def test_high_risk_escalation_blocks_execution(self):
        def fake_jev(**kwargs):
            return {
                "status": "JEV_LEAN_DECISION_OK",
                "question_count": 2,
                "decision": {
                    "workers": ["ESCALATE_TO_CHATGPT"],
                    "route_shape": "ESCALATE",
                    "action": "ESCALATE",
                    "confidence": 0.99,
                    "low_confidence": False,
                },
            }

        with tempfile.TemporaryDirectory() as raw:
            plan = plan_media_run(_inputs(Path(raw)), jev_decider=fake_jev, high_risk=True)
        self.assertTrue(plan["execution_blocked"])
        self.assertEqual(plan["parallel_waves"], [])
        self.assertEqual(plan["stages_to_run"], [])
        self.assertEqual(plan["final_encode"]["count"], 0)

    def test_malformed_jev_result_cannot_be_admitted(self):
        def fake_jev(**kwargs):
            return {"status": "BROKEN", "question_count": 2, "decision": {"workers": ["PARALLEL_PREP"], "confidence": 0.99}}

        with tempfile.TemporaryDirectory() as raw:
            plan = plan_media_run(_inputs(Path(raw)), jev_decider=fake_jev)
        self.assertEqual(plan["profile_source"], "PYTHON_DETERMINISTIC_CONTROL_PLANE")
        self.assertEqual(plan["jev"]["admission"], "REJECTED_BY_DETERMINISTIC_MEDIA_GUARD")

    def test_jev_shape_is_applied_without_dropping_a_preparation_stage(self):
        def fake_jev(**kwargs):
            return {
                "status": "JEV_LEAN_DECISION_OK",
                "question_count": 2,
                "decision": {
                    "workers": ["PARALLEL_PREP"],
                    "route_shape": "PARALLEL_PAIR",
                    "action": "EXECUTE",
                    "confidence": 0.99,
                    "low_confidence": False,
                },
            }

        with tempfile.TemporaryDirectory() as raw:
            plan = plan_media_run(_inputs(Path(raw)), jev_decider=fake_jev)
        self.assertEqual(plan["parallelism"]["planned_parallel_lanes"], 2)
        self.assertEqual(plan["parallel_waves"][1], ["voice_and_measured_timing", "rights_verified_visual_assets"])
        self.assertEqual(plan["parallel_waves"][2], ["character_shell_and_toolchain_prep"])

    def test_policy_content_change_invalidates_verified_artifacts(self):
        with tempfile.TemporaryDirectory() as raw:
            inputs = _inputs(Path(raw))
            first = _verified(plan_media_run(inputs, use_jev=False))
            policy = __import__("copy").deepcopy(__import__("scripts.media_speed_orchestrator", fromlist=["load_policy"]).load_policy())
            policy["quality_first"]["paid_or_freemium_media_generation"] = True
            second = plan_media_run(inputs, previous_plan=first, policy=policy, use_jev=False)
        self.assertEqual(second["stages_to_run"], list(second["stages"]))


if __name__ == "__main__":
    unittest.main()
