import json
import tempfile
import unittest
from pathlib import Path

from scripts import video_creation_admission as admission


class VideoCreationAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_paths = admission.ROOT, admission.POLICY_PATH, admission.READ_GATE_PATH, admission.SPEED_POLICY_PATH
        admission.ROOT = self.root
        admission.POLICY_PATH = self.root / "config/video_creation_admission_policy.json"
        admission.READ_GATE_PATH = self.root / "config/media_command_read_gate.json"
        admission.SPEED_POLICY_PATH = self.root / "config/media_speed_quality_policy.json"

        self.read_set = [
            "README.md",
            "config/current_commander_handoff.json",
            "config/permanent_standards_manifest.json",
            "docs/AI_ARMY_MASTER_RULEBOOK.md",
            "AGENTS.md",
            "config/media_command_read_gate.json",
            "config/current_media_quality_handoff.json",
            "config/media_speed_quality_policy.json",
            "scripts/media_speed_orchestrator.py",
            "config/media_source_policy.json",
            "config/video_creation_admission_policy.json",
            "scripts/video_creation_admission.py",
        ]
        for path in self.read_set:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
        for path in ("scripts/render_reusable_short.py", "scripts/render_reusable_landscape.py"):
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()

        gate = {"speed_first_delivery_override": {"read_set": self.read_set}}
        speed = {
            "longform_renderer": "scripts/render_reusable_landscape.py",
            "speed_first_delivery": {
                "automatic_completion_check": [
                    "ENCODER_EXIT_SUCCESS",
                    "NONEMPTY_OUTPUT_FILE",
                    "AUDIO_AND_VIDEO_STREAMS_PRESENT",
                ]
            },
            "visual_density_contract": {
                "visual_beats_per_main_section": {"minimum": 2, "target_range": [2, 4]},
            },
            "fast_longform_delivery": {
                "wall_clock_target_minutes": 5,
                "applies_to_requested_longform_up_to_seconds": 960,
                "default_renderer": "scripts/render_reusable_landscape.py",
                "one_video_encode_only": True,
                "voice_segmenting": {"target_max_segments_for_16_minutes": 16, "avoid_sentence_level_synthesis_calls": True},
            },
            "script_clarity_contract": {
                "topic_headings": {
                    "use_clear_heading_for_each_main_topic": True,
                    "heading_names_the_topic_in_plain_words": True,
                },
                "narration": {
                    "state_main_point_before_details": True,
                    "prefer_common_words": True,
                    "keep_only_details_that_change_understanding_or_action": True,
                    "avoid_repeating_caveats_or_availability_notes": True,
                },
            },
        }
        policy = {
            "schema_version": "video-creation-admission-v1",
            "status": "ENFORCED_PERMANENT_STANDARD",
            "required_read_set_ref": "config/media_command_read_gate.json#/speed_first_delivery_override/read_set",
            "voice_contract": {
                "engine": "VOICEVOX_LOCAL",
                "primary_voice": "ずんだもん",
                "standard_cast": ["ずんだもん", "四国めたん"],
                "voicevox_unavailable_action": "BLOCK_BEFORE_RENDER",
                "silent_video_fallback": False,
            },
            "free_execution_contract": {"paid_or_freemium_tts": False, "paid_media_substitution": False},
            "caption_contract": {
                "full_spoken_text_required": True,
                "summary_caption_may_not_replace_narration": True,
                "caption_contract_name": "FULL_SPOKEN_TEXT",
                "renderers": {
                    "shortform": "scripts/render_reusable_short.py",
                    "longform": "scripts/render_reusable_landscape.py",
                },
            },
            "visual_asset_contract": {
                "claim_bearing_news_requires_related_visual_plan": True,
                "source_page_required": True,
                "asset_locator_required": True,
                "license_or_public_domain_state_required": True,
                "scene_or_claim_mapping_required": True,
                "semantic_match_required": True,
                "visual_density_policy": "config/media_speed_quality_policy.json#/visual_density_contract",
                "distinct_relevant_visuals_per_main_section_minimum": 2,
                "distinct_relevant_visuals_per_main_section_target_maximum": 4,
                "official_primary_visuals_preferred": True,
                "image_generation_allowed": False,
                "screenshots_must_exclude_browser_and_player_ui": True,
                "official_source_alone_does_not_clear_reuse_rights": True,
                "provenance_scope": "EXTERNAL_OR_REUSED_VISUAL_ASSETS_ONLY",
                "original_simple_visuals_may_use_creator_provenance": True,
                "unknown_rights_action": "BLOCK_BEFORE_RENDER",
            },
            "artifact_contract": {
                "routine_output_checks_ref": "config/media_speed_quality_policy.json#/speed_first_delivery/automatic_completion_check"
            },
            "character_output_contract": {
                "applies_only_when_character_led_profile_is_selected_or_user_requests_it": True
            },
        }
        for path, data in (
            (admission.READ_GATE_PATH, gate),
            (admission.SPEED_POLICY_PATH, speed),
            (admission.POLICY_PATH, policy),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), encoding="utf-8")
        (self.root / "config/media_source_policy.json").write_text(json.dumps({
            "generated_images_enabled_for_video": False,
            "factual_video_visual_rules": {"prefer_official_primary_source_visuals": True, "capture_only_media_region_no_browser_or_player_chrome": True},
        }), encoding="utf-8")

    def tearDown(self):
        admission.ROOT, admission.POLICY_PATH, admission.READ_GATE_PATH, admission.SPEED_POLICY_PATH = self.old_paths
        self.temp.cleanup()

    def test_ordinary_route_uses_only_canonical_read_set_and_speed_checks(self):
        report = admission.static_admission()
        self.assertEqual(report["status"], "PASS", report["failures"])
        self.assertEqual(report["required_read_set"], self.read_set)
        self.assertEqual(len(report["routine_output_checks"]), 3)
        self.assertEqual(report["caption_renderers"]["longform"], "scripts/render_reusable_landscape.py")


    def test_visual_density_is_pinned_to_speed_policy(self):
        policy = json.loads(admission.POLICY_PATH.read_text())
        policy["visual_asset_contract"]["distinct_relevant_visuals_per_main_section_minimum"] = 1
        admission.POLICY_PATH.write_text(json.dumps(policy), encoding="utf-8")
        report = admission.static_admission()
        self.assertEqual(report["status"], "BLOCKED")
        self.assertTrue(any("at least two distinct relevant visuals" in failure for failure in report["failures"]))

    def test_renderer_must_be_declared_and_present_for_each_profile(self):
        policy = json.loads(admission.POLICY_PATH.read_text())
        policy["caption_contract"]["renderers"]["longform"] = "scripts/not_a_renderer.py"
        admission.POLICY_PATH.write_text(json.dumps(policy), encoding="utf-8")
        report = admission.static_admission()
        self.assertEqual(report["status"], "BLOCKED")
        self.assertTrue(any("longform profile" in failure for failure in report["failures"]))


    def test_generated_video_visuals_are_blocked(self):
        policy = json.loads(admission.POLICY_PATH.read_text())
        policy["visual_asset_contract"]["image_generation_allowed"] = True
        admission.POLICY_PATH.write_text(json.dumps(policy), encoding="utf-8")
        report = admission.static_admission()
        self.assertEqual(report["status"], "BLOCKED")
        self.assertTrue(any("image generation" in failure for failure in report["failures"]))

    def test_fast_longform_contract_is_enforced(self):
        speed = json.loads(admission.SPEED_POLICY_PATH.read_text())
        speed["fast_longform_delivery"]["wall_clock_target_minutes"] = 16
        admission.SPEED_POLICY_PATH.write_text(json.dumps(speed), encoding="utf-8")
        report = admission.static_admission()
        self.assertEqual(report["status"], "BLOCKED")
        self.assertTrue(any("five-minute work target" in failure for failure in report["failures"]))


if __name__ == "__main__":
    unittest.main()
