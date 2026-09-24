import json
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from scripts.render_static_speaker_color_longform import (
    EMPHASIS_RED,
    EMPHASIS_YELLOW,
    emphasis_terms_for_line,
    fit_rich_caption,
    fit_attribution,
    fit_single_line,
    get_font,
    rich_character_spans,
    wrap_rich,
    ZUNDAMON_ACCENT,
    rendered_visual_evidence,
)
from scripts.synthesize_longform_voicevox import caption_text_for_line, deterministic_emphasis_terms
from scripts.validate_video_caption_contract import validate_shortform_emphasis


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

    def test_wrap_rich_keeps_latin_words_and_reviewed_phrases_indivisible(self):
        draw = ImageDraw.Draw(Image.new("RGBA", (640, 240)))
        font = get_font(24)
        caption = "火星・JezeroクレーターのMargin Unit"
        lines = wrap_rich(draw, caption, font, int(draw.textlength("Jezero", font)) + 2, ["Jezero", "Margin Unit"], ZUNDAMON_ACCENT)
        rendered = ["".join(value for value, _ in line) for line in lines]
        self.assertTrue(any("Jezero" in line for line in rendered), rendered)
        self.assertTrue(any("Margin Unit" in line for line in rendered), rendered)
        self.assertEqual("".join(rendered).replace(" ", ""), caption.replace(" ", ""))
        self.assertTrue(all(not line.startswith(" ") and not line.endswith(" ") for line in rendered))

    def test_fit_rich_caption_shrinks_before_splitting_an_indivisible_term(self):
        draw = ImageDraw.Draw(Image.new("RGBA", (640, 240)))
        min_font = get_font(30)
        max_width = int(draw.textlength("Margin Unit", min_font)) + 2
        font, lines, _ = fit_rich_caption(draw, "Margin Unit", ["Margin Unit"], ZUNDAMON_ACCENT, max_width, 180, start_size=54, min_size=30)
        rendered = ["".join(value for value, _ in line) for line in lines]
        self.assertEqual(rendered, ["Margin Unit"])
        self.assertLess(font.size, 54)
        self.assertLessEqual(draw.textlength(rendered[0], font), max_width)

    def test_fit_rich_caption_fails_closed_if_an_indivisible_term_cannot_fit(self):
        draw = ImageDraw.Draw(Image.new("RGBA", (640, 240)))
        with self.assertRaises(RuntimeError):
            fit_rich_caption(draw, "Jezero", ["Jezero"], ZUNDAMON_ACCENT, 5, 180, start_size=30, min_size=30)

    def test_source_attribution_is_complete_and_fits_reserved_footer(self):
        draw = ImageDraw.Draw(Image.new("RGBA", (1080, 1920)))
        credit = "Image credit: NASA/JPL-Caltech/University of Arizona via Wikimedia Commons · PD-USGov · context image"
        font, width = fit_single_line(draw, credit, 880, start_size=20, min_size=14)
        self.assertEqual(credit, "Image credit: NASA/JPL-Caltech/University of Arizona via Wikimedia Commons · PD-USGov · context image")
        self.assertLessEqual(width, 880)
        self.assertLessEqual(draw.textbbox((0, 0), credit, font=font)[2], 880)
        self.assertGreaterEqual(font.size, 14)

    def test_source_attribution_fails_closed_instead_of_truncating(self):
        draw = ImageDraw.Draw(Image.new("RGBA", (100, 80)))
        with self.assertRaises(RuntimeError):
            fit_single_line(draw, "NASA/JPL-Caltech via Wikimedia Commons", 5, start_size=14, min_size=14)

    def test_long_source_credit_wraps_between_attribution_and_rights_metadata(self):
        draw = ImageDraw.Draw(Image.new("RGBA", (1080, 1920)))
        source = "NASA/JPL-Caltech/University of Arizona via Wikimedia Commons / public domain"
        credit = f"Image credit: {source} · PD-USGov · context image"
        font, lines, widths = fit_attribution(draw, credit, 880)
        self.assertEqual(len(lines), 2, lines)
        self.assertEqual(" · ".join(lines), credit)
        self.assertTrue(all(width <= 880 for width in widths), widths)
        self.assertGreaterEqual(font.size, 14)
        self.assertIn("University of Arizona", lines[0])
        self.assertIn("PD-USGov · context image", lines[1])

    def test_source_attribution_fails_closed_when_two_lines_cannot_fit(self):
        draw = ImageDraw.Draw(Image.new("RGBA", (100, 80)))
        with self.assertRaises(RuntimeError):
            fit_attribution(draw, "Image credit: NASA/JPL-Caltech · PD-USGov", 5, start_size=14, min_size=14)

    def test_marked_terms_keep_emphasis_color_after_split(self):
        spans = rich_character_spans("重要語Jezero", ["Jezero"], (77, 224, 132, 255))
        colors = {color for _, color in spans}
        self.assertIn(EMPHASIS_YELLOW, colors)
        self.assertIn((77, 224, 132, 255), colors)

    def test_warning_term_uses_red_emphasis(self):
        terms = emphasis_terms_for_line({"emphasis_terms": ["ではない"]}, "これは生命の発見ではない")
        spans = rich_character_spans("これは生命の発見ではない", terms, (77, 224, 132, 255))
        self.assertIn(EMPHASIS_RED, {color for _, color in spans})

    def test_timing_manifest_persists_deterministic_emphasis_terms(self):
        terms = deterministic_emphasis_terms({}, "Jezeroの湖と地下水を調べた。")
        self.assertEqual(terms, [])
        terms = deterministic_emphasis_terms({"emphasis_terms": ["Jezero"]}, "Jezeroの湖と地下水を調べた。")
        self.assertEqual(terms, ["Jezero"])
        with self.assertRaises(ValueError):
            deterministic_emphasis_terms({"emphasis_terms": ["Jezero", "地下水"]}, "Jezeroの湖と地下水を調べた。")

    def test_news60_emphasis_requires_reason_and_is_limited_per_beat_and_video(self):
        mission = {"template_id": "zundamon_news60"}
        lines = {
            "L1": {"semantic_beat_id": "HOOK", "emphasis_reason": "A decisive, verified contrast."},
            "L2": {"semantic_beat_id": "HOOK", "emphasis_reason": "Another phrase."},
            "L3": {"semantic_beat_id": "EVIDENCE", "emphasis_reason": "A key source-backed finding."},
        }
        records = [
            {"id": "L1", "caption_text": "first", "caption_emphasis_terms": ["first"]},
            {"id": "L2", "caption_text": "ordinary", "caption_emphasis_terms": []},
            {"id": "L3", "caption_text": "second", "caption_emphasis_terms": ["second"]},
        ]
        self.assertEqual(validate_shortform_emphasis(mission, records, lines), 2)
        records[-1]["caption_emphasis_terms"] = ["second", "third"]
        with self.assertRaises(SystemExit):
            validate_shortform_emphasis(mission, records, lines)
        records = [
            {"id": "L1", "caption_text": "one", "caption_emphasis_terms": ["one"]},
            {"id": "L2", "caption_text": "two", "caption_emphasis_terms": ["two"]},
            {"id": "L3", "caption_text": "three", "caption_emphasis_terms": ["three"]},
        ]
        lines["L2"]["semantic_beat_id"] = "EVIDENCE"
        lines["L3"]["semantic_beat_id"] = "TAKEAWAY"
        self.assertEqual(validate_shortform_emphasis(mission, records, lines), 3)
        records.append({"id": "L4", "caption_text": "four", "caption_emphasis_terms": ["four"]})
        lines["L4"] = {"semantic_beat_id": "OTHER", "emphasis_reason": "Reason."}
        with self.assertRaises(SystemExit):
            validate_shortform_emphasis(mission, records, lines)

    def test_emphasis_is_explicit_only_and_bounded_to_one_phrase(self):
        self.assertEqual(emphasis_terms_for_line({}, "NASAが新発見を発表した。"), [])
        self.assertEqual(emphasis_terms_for_line({"emphasis_terms": ["新発見"]}, "NASAが新発見を発表した。"), ["新発見"])
        with self.assertRaises(RuntimeError):
            emphasis_terms_for_line({"emphasis_terms": ["NASA", "新発見"]}, "NASAが新発見を発表した。")

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

    def test_rendered_visual_evidence_requires_actual_scene_use(self):
        mission = {"scenes": [{"scene_id": "M01"}, {"scene_id": "M02"}]}
        evidence = rendered_visual_evidence([
            {
                "scene_id": "M01",
                "photo_rendered": True,
                "asset_id": "mars_jezero_crater_rim_panorama",
                "attribution_display_text": "Image credit: NASA/JPL-Caltech via Wikimedia Commons · PD-USGov · context image",
                "attribution_width_px": 760,
            },
            {
                "scene_id": "M02",
                "photo_rendered": True,
                "asset_id": "mars_perseverance_jezero_map",
                "attribution_display_text": "Image credit: NASA/JPL-Caltech/University of Arizona via Wikimedia Commons · PD-USGov · context image",
                "attribution_width_px": 840,
            },
        ], mission)
        self.assertEqual(evidence["rendered_photo_scene_coverage_ratio"], 1.0)
        self.assertEqual(evidence["asset_ids_used"], ["mars_jezero_crater_rim_panorama", "mars_perseverance_jezero_map"])
        self.assertTrue(all(row["attribution_fits_reserved_width"] for row in evidence["scenes"]))
        self.assertIn("PD-USGov", evidence["scenes"][0]["attribution_display_text"])
        self.assertIn("University of Arizona", evidence["scenes"][1]["attribution_display_text"])


if __name__ == "__main__":
    unittest.main()
