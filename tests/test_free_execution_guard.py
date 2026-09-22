import json
import unittest
from pathlib import Path

from scripts.media_agent_runtime import build_connector_state, select_generation_route
from scripts.video_creation_admission import static_admission
from scripts.validate_free_execution_guard import main as validate_guard


ROOT = Path(__file__).resolve().parents[1]


class FreeExecutionGuardTests(unittest.TestCase):
    def test_repository_guard_is_enforced_and_cross_tab_durable(self):
        self.assertEqual(validate_guard(), 0)

    def test_unknown_or_paid_media_route_is_blocked_even_with_flag(self):
        state = build_connector_state(connected_plugins=["runway", "fal"], paid_media_approved=True)
        self.assertEqual(select_generation_route(state), "BLOCKED_NEEDS_APPROVED_MEDIA_GENERATION_ROUTE")
        self.assertFalse(state["creative_generation"]["runway"])
        self.assertFalse(state["creative_generation"]["fal"])

    def test_guard_explicitly_blocks_trial_credit_as_free(self):
        policy = json.loads((ROOT / "config/free_execution_guard.json").read_text(encoding="utf-8"))
        self.assertFalse(policy["default_runtime"]["trial_credit_or_freemium_route_counts_as_free"])
        self.assertEqual(policy["default_runtime"]["unknown_cost_route"], "BLOCK")

    def test_video_creation_admission_requires_zundamon_and_no_silent_fallback(self):
        report = static_admission()
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["primary_voice"], "ずんだもん")
        self.assertEqual(report["voicevox_unavailable_action"], "BLOCK_BEFORE_RENDER")
        self.assertEqual(report["paid_or_freemium_tts"], False)

    def test_video_builder_cannot_bypass_runtime_admission(self):
        source = (ROOT / "scripts/build_free_news_video.py").read_text(encoding="utf-8")
        self.assertIn("require_runtime_admission", source)
        self.assertIn("_synthesize_voicevox", source)
        self.assertIn("VOICEVOX:ずんだもん", source)


if __name__ == "__main__":
    unittest.main()
