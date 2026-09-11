from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.asset_rights_ledger import AssetRightsError, load_ledger, publish_rights_gate, register_asset
from scripts.ffmpeg_renderer import build_render_plan, render
from scripts.media_pipeline import PIPELINE, build_media_gate, export_voice_handoff, validate_stage_order


class MediaPipelineSafetyTests(unittest.TestCase):
    def good_asset(self):
        return {
            "asset_id": "commons-001",
            "source_url": "https://commons.wikimedia.org/wiki/File:Example.jpg",
            "author": "Example Author",
            "license": "CC-BY-4.0",
            "attribution_required": True,
            "commercial_use_ok": True,
            "rights_verified": True,
            "verified_at": "2026-09-11T12:00:00+00:00",
        }

    def test_pipeline_orders_fact_check_rights_qa_and_human_publish(self):
        self.assertTrue(validate_stage_order(PIPELINE))
        self.assertLess(PIPELINE.index("SCRIPT_DRAFT"), PIPELINE.index("INDEPENDENT_FACT_CHECK"))
        self.assertLess(PIPELINE.index("FFMPEG_EDIT"), PIPELINE.index("INDEPENDENT_MULTIMODAL_QA"))
        self.assertEqual(PIPELINE[-1], "HUMAN_PUBLISH_APPROVAL")

    def test_unknown_photo_license_is_blocked(self):
        ledger = load_ledger()
        bad = self.good_asset()
        bad["license"] = "UNKNOWN"
        with self.assertRaises(AssetRightsError):
            register_asset(ledger, bad)

    def test_duplicate_asset_reuses_existing_ledger_entry(self):
        ledger = register_asset(load_ledger(), self.good_asset())
        again = register_asset(ledger, self.good_asset())
        self.assertEqual(len(again["assets"]), 1)
        self.assertTrue(again["last_registration"]["reused"])

    def test_validator_failure_blocks_publish_approval(self):
        ledger = register_asset(load_ledger(), self.good_asset())
        gate = publish_rights_gate(
            ledger,
            asset_ids=["commons-001"],
            deterministic_validator_passed=False,
            independent_media_qa_passed=True,
        )
        self.assertFalse(gate["rights_gate_passed"])
        self.assertIn("validator_failed", gate["failures"])
        self.assertFalse(gate["publish_executed"])

    def test_same_producer_and_final_qa_is_not_ready_for_publish_approval(self):
        ledger = register_asset(load_ledger(), self.good_asset())
        identity = {"provider": "GROQ", "model_family": "QWEN", "exact_model": "qwen-x"}
        gate = build_media_gate(
            ledger=ledger,
            asset_ids=["commons-001"],
            deterministic_validator_passed=True,
            producer=identity,
            final_qa=identity,
            final_qa_passed=True,
        )
        self.assertFalse(gate["producer_reviewer_diverse"])
        self.assertFalse(gate["ready_for_human_publish_approval"])
        self.assertFalse(gate["publish_executed"])

    def test_voice_handoff_exports_four_required_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = export_voice_handoff(
                output_dir=tmp,
                voice_script="これはテストです。",
                cues=[{"start_sec": 0, "end_sec": 1.5, "text": "これはテストです。", "delivery": "clear"}],
                subtitles=[{"start_sec": 0, "end_sec": 1.5, "text": "これはテストです。"}],
                scenes=[{"scene_id": "s1", "asset_id": "commons-001", "start_sec": 0, "end_sec": 1.5}],
            )
            self.assertEqual(set(paths), {"voice_script", "voice_cues", "subtitle", "scene_timeline"})
            for value in paths.values():
                self.assertTrue(Path(value).exists())
            timeline = json.loads(Path(paths["scene_timeline"]).read_text(encoding="utf-8"))
            self.assertFalse(timeline["generated_images_used"])
            self.assertFalse(timeline["generated_video_used"])
            self.assertFalse(timeline["publish_authority"])

    def test_ffmpeg_plan_is_local_dry_run_and_has_no_publish_authority(self):
        plan = build_render_plan(
            images=["asset1.jpg", "asset2.jpg"],
            audio_path="voice.wav",
            subtitle_path="subtitle.srt",
            output_path="short.mp4",
        )
        self.assertFalse(plan["network_required"])
        self.assertFalse(plan["generated_video_ai_used"])
        self.assertFalse(plan["publish_authority"])
        result = render(plan, execute=False)
        self.assertEqual(result["status"], "DRY_RUN")
        self.assertFalse(result["publish_executed"])


if __name__ == "__main__":
    unittest.main()
