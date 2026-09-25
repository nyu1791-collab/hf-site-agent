import csv
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts.export_ymm4_script import (
    ALLOWED_BEATS,
    ALLOWED_EMOTIONS,
    ALLOWED_SPEAKERS,
    MAX_SPECIAL_HIGHLIGHTS,
    MAX_SPECIAL_HIGHLIGHTS_PER_BEAT,
    REQUIRED_FIELDS,
    ExportError,
    build_exports,
    write_exports,
)


def sample():
    return {
        "title": "テスト台本",
        "dialogue": [
            {
                "id": "L1",
                "chapter_id": "CH1",
                "chapter_title": "第1章",
                "speaker": "ずんだもん",
                "voice_text": "結果は,\"三つ\"です。\n続き",
                "caption_text": "結果は、三つです。\n続き",
                "emotion": "curious",
                "visual_beat": "公式画像を表示",
                "source_claim_ids": ["C1"],
                "semantic_beat_id": "HOOK",
                "emphasis_terms": ["三つ"],
                "emphasis_reason": "結論を変える数値",
            },
            {
                "id": "L2",
                "chapter_id": "CH2",
                "chapter_title": "第2章",
                "speaker": "四国めたん",
                "voice_text": "なるほど。",
                "caption_text": "なるほど。",
                "emotion": "thoughtful",
                "visual_beat": "",
                "source_claim_ids": [],
                "semantic_beat_id": "TAKEAWAY",
                "emphasis_terms": [],
                "emphasis_reason": "",
            },
        ],
    }


class ExportYmm4ScriptTests(unittest.TestCase):
    def test_export_contract_matches_enforced_shortform_template(self):
        path = Path(__file__).resolve().parents[1] / "config/zundamon_news60_template.json"
        policy = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(REQUIRED_FIELDS, set(policy["script_contract"]["required_dialogue_fields"]))
        self.assertEqual(ALLOWED_SPEAKERS, set(policy["caption_color"]["speaker_identity_colors"]))
        self.assertEqual(ALLOWED_EMOTIONS, set(policy["character_performance"]["expression_states"]))
        self.assertEqual(ALLOWED_BEATS, {beat["id"] for beat in policy["story_beats"]})
        self.assertEqual(MAX_SPECIAL_HIGHLIGHTS, policy["caption_color"]["maximum_special_highlights_in_60_second_video"])
        self.assertEqual(MAX_SPECIAL_HIGHLIGHTS_PER_BEAT, policy["caption_color"]["maximum_special_highlights_per_semantic_beat"])

    def test_builds_two_column_rows_and_preserves_review_cues(self):
        rows, cues = build_exports(sample())
        self.assertEqual(rows, [["ずんだもん", '結果は,"三つ"です。\n続き'], ["四国めたん", "なるほど。"]])
        self.assertEqual(cues["line_count"], 2)
        self.assertEqual(cues["caption_text_difference_count"], 1)
        self.assertEqual(cues["cues"][0]["emotion"], "curious")
        self.assertFalse(cues["cues"][0]["caption_matches_voice_text"])
        self.assertEqual(cues["cues"][0]["emphasis_terms"], ["三つ"])

    def test_csv_round_trip_quotes_comma_quote_newline_and_uses_utf8_bom(self):
        rows, cues = build_exports(sample())
        with tempfile.TemporaryDirectory() as raw:
            csv_path, cues_path = write_exports(rows, cues, Path(raw))
            text = csv_path.read_text(encoding="utf-8-sig")
            self.assertTrue(csv_path.read_bytes().startswith(bytes.fromhex("efbbbf")))
            self.assertIn('ずんだもん,"結果は,""三つ""です。\n続き"', text)
            self.assertEqual(list(csv.reader(io.StringIO(text, newline=""))), rows)
            self.assertEqual(json.loads(cues_path.read_text(encoding="utf-8"))["line_count"], 2)
            self.assertEqual(json.loads(cues_path.read_text(encoding="utf-8"))["caption_text_difference_count"], 1)

    def test_rejects_unknown_speaker_and_duplicate_ids(self):
        doc = sample()
        doc["dialogue"][0]["speaker"] = "unknown"
        with self.assertRaisesRegex(ExportError, "unsupported_speaker"):
            build_exports(doc)
        doc = sample()
        doc["dialogue"][1]["id"] = "L1"
        with self.assertRaisesRegex(ExportError, "duplicate_id"):
            build_exports(doc)

    def test_rejects_unjustified_or_excessive_highlights(self):
        doc = sample()
        doc["dialogue"][0]["emphasis_reason"] = ""
        with self.assertRaisesRegex(ExportError, "highlighted_term_requires_reason"):
            build_exports(doc)
        doc = sample()
        for line_id, beat in [("L3", "WHAT_CHANGED"), ("L4", "EVIDENCE")]:
            line = copy.deepcopy(doc["dialogue"][1])
            line["id"] = line_id
            line["semantic_beat_id"] = beat
            line["emphasis_terms"] = ["なるほど"]
            line["emphasis_reason"] = "判断に必要な箇所"
            doc["dialogue"].append(line)
        doc["dialogue"][1]["emphasis_terms"] = ["なるほど"]
        doc["dialogue"][1]["emphasis_reason"] = "判断に必要な箇所"
        with self.assertRaisesRegex(ExportError, "exceed_3"):
            build_exports(doc)

    def test_rejects_multiple_highlights_in_one_semantic_beat(self):
        doc = sample()
        doc["dialogue"][0]["emphasis_terms"] = ["結果", "三つ"]
        with self.assertRaisesRegex(ExportError, "exceed_1_per_semantic_beat:HOOK"):
            build_exports(doc)

        doc = sample()
        doc["dialogue"][1]["semantic_beat_id"] = "HOOK"
        doc["dialogue"][1]["emphasis_terms"] = ["なるほど"]
        doc["dialogue"][1]["emphasis_reason"] = "判断に必要な箇所"
        with self.assertRaisesRegex(ExportError, "exceed_1_per_semantic_beat:HOOK"):
            build_exports(doc)

    def test_longform_can_use_more_than_three_manual_highlights_across_chapters(self):
        doc = sample()
        for index, beat in enumerate(
            ["WHAT_CHANGED", "WHY_IT_HAPPENED", "EVIDENCE", "LIMIT_OR_CAVEAT"],
            start=3,
        ):
            line = copy.deepcopy(doc["dialogue"][0])
            line["id"] = f"L{index}"
            line["chapter_id"] = f"CH{index}"
            line["chapter_title"] = f"第{index}章"
            line["semantic_beat_id"] = beat
            line["caption_text"] = "重要語"
            line["voice_text"] = "重要語"
            line["emphasis_terms"] = ["重要語"]
            line["emphasis_reason"] = "章内の結論に必要"
            doc["dialogue"].append(line)
        _, cues = build_exports(
            doc,
            max_total_highlights=None,
            highlight_scope="chapter",
        )
        self.assertEqual(cues["special_highlight_count"], 5)
        self.assertEqual(cues["cues"][2]["chapter_id"], "CH3")

    def test_longform_blocks_two_highlights_in_one_chapter(self):
        doc = sample()
        line = copy.deepcopy(doc["dialogue"][0])
        line["id"] = "L3"
        line["chapter_id"] = "CH1"
        line["chapter_title"] = "第1章"
        line["semantic_beat_id"] = "EVIDENCE"
        line["caption_text"] = "大切な語"
        line["voice_text"] = "大切な語"
        line["emphasis_terms"] = ["大切な語"]
        line["emphasis_reason"] = "要点の識別"
        doc["dialogue"].append(line)
        with self.assertRaisesRegex(ExportError, "exceed_1_per_chapter:CH1"):
            build_exports(
                doc,
                max_total_highlights=None,
                highlight_scope="chapter",
            )

    def test_existing_outputs_are_not_overwritten_unless_requested(self):
        rows, cues = build_exports(sample())
        with tempfile.TemporaryDirectory() as raw:
            write_exports(rows, cues, Path(raw))
            with self.assertRaisesRegex(ExportError, "output_exists"):
                write_exports(rows, cues, Path(raw))
            write_exports(rows, cues, Path(raw), overwrite=True)


if __name__ == "__main__":
    unittest.main()
