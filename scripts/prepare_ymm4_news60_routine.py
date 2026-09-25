#!/usr/bin/env python3
"""Build a reusable YMM4 import package; do not create a project or media."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.export_ymm4_script import (  # noqa: E402
    CUES_NAME,
    CSV_NAME,
    ExportError,
    build_exports,
    load_script,
    write_exports,
)

SHORTFORM = ROOT / "config/zundamon_news60_template.json"
ROUTINE = ROOT / "config/ymm4_news60_routine.json"
BEAT_SHEET_NAME = "ymm4_beat_sheet.json"
RUN_SHEET_NAME = "ymm4_run_sheet.md"
MANIFEST_NAME = "ymm4_package_manifest.json"
EXPECTED_FILES = {CSV_NAME, CUES_NAME, BEAT_SHEET_NAME, RUN_SHEET_NAME, MANIFEST_NAME}


class RoutineError(ValueError):
    """The YMM4 package cannot be prepared safely."""


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise RoutineError(f"cannot_hash_file:{path}:{exc}") from exc


def _read_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoutineError(f"cannot_read_contract:{path}:{exc}") from exc
    if not isinstance(data, dict):
        raise RoutineError(f"contract_must_be_object:{path}")
    return data


def validate_contracts(
    shortform: Mapping[str, Any], routine: Mapping[str, Any]
) -> None:
    """Reject configuration drift before generating an import package."""
    if shortform.get("schema_version") != "zundamon-news60-template-v1":
        raise RoutineError("shortform_contract_version_drift")
    if routine.get("schema_version") != "ymm4-news60-routine-v1":
        raise RoutineError("routine_contract_version_drift")
    if routine.get("status") != "PREPRODUCTION_BLUEPRINT":
        raise RoutineError("routine_not_preproduction_only")
    if routine.get("applies_to_template") != "config/zundamon_news60_template.json":
        raise RoutineError("routine_shortform_reference_drift")
    bundle_files = routine.get("per_video_bundle_files") or []
    if not isinstance(bundle_files, list) or any(not isinstance(name, str) for name in bundle_files) or len(bundle_files) != len(EXPECTED_FILES) or set(bundle_files) != EXPECTED_FILES:
        raise RoutineError("routine_bundle_file_contract_drift")
    names = routine.get("item_template_names") or []
    if not isinstance(names, list) or len(names) != 4 or any(not isinstance(name, str) or not name.strip() for name in names) or len(set(names)) != 4:
        raise RoutineError("routine_item_template_contract_drift")
    baseline = routine.get("local_baseline_project_name")
    if not isinstance(baseline, str) or Path(baseline).name != baseline or not baseline.endswith(".ymmp"):
        raise RoutineError("routine_baseline_name_invalid")
    reuse = routine.get("reuse") or {}
    if not isinstance(reuse, Mapping) or any(reuse.get(key) is not True for key in (
        "duplicate_baseline_per_video",
        "use_verified_character_and_visual_cache",
        "never_commit_third_party_character_or_audio_assets",
        "never_assume_renderer_reaction_pack_is_ymm4_compatible",
        "do_not_assume_voicevox_app_presets_apply_to_ymm4_direct_voice_generation",
    )):
        raise RoutineError("routine_reuse_guard_drift")
    if not (
        routine.get("baseline_must_be_created_and_checked_on_windows") is True
        and routine.get("target_around_ten_minutes_is_conditional_not_guaranteed") is True
        and routine.get("no_network_paid_call_audio_render_or_video_from_preparation") is True
    ):
        raise RoutineError("routine_safety_contract_drift")
    output = shortform.get("output") or {}
    if output.get("aspect_ratio") != "9:16" or output.get("target_duration_seconds") != [55, 60]:
        raise RoutineError("shortform_output_contract_drift")
    if (shortform.get("caption_color") or {}).get("automatic_keyword_highlighting") is not False:
        raise RoutineError("automatic_highlighting_enabled")
    if (shortform.get("visual_evidence") or {}).get("no_paid_or_freemium_image_or_video_generation") is not True:
        raise RoutineError("paid_media_guard_missing")


def build_beat_sheet(
    cues_document: Mapping[str, Any],
    shortform: Mapping[str, Any],
    routine: Mapping[str, Any],
) -> dict[str, Any]:
    cues = cues_document["cues"]
    cards = []
    missing = []
    for beat in shortform["story_beats"]:
        beat_id = beat["id"]
        lines = [cue for cue in cues if cue["semantic_beat_id"] == beat_id]
        if not lines:
            missing.append(beat_id)
        cards.append(
            {
                "beat_id": beat_id,
                "planning_window_seconds": beat["seconds"],
                "line_ids": [cue["id"] for cue in lines],
                "speakers": list(dict.fromkeys(cue["speaker"] for cue in lines)),
                "expression_review": [
                    {"line_id": cue["id"], "emotion": cue["emotion"]}
                    for cue in lines if cue["emotion"] != "normal"
                ],
                "visual_requests": [
                    {
                        "line_id": cue["id"],
                        "visual_beat": cue["visual_beat"],
                        "source_claim_ids": cue["source_claim_ids"],
                        "rights_status": "REVIEW_REQUIRED",
                    }
                    for cue in lines if cue["visual_beat"]
                ],
                "caption_difference_line_ids": [
                    cue["id"] for cue in lines if not cue["caption_matches_voice_text"]
                ],
                "highlight_terms": [
                    term for cue in lines for term in cue["emphasis_terms"]
                ],
            }
        )
    return {
        "schema_version": "ymm4-news60-beat-sheet-v1",
        "title": cues_document["title"],
        "timing_status": "PLANNING_WINDOWS_ONLY_MEASURE_VOICEVOX_AUDIO_LATER",
        "output": shortform["output"],
        "speaker_colors": shortform["caption_color"]["speaker_identity_colors"],
        "item_template_names": routine["item_template_names"],
        "missing_story_beats_for_editorial_review": missing,
        "visual_cues_without_claim_ids_for_review": [
            cue["id"] for cue in cues
            if cue["visual_beat"] and not cue["source_claim_ids"]
        ],
        "factual_beats_without_claim_ids_for_review": [
            cue["id"] for cue in cues
            if cue["semantic_beat_id"] in {"WHAT_CHANGED", "WHY_IT_HAPPENED", "EVIDENCE"}
            and not cue["source_claim_ids"]
        ],
        "cards": cards,
    }


def _run_sheet(cues: Mapping[str, Any], sheet: Mapping[str, Any], routine: Mapping[str, Any]) -> str:
    title = " ".join(str(cues["title"]).split())
    lines = [
        f"# YMM4 NEWS60 import run sheet — {title}",
        "",
        "This is an import package, not a YMM4 project, approved evidence, audio, or a finished video.",
        f"Baseline on Windows: duplicate the checked {routine['local_baseline_project_name']} project.",
        "If the baseline does not exist, complete the one-time setup in docs/YMM4_NEWS60_ROUTINE.md.",
        "",
        "1. Confirm the current video admission, source/claim lock, and rights state.",
        "2. Import ymm4_script.csv via Tools → Script Import. It carries speaker and voice_text only.",
        "3. Review ymm4_review_cues.json before editing expressions and displayed captions.",
        "4. Reuse the registered headline, explainer, evidence, and takeaway item templates.",
        "5. Use source-matched visuals only after current rights and claim mapping checks.",
        "6. Use the configured local VOICEVOX voices; measure WAV timing before final captions.",
        "7. Check full spoken captions, speaker colors, sparse emphasis, mouth/face preview, and safe zones.",
        "8. After a separately authorized render, run machine QA and watch the result again.",
        "",
        f"Lines: {cues['line_count']}; caption text differences: {cues['caption_text_difference_count']}; "
        f"special highlights: {cues['special_highlight_count']}.",
        "Target windows below are editorial guides, not measured audio timing.",
        "",
        "| Beat | Window (s) | Line IDs |",
        "|---|---:|---|",
    ]
    for card in sheet["cards"]:
        start, end = card["planning_window_seconds"]
        ids = ", ".join(card["line_ids"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {card['beat_id']} | {start}–{end} | {ids or '—'} |")
    lines += [
        "",
        f"Missing beat review: {', '.join(sheet['missing_story_beats_for_editorial_review']) or 'none'}.",
        f"Visual/source mapping review: {', '.join(sheet['visual_cues_without_claim_ids_for_review']) or 'none'}.",
        f"Factual/source mapping review: {', '.join(sheet['factual_beats_without_claim_ids_for_review']) or 'none'}.",
        "",
        "The roughly ten-minute goal applies to a warm, prevalidated local setup with locked claims and verified cache hits. Record actual timings; do not skip gates to reach the target.",
        "",
    ]
    return "\n".join(lines)


def prepare_package(
    script_path: Path, output_dir: Path, *, root: Path = ROOT
) -> dict[str, Any]:
    shortform_path = root / "config/zundamon_news60_template.json"
    routine_path = root / "config/ymm4_news60_routine.json"
    shortform, routine = _read_object(shortform_path), _read_object(routine_path)
    validate_contracts(shortform, routine)
    script_sha256 = _file_sha256(script_path)
    document = load_script(script_path)
    rows, cues = build_exports(document)
    if _file_sha256(script_path) != script_sha256:
        raise RoutineError("script_changed_during_preparation")
    sheet = build_beat_sheet(cues, shortform, routine)

    parent = output_dir.parent.resolve()
    destination = parent / output_dir.name
    if destination.exists() or destination.is_symlink():
        raise RoutineError("output_directory_exists:use_a_new_run_directory")
    try:
        parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=parent))
    except OSError as exc:
        raise RoutineError(f"cannot_stage_package:{exc}") from exc
    try:
        write_exports(rows, cues, stage)
        (stage / BEAT_SHEET_NAME).write_text(
            json.dumps(sheet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (stage / RUN_SHEET_NAME).write_text(
            _run_sheet(cues, sheet, routine), encoding="utf-8"
        )
        files = {
            name: _file_sha256(stage / name)
            for name in sorted(EXPECTED_FILES - {MANIFEST_NAME})
        }
        manifest = {
            "schema_version": "ymm4-news60-package-v1",
            "status": "IMPORT_PACKAGE_READY_NOT_MEDIA_VALIDATED",
            "script_sha256": script_sha256,
            "shortform_policy_sha256": _file_sha256(shortform_path),
            "routine_blueprint_sha256": _file_sha256(routine_path),
            "files_sha256": files,
            "project_template": routine["local_baseline_project_name"],
            "project_template_windows_check": "REQUIRED_SEPARATELY",
            "measured_voice_timing": "NOT_YET_MEASURED",
            "source_rights_caption_and_visual_qa": "REQUIRED_SEPARATELY",
        }
        (stage / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if destination.exists() or destination.is_symlink():
            raise RoutineError("output_directory_exists:use_a_new_run_directory")
        os.rename(stage, destination)
    except (OSError, ExportError) as exc:
        raise RoutineError(f"cannot_prepare_package:{exc}") from exc
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="locked canonical dialogue JSON")
    parser.add_argument("--output-dir", required=True, type=Path, help="new directory for this run")
    args = parser.parse_args(argv)
    try:
        manifest = prepare_package(args.input, args.output_dir)
    except (RoutineError, ExportError) as exc:
        print(f"YMM4 routine preparation blocked: {exc}", file=sys.stderr)
        return 2
    print(f"YMM4 import package: {args.output_dir}")
    print(f"Status: {manifest['status']}")
    print("No audio, YMM4 project, or video was generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
