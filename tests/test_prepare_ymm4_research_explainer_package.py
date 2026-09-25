import csv
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts.export_ymm4_script import ExportError
from scripts.prepare_ymm4_research_explainer_package import (
    PACKAGE_FILES,
    RoutineError,
    prepare_package,
    validate_document,
)


ROOT = Path(__file__).resolve().parents[1]


def sample():
    line = {
        "speaker": "ずんだもん",
        "voice_text": "確認された内容なのだ。",
        "caption_text": "確認された内容なのだ。",
        "caption_difference_reason": "",
        "emotion": "serious",
        "visual_beat": "公式資料の図を表示",
        "source_claim_ids": ["C1"],
        "claim_bearing": True,
        "semantic_beat_id": "EVIDENCE",
        "emphasis_terms": [],
        "emphasis_reason": "",
    }
    return {
        "title": "検証用の研究解説",
        "research_cutoff_date": "2026-09-25",
        "target_duration_minutes": [8, 12],
        "source_ledger": [
            {
                "id": "S1",
                "title": "公式技術資料",
                "url": "https://example.org/report",
                "source_type": "PRIMARY_OFFICIAL",
                "published_date": "2026-09-24",
                "checked_at": "2026-09-25",
            }
        ],
        "claim_ledger": [
            {
                "id": "C1",
                "statement": "資料が直接示す内容",
                "source_ids": ["S1"],
                "review_status": "VERIFIED",
            }
        ],
        "visual_assets": [
            {
                "id": "V1",
                "source_url": "https://example.org/figure",
                "claim_ids": ["C1"],
                "rights_status": "REVIEW_REQUIRED",
            }
        ],
        "chapters": [
            {
                "id": "CH01",
                "title": "何が分かったのか",
                "purpose": "一次資料の内容を説明する。",
                "dialogue": [
                    {"id": "L1", **copy.deepcopy(line)}
                ],
            },
            {
                "id": "CH02",
                "title": "どこに注意するか",
                "purpose": "範囲と限界を確認する。",
                "dialogue": [
                    {
                        "id": "L2",
                        **{
                            **copy.deepcopy(line),
                            "speaker": "四国めたん",
                            "emotion": "thoughtful",
                            "semantic_beat_id": "LIMIT_OR_CAVEAT",
                            "claim_bearing": False,
                            "source_claim_ids": [],
                            "visual_beat": "",
                        },
                    }
                ],
            },
        ],
    }


class Ymm4ResearchExplainerPackageTests(unittest.TestCase):
    def test_builds_csv_chapters_sources_hashes_and_review_states_without_media(self):
        with tempfile.TemporaryDirectory() as raw:
            input_path = Path(raw) / "script.json"
            input_path.write_text(json.dumps(sample(), ensure_ascii=False), encoding="utf-8")
            output = Path(raw) / "episode"
            manifest = prepare_package(input_path, output)

            self.assertEqual({path.name for path in output.iterdir()}, PACKAGE_FILES)
            self.assertEqual(manifest["status"], "IMPORT_PACKAGE_READY_NOT_MEDIA_VALIDATED")
            self.assertEqual(manifest["source_verification_status"], "NOT_PERFORMED_BY_PACKAGE_BUILDER")
            self.assertEqual(manifest["audio_timing_status"], "NOT_YET_MEASURED")
            self.assertEqual(manifest["visual_rights_status"], "REVIEW_REQUIRED")
            self.assertEqual(manifest["windows_baseline_status"], "REQUIRED_SEPARATELY")
            for name, digest in manifest["files_sha256"].items():
                self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), digest)

            csv_bytes = (output / "ymm4_script.csv").read_bytes()
            self.assertTrue(csv_bytes.startswith(bytes.fromhex("efbbbf")))
            rows = list(csv.reader(io.StringIO(csv_bytes.decode("utf-8-sig"), newline="")))
            self.assertEqual(rows, [
                ["ずんだもん", "確認された内容なのだ。"],
                ["四国めたん", "確認された内容なのだ。"],
            ])

            chapters = json.loads((output / "ymm4_chapter_sheet.json").read_text(encoding="utf-8"))
            self.assertEqual(chapters["chapters"][0]["chapter_id"], "CH01")
            self.assertEqual(chapters["chapters"][0]["source_ids"], ["S1"])
            self.assertEqual(chapters["chapters"][0]["source_display"][0]["title"], "公式技術資料")
            self.assertEqual(chapters["chapters"][0]["timing_status"], "WAITING_FOR_MEASURED_VOICEVOX_WAV")

            cues = json.loads((output / "ymm4_review_cues.json").read_text(encoding="utf-8"))
            self.assertEqual(cues["cues"][0]["chapter_id"], "CH01")
            self.assertEqual(cues["cues"][0]["claim_review_statuses"], {"C1": "VERIFIED"})
            self.assertEqual(cues["cues"][0]["caption_difference_reason"], "")
            register = json.loads((output / "ymm4_source_claim_register.json").read_text(encoding="utf-8"))
            self.assertEqual(register["claim_verification"], "NOT_PERFORMED_BY_PACKAGE_BUILDER")
            self.assertEqual(register["visual_assets_requiring_rights_review"], ["V1"])
            self.assertFalse(any(path.suffix in {".wav", ".mp4", ".ymmp"} for path in output.iterdir()))

    def test_blocks_unfilled_scaffold_and_factual_lines_without_claim_ids(self):
        template_path = ROOT / "examples/ymm4_research_explainer_script_template.json"
        template = json.loads(template_path.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(RoutineError, "title_must_be_nonempty_text"):
            validate_document(template)

        doc = sample()
        doc["chapters"][0]["dialogue"][0]["source_claim_ids"] = []
        with self.assertRaisesRegex(RoutineError, "claim_bearing_line_requires_claim_ids:L1"):
            validate_document(doc)

    def test_caption_difference_requires_a_written_review_reason(self):
        doc = sample()
        line = doc["chapters"][0]["dialogue"][0]
        line["caption_text"] = "確認された内容なのだ"
        with self.assertRaisesRegex(RoutineError, "caption_difference_requires_review_reason:L1"):
            validate_document(doc)
        line["caption_difference_reason"] = "句点だけを字幕では省略"
        _, cues, _ = validate_document(doc)
        self.assertEqual(cues["cues"][0]["caption_difference_reason"], "句点だけを字幕では省略")

    def test_blocks_unknown_sources_and_blocked_claims(self):
        doc = sample()
        doc["claim_ledger"][0]["source_ids"] = ["S404"]
        with self.assertRaisesRegex(RoutineError, "unknown_source_ids"):
            validate_document(doc)
        doc = sample()
        doc["claim_ledger"][0]["review_status"] = "BLOCKED"
        with self.assertRaisesRegex(RoutineError, "blocked_claim_cannot_be_packaged:C1"):
            validate_document(doc)

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as raw:
            input_path = Path(raw) / "script.json"
            input_path.write_text(json.dumps(sample(), ensure_ascii=False), encoding="utf-8")
            output = Path(raw) / "existing"
            output.mkdir()
            marker = output / "keep.txt"
            marker.write_text("leave", encoding="utf-8")
            with self.assertRaisesRegex(RoutineError, "output_directory_exists"):
                prepare_package(input_path, output)
            self.assertEqual(marker.read_text(encoding="utf-8"), "leave")

    def test_highlights_can_repeat_across_chapters_but_not_within_one(self):
        doc = sample()
        for index, chapter in enumerate(doc["chapters"]):
            line = chapter["dialogue"][0]
            line["id"] = f"H{index}"
            line["emphasis_terms"] = ["内容"]
            line["emphasis_reason"] = "章の結論を示す重要語"
        doc["chapters"][1]["dialogue"].append({
            **copy.deepcopy(doc["chapters"][1]["dialogue"][0]),
            "id": "H_DUP",
        })
        with self.assertRaisesRegex(ExportError, "exceed_1_per_chapter:CH02"):
            validate_document(doc)
        doc["chapters"][1]["dialogue"].pop()
        _, cues, _ = validate_document(doc)
        self.assertEqual(cues["special_highlight_count"], 2)


if __name__ == "__main__":
    unittest.main()
