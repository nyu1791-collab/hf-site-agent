import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from export_ymm4_script import ExportError, build_exports, write_exports


def sample():
    return {
        "title": "テスト台本",
        "dialogue": [
            {
                "id": "L1",
                "speaker": "ずんだもん",
                "voice_text": "結果は,三つです。\\n続き",
                "caption_text": "結果は、三つです。",
                "emotion": "curious",
                "visual_beat": "公式画像を表示",
                "source_claim_ids": ["C1"],
                "semantic_beat_id": "HOOK",
                "emphasis_terms": ["三つ"],
                "emphasis_reason": "結論を変える数値",
            },
            {
                "id": "L2",
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
    def test_builds_two_column_rows_and_preserves_review_cues(self):
        rows, cues = build_exports(sample())
        self.assertEqual(rows, [["ずんだもん", "結果は,三つです。\\n続き"], ["四国めたん", "なるほど。"]])
        self.assertEqual(cues["line_count"], 2)
        self.assertEqual(cues["cues"][0]["emotion"], "curious")
        self.assertFalse(cues["cues"][0]["caption_matches_voice_text"])
        self.assertEqual(cues["cues"][0]["emphasis_terms"], ["三つ"])

    def test_csv_round_trip_quotes_comma_and_uses_windows_friendly_bom(self):
        rows, cues = build_exports(sample())
        with tempfile.TemporaryDirectory() as raw:
            csv_path, cues_path = write_exports(rows, cues, Path(raw))
            text = csv_path.read_text(encoding="utf-8-sig")
            self.assertTrue(csv_path.read_bytes().startswith(bytes.fromhex("efbbbf")))
            self.assertEqual(list(csv.reader(io.StringIO(text, newline=""))), rows)
            self.assertEqual(json.loads(cues_path.read_text(encoding="utf-8"))["line_count"], 2)

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
        doc["dialogue"][1]["emphasis_terms"] = ["な", "る", "ほど"]
        doc["dialogue"][1]["emphasis_reason"] = "test"
        with self.assertRaisesRegex(ExportError, "exceed_3"):
            build_exports(doc)

    def test_existing_outputs_are_not_overwritten_unless_requested(self):
        rows, cues = build_exports(sample())
        with tempfile.TemporaryDirectory() as raw:
            write_exports(rows, cues, Path(raw))
            with self.assertRaisesRegex(ExportError, "output_exists"):
                write_exports(rows, cues, Path(raw))
            write_exports(rows, cues, Path(raw), overwrite=True)


if __name__ == "__main__":
    unittest.main()
