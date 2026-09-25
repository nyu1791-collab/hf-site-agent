import copy
import csv
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.export_ymm4_script import ExportError
from scripts.prepare_ymm4_news60_routine import (
    EXPECTED_FILES,
    RoutineError,
    prepare_package,
    validate_contracts,
)


ROOT = Path(__file__).resolve().parents[1]


def script():
    return {
        "title": "架空の説明",
        "dialogue": [
            {
                "id": "L1",
                "speaker": "ずんだもん",
                "voice_text": "今日の問いなのだ。",
                "caption_text": "今日の問いなのだ。",
                "emotion": "curious",
                "visual_beat": "",
                "source_claim_ids": [],
                "semantic_beat_id": "HOOK",
                "emphasis_terms": [],
                "emphasis_reason": "",
            },
            {
                "id": "L2",
                "speaker": "四国めたん",
                "voice_text": "エーアイの資料です。",
                "caption_text": "AIの資料です。",
                "emotion": "serious",
                "visual_beat": "資料の図を確認",
                "source_claim_ids": ["C1"],
                "semantic_beat_id": "EVIDENCE",
                "emphasis_terms": ["資料"],
                "emphasis_reason": "核心となる資料",
            },
            {
                "id": "L3",
                "speaker": "ずんだもん",
                "voice_text": "ただし、断定はできないのだ。",
                "caption_text": "ただし、断定はできないのだ。",
                "emotion": "thoughtful",
                "visual_beat": "補足の図",
                "source_claim_ids": [],
                "semantic_beat_id": "LIMIT_OR_CAVEAT",
                "emphasis_terms": [],
                "emphasis_reason": "",
            },
        ],
    }


class Ymm4RoutineTests(unittest.TestCase):
    def _write_script(self, parent, document=None):
        path = Path(parent) / "script.json"
        path.write_text(json.dumps(document or script(), ensure_ascii=False), encoding="utf-8")
        return path

    def test_package_uses_story_beats_and_preserves_unverified_rights_state(self):
        with tempfile.TemporaryDirectory() as raw:
            input_path = self._write_script(raw)
            output_dir = Path(raw) / "new_episode"
            manifest = prepare_package(input_path, output_dir)
            self.assertEqual({p.name for p in output_dir.iterdir()}, EXPECTED_FILES)
            self.assertEqual(manifest["status"], "IMPORT_PACKAGE_READY_NOT_MEDIA_VALIDATED")
            self.assertEqual(manifest["measured_voice_timing"], "NOT_YET_MEASURED")
            self.assertEqual(manifest["project_template_windows_check"], "REQUIRED_SEPARATELY")
            for name, digest in manifest["files_sha256"].items():
                self.assertEqual(hashlib.sha256((output_dir / name).read_bytes()).hexdigest(), digest)
            csv_bytes = (output_dir / "ymm4_script.csv").read_bytes()
            self.assertTrue(csv_bytes.startswith(bytes.fromhex("efbbbf")))
            rows = list(csv.reader(io.StringIO(csv_bytes.decode("utf-8-sig"), newline="")))
            self.assertEqual(rows[1], ["四国めたん", "エーアイの資料です。"])
            cues = json.loads((output_dir / "ymm4_review_cues.json").read_text(encoding="utf-8"))
            self.assertEqual(cues["caption_text_difference_count"], 1)
            sheet = json.loads((output_dir / "ymm4_beat_sheet.json").read_text(encoding="utf-8"))
            self.assertEqual(sheet["speaker_colors"]["ずんだもん"], "#4DE084")
            self.assertEqual(sheet["cards"][0]["planning_window_seconds"], [0, 3])
            evidence = next(card for card in sheet["cards"] if card["beat_id"] == "EVIDENCE")
            self.assertEqual(evidence["line_ids"], ["L2"])
            self.assertEqual(evidence["visual_requests"][0]["rights_status"], "REVIEW_REQUIRED")
            self.assertEqual(evidence["caption_difference_line_ids"], ["L2"])
            self.assertEqual(sheet["visual_cues_without_claim_ids_for_review"], ["L3"])
            self.assertEqual(sheet["factual_beats_without_claim_ids_for_review"], [])
            self.assertIn("WHAT_CHANGED", sheet["missing_story_beats_for_editorial_review"])
            self.assertFalse(any(path.suffix in {".wav", ".mp4", ".ymmp"} for path in output_dir.iterdir()))

    def test_existing_directory_and_invalid_script_leave_no_partial_package(self):
        with tempfile.TemporaryDirectory() as raw:
            input_path = self._write_script(raw)
            output_dir = Path(raw) / "existing"
            output_dir.mkdir()
            sentinel = output_dir / "keep.txt"
            sentinel.write_text("untouched", encoding="utf-8")
            with self.assertRaisesRegex(RoutineError, "output_directory_exists"):
                prepare_package(input_path, output_dir)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "untouched")

            invalid = script()
            invalid["dialogue"][1]["speaker"] = "unknown"
            self._write_script(raw, invalid)
            blocked_dir = Path(raw) / "blocked"
            with self.assertRaisesRegex(ExportError, "unsupported_speaker"):
                prepare_package(input_path, blocked_dir)
            self.assertFalse(blocked_dir.exists())

    def test_scaffold_requires_filling_and_contract_drift_is_rejected(self):
        scaffold = json.loads((ROOT / "examples/ymm4_news60_script_template.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as raw:
            path = self._write_script(raw, scaffold)
            with self.assertRaisesRegex(ExportError, "title_must_be_nonempty_text"):
                prepare_package(path, Path(raw) / "no_placeholder_output")
        shortform = json.loads((ROOT / "config/zundamon_news60_template.json").read_text(encoding="utf-8"))
        routine = json.loads((ROOT / "config/ymm4_news60_routine.json").read_text(encoding="utf-8"))
        validate_contracts(shortform, routine)
        drift = copy.deepcopy(routine)
        drift["reuse"]["never_assume_renderer_reaction_pack_is_ymm4_compatible"] = False
        with self.assertRaisesRegex(RoutineError, "routine_reuse_guard_drift"):
            validate_contracts(shortform, drift)

    def test_unsourced_evidence_is_flagged_for_editorial_review(self):
        with tempfile.TemporaryDirectory() as raw:
            document = script()
            document["dialogue"][1]["source_claim_ids"] = []
            input_path = self._write_script(raw, document)
            output_dir = Path(raw) / "review"
            prepare_package(input_path, output_dir)
            sheet = json.loads((output_dir / "ymm4_beat_sheet.json").read_text(encoding="utf-8"))
            self.assertEqual(sheet["factual_beats_without_claim_ids_for_review"], ["L2"])
            self.assertEqual(sheet["visual_cues_without_claim_ids_for_review"], ["L2", "L3"])

    def test_cli_runs_from_repo_root_without_creating_media(self):
        with tempfile.TemporaryDirectory() as raw:
            input_path = self._write_script(raw)
            output_dir = Path(raw) / "package"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts/prepare_ymm4_news60_routine.py"),
                    "--input", str(input_path),
                    "--output-dir", str(output_dir),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("No audio, YMM4 project, or video", result.stdout)
            self.assertTrue((output_dir / "ymm4_run_sheet.md").is_file())


if __name__ == "__main__":
    unittest.main()
