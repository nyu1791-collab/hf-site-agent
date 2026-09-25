import csv
import copy
import hashlib
import io
import json
import tempfile
import unittest
import wave
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
        "visual_asset_ids": ["V1"],
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
                "source_support": [
                    {
                        "source_id": "S1",
                        "locator": "Section 2, paragraph 1",
                        "support_summary": "ここに要約した記述が主張を直接支える。",
                    }
                ],
                "reviewed_by": "reviewer-1",
                "review_method": "原資料の該当段落と主張を照合",
                "reviewed_at": "2026-09-25",
            }
        ],
        "visual_assets": [
            {
                "id": "V1",
                "source_page": "https://example.org/report/figures",
                "asset_locator": "Figure 2, SVG download",
                "license_or_public_domain_state": "UNKNOWN_PENDING_REVIEW",
                "rights_status": "REVIEW_REQUIRED",
                "retrieval_or_verification_timestamp": "2026-09-25T12:00:00Z",
                "scene_or_claim_mapping": {
                    "line_ids": ["L1"],
                    "chapter_ids": ["CH01"],
                    "claim_ids": ["C1"],
                },
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
                            "visual_asset_ids": [],
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
            self.assertEqual(manifest["voice_credit_status"], "REVIEW_REQUIRED")
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
            self.assertEqual(chapters["chapters"][0]["source_display"][0]["locator"], "Section 2, paragraph 1")
            self.assertEqual(chapters["chapters"][0]["timing_status"], "WAITING_FOR_MEASURED_VOICEVOX_WAV")

            cues = json.loads((output / "ymm4_review_cues.json").read_text(encoding="utf-8"))
            self.assertEqual(cues["cues"][0]["chapter_id"], "CH01")
            self.assertEqual(cues["cues"][0]["claim_review_statuses"], {"C1": "VERIFIED"})
            self.assertEqual(cues["cues"][0]["caption_difference_reason"], "")
            register = json.loads((output / "ymm4_source_claim_register.json").read_text(encoding="utf-8"))
            self.assertEqual(register["claim_verification"], "NOT_PERFORMED_BY_PACKAGE_BUILDER")
            self.assertEqual(register["visual_assets_requiring_rights_review"], ["V1"])
            credits = json.loads((output / "ymm4_voice_credit_checklist.json").read_text(encoding="utf-8"))
            self.assertEqual(credits["status"], "REVIEW_REQUIRED")
            self.assertEqual(credits["voice_credits"][0]["exact_credit_text"], "VOICEVOX:ずんだもん")
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

    def test_template_ids_package_files_and_read_gate_stay_in_sync(self):
        template = json.loads((ROOT / "examples/ymm4_research_explainer_script_template.json").read_text(encoding="utf-8"))
        profile = json.loads((ROOT / "config/ymm4_research_explainer_profile.json").read_text(encoding="utf-8"))
        read_gate = json.loads((ROOT / "config/media_command_read_gate.json").read_text(encoding="utf-8"))
        manifest = json.loads((ROOT / "config/permanent_standards_manifest.json").read_text(encoding="utf-8"))
        claims = {row["id"] for row in template["claim_ledger"]}
        lines = {line["id"]: line for chapter in template["chapters"] for line in chapter["dialogue"]}
        self.assertEqual(len(claims), len(template["claim_ledger"]))
        for line in lines.values():
            self.assertTrue(set(line["source_claim_ids"]).issubset(claims))
            if line["claim_bearing"]:
                self.assertTrue(line["source_claim_ids"])
        for asset in template["visual_assets"]:
            mapping = asset["scene_or_claim_mapping"]
            self.assertTrue(set(mapping["line_ids"]).issubset(lines))
            self.assertTrue(set(mapping["claim_ids"]).issubset(claims))
        configured_files = {item.split(" ", 1)[0] for item in profile["package_files"]}
        self.assertEqual(configured_files, PACKAGE_FILES | {"ymm4_caption_timeline.srt", "ymm4_wav_timing_manifest.json"})
        self.assertIn("scripts/export_ymm4_script.py", read_gate["trigger_sets"]["VIDEO_CREATION"]["conditional"]["if_longform"])

        def walk(value):
            if isinstance(value, dict):
                if value.get("id") == "ymm4-research-explainer-profile":
                    return value
                for child in value.values():
                    found = walk(child)
                    if found:
                        return found
            elif isinstance(value, list):
                for child in value:
                    found = walk(child)
                    if found:
                        return found
            return None

        profile_entry = walk(manifest)
        self.assertIsNotNone(profile_entry)
        self.assertIn("scripts/export_ymm4_script.py", profile_entry["dependencies"])

    def test_dictionary_preserves_full_caption_and_reason_never_allows_omission(self):
        doc = sample()
        line = doc["chapters"][0]["dialogue"][0]
        line["voice_text"] = "これはエーアイで確認された内容なのだ。"
        line["caption_text"] = "これはAIで確認された内容なのだ。"
        doc["pronunciation_dictionary"] = [
            {
                "surface_term": "AI",
                "voice_reading": "エーアイ",
                "caption_spelling": "AI",
                "revision": "house-style-2026-09",
            }
        ]
        with self.assertRaisesRegex(RoutineError, "caption_dictionary_difference_requires_reason:L1"):
            validate_document(doc)
        line["caption_difference_reason"] = "読みを表記辞書に従って英字表記へ戻す"
        _, cues, _ = validate_document(doc)
        self.assertEqual(cues["cues"][0]["caption_difference_reason"], "読みを表記辞書に従って英字表記へ戻す")
        line["caption_text"] = "これはAIで確認された内容。"
        line["caption_difference_reason"] = "読みにくいので一部を省略"
        with self.assertRaisesRegex(RoutineError, "caption_content_mismatch_or_omission:L1"):
            validate_document(doc)

    def test_blocks_unknown_sources_and_blocked_claims(self):
        doc = sample()
        doc["claim_ledger"][0]["source_ids"] = ["S404"]
        with self.assertRaisesRegex(RoutineError, "unknown_source_ids"):
            validate_document(doc)

    def test_verified_and_qualified_claims_require_traceable_support_and_review(self):
        doc = sample()
        doc["claim_ledger"][0]["source_support"] = []
        with self.assertRaisesRegex(RoutineError, "claim_C1_verified_requires_source_locator_and_summary"):
            validate_document(doc)
        doc = sample()
        doc["claim_ledger"][0]["review_status"] = "QUALIFIED"
        with self.assertRaisesRegex(RoutineError, "claim_C1_qualification_note_must_be_nonempty_text"):
            validate_document(doc)

    def test_duplicate_ids_and_unrelated_pronunciation_rewrites_are_blocked(self):
        doc = sample()
        doc["claim_ledger"][0]["source_ids"] = ["S1", "S1"]
        with self.assertRaisesRegex(RoutineError, "source_ids_must_not_contain_duplicates"):
            validate_document(doc)
        doc = sample()
        doc["pronunciation_dictionary"] = [{
            "surface_term": "AI",
            "voice_reading": "エーアイ",
            "caption_spelling": "別の意味",
            "revision": "r1",
        }]
        with self.assertRaisesRegex(RoutineError, "surface_term_must_equal_caption_spelling"):
            validate_document(doc)

    def test_dates_and_visual_provenance_fail_closed(self):
        doc = sample()
        doc["source_ledger"][0]["checked_at"] = "2026-09-26"
        with self.assertRaisesRegex(RoutineError, "checked_after_research_cutoff"):
            validate_document(doc)
        doc = sample()
        doc["visual_assets"][0]["rights_status"] = "CLEARED"
        doc["visual_assets"][0]["license_or_public_domain_state"] = "UNKNOWN_PENDING_REVIEW"
        with self.assertRaisesRegex(RoutineError, "cleared_visual_V1_has_unknown_license_state"):
            validate_document(doc)
        doc = sample()
        doc["visual_assets"][0]["scene_or_claim_mapping"]["line_ids"] = ["L2"]
        with self.assertRaisesRegex(RoutineError, "visual_V1_line_mapping_mismatch"):
            validate_document(doc)
        doc = sample()
        doc["visual_assets"][0]["retrieval_or_verification_timestamp"] = "2026-09-26T12:00:00Z"
        with self.assertRaisesRegex(RoutineError, "visual_V1_retrieved_after_research_cutoff"):
            validate_document(doc)

    def test_cleared_visual_requires_a_specific_reuse_basis_and_blocks_restricted_licenses(self):
        doc = sample()
        asset = doc["visual_assets"][0]
        asset.update({
            "rights_status": "CLEARED",
            "license_or_public_domain_state": "CC-BY-4.0",
            "rights_basis_locator": "https://creativecommons.org/licenses/by/4.0/",
            "reuse_conditions_summary": "Attribution is required; attribution text is recorded in the release credits.",
            "rights_review_method": "Checked the exact asset page against the linked license terms.",
            "rights_reviewed_by": "rights-reviewer-1",
            "rights_reviewed_at": "2026-09-25",
        })
        _, _, register = validate_document(doc)
        self.assertEqual(register["visual_rights_status"], "CLEARED_FOR_LISTED_ASSETS")
        asset["license_or_public_domain_state"] = "CC-BY-NC-4.0"
        with self.assertRaisesRegex(RoutineError, "has_blocked_license_state"):
            validate_document(doc)

    def test_no_visuals_produces_not_selected_not_review_required(self):
        doc = sample()
        doc["visual_assets"] = []
        doc["chapters"][0]["dialogue"][0]["visual_asset_ids"] = []
        _, _, register = validate_document(doc)
        self.assertEqual(register["visual_rights_status"], "NOT_SELECTED")

    def test_measured_wavs_create_text_only_srt_and_hash_manifest(self):
        doc = sample()
        doc["chapters"][0]["dialogue"][0]["voice_audio_file"] = "L1.wav"
        doc["chapters"][1]["dialogue"][0]["voice_audio_file"] = "L2.wav"
        with tempfile.TemporaryDirectory() as raw:
            raw_dir = Path(raw)
            audio_dir = raw_dir / "wavs"
            audio_dir.mkdir()
            for filename in ("L1.wav", "L2.wav"):
                with wave.open(str(audio_dir / filename), "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(8000)
                    wav.writeframes(b"\x00\x00" * 1600)
            input_path = raw_dir / "script.json"
            input_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            output = raw_dir / "episode"
            manifest = prepare_package(input_path, output, audio_dir)
            self.assertEqual(manifest["audio_timing_status"], "MEASURED_FROM_WAV_FRAME_COUNT_WINDOWS_PREVIEW_REQUIRED")
            self.assertIn("ymm4_caption_timeline.srt", {path.name for path in output.iterdir()})
            srt = (output / "ymm4_caption_timeline.srt").read_text(encoding="utf-8")
            self.assertTrue((output / "ymm4_caption_timeline.srt").read_bytes().startswith(bytes.fromhex("efbbbf")))
            self.assertIn("00:00:00,000 --> 00:00:00,200", srt)
            self.assertIn("00:00:00,380 --> 00:00:00,580", srt)
            self.assertNotIn(">>", srt)
            timing = json.loads((output / "ymm4_wav_timing_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual([entry["duration_ms"] for entry in timing["entries"]], [200, 200])
            self.assertEqual(len(timing["entries"][0]["sha256"]), 64)
            self.assertEqual(manifest["files_sha256"]["ymm4_caption_timeline.srt"], hashlib.sha256((output / "ymm4_caption_timeline.srt").read_bytes()).hexdigest())
            self.assertFalse(any(path.suffix == ".wav" for path in output.iterdir()))

    def test_timing_requires_one_valid_wav_per_line_and_never_accepts_paths(self):
        doc = sample()
        doc["chapters"][0]["dialogue"][0]["voice_audio_file"] = "../L1.wav"
        with tempfile.TemporaryDirectory() as raw:
            raw_dir = Path(raw)
            audio_dir = raw_dir / "wavs"
            audio_dir.mkdir()
            input_path = raw_dir / "script.json"
            input_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(RoutineError, "every_dialogue_line_requires_one_voice_audio_file"):
                prepare_package(input_path, raw_dir / "bad", audio_dir)
            doc["chapters"][1]["dialogue"][0]["voice_audio_file"] = "L2.wav"
            input_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(RoutineError, "must_be_a_basename"):
                prepare_package(input_path, raw_dir / "bad2", audio_dir)
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
        doc["visual_assets"] = []
        for index, chapter in enumerate(doc["chapters"]):
            line = chapter["dialogue"][0]
            line["id"] = f"H{index}"
            line["visual_asset_ids"] = []
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
