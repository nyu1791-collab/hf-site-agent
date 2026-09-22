import json
import unittest
from pathlib import Path

from scripts.render_static_speaker_color_longform import (
    EMPHASIS_RED,
    EMPHASIS_YELLOW,
    emphasis_terms_for_line,
    rich_character_spans,
)
from scripts.synthesize_longform_voicevox import caption_text_for_line, deterministic_emphasis_terms


class VideoCaptionContractTests(unittest.TestCase):
    def test_summary_caption_never_replaces_full_voice_text(self):
        mission = {"caption_text_mode": "VOICE_TEXT_FULL"}
        line = {
            "id": "L01",
            "voice_text": "火星のJezeroで水が何度も動いたのだ。",
            "caption_text": "火星の水",
        }
        caption, source = caption_text_for_line(mission, line)
        self.assertEqual(caption, line["voice_text"])
        self.assertEqual(source, "VOICE_TEXT")

    def test_explicit_full_caption_cannot_omit_spoken_text(self):
        mission = {"caption_text_mode": "FULL_SPOKEN_TEXT"}
        line = {"id": "L02", "voice_text": "火星のJezeroで水が何度も動いたのだ。", "full_caption_text": "火星の水"}
        with self.assertRaises(SystemExit):
            caption_text_for_line(mission, line)

    def test_marked_terms_keep_emphasis_color_after_split(self):
        spans = rich_character_spans("重要語Jezero", ["Jezero"], (77, 224, 132, 255))
        colors = {color for _, color in spans}
        self.assertIn(EMPHASIS_YELLOW, colors)
        self.assertIn((77, 224, 132, 255), colors)

    def test_warning_term_uses_red_emphasis(self):
        terms = emphasis_terms_for_line({}, "これは生命の発見ではない")
        spans = rich_character_spans("これは生命の発見ではない", terms, (77, 224, 132, 255))
        self.assertIn(EMPHASIS_RED, {color for _, color in spans})

    def test_timing_manifest_persists_deterministic_emphasis_terms(self):
        terms = deterministic_emphasis_terms({}, "Jezeroの湖と地下水を調べた。")
        self.assertEqual(terms, ["Jezero", "地下水", "湖"])

    def test_mars_visual_registry_is_rights_provenanced(self):
        data = json.loads(Path("config/media_reusable_asset_standard.json").read_text(encoding="utf-8"))
        assets = {row["asset_id"]: row for row in data["assets"]}
        for asset_id in ("mars_jezero_crater_rim_panorama", "mars_perseverance_jezero_map"):
            asset = assets[asset_id]
            self.assertTrue(asset["source_page"].startswith("https://"))
            self.assertTrue(asset["download_url"].startswith("https://"))
            self.assertEqual(asset["rights_state"], "PD-USGov")
            self.assertEqual(asset["validation"], "JPEG_MAGIC")

    def test_mars_source_lock_carries_visual_provenance(self):
        lock = json.loads(Path("missions/media/20260922-mars-water-news60-v1.sources.json").read_text(encoding="utf-8"))
        required = ("source_page", "asset_locator", "rights_state", "claim_mapping", "retrieval_or_verification_timestamp")
        self.assertTrue(lock["visual_rights"]["visual_provenance_fields_complete"])
        self.assertGreaterEqual(len(lock["visuals"]), 2)
        for visual in lock["visuals"]:
            self.assertTrue(all(visual.get(field) for field in required))
            self.assertTrue(visual["semantic_match"])


if __name__ == "__main__":
    unittest.main()
