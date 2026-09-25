#!/usr/bin/env python3
"""Prepare a longform YMM4 script package; do not create media."""
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
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.export_ymm4_script import ExportError, build_exports, write_exports  # noqa: E402

PROFILE_PATH = ROOT / "config/ymm4_research_explainer_profile.json"
PROFILE_SCHEMA = "ymm4-research-explainer-profile-v1"
PACKAGE_FILES = {
    "ymm4_script.csv",
    "ymm4_review_cues.json",
    "ymm4_chapter_sheet.json",
    "ymm4_source_claim_register.json",
    "ymm4_run_sheet.md",
    "ymm4_package_manifest.json",
}
CLAIM_STATUSES = {"REVIEW_REQUIRED", "VERIFIED", "QUALIFIED", "BLOCKED"}
ASSET_RIGHTS_STATUSES = {"REVIEW_REQUIRED", "CLEARED", "NOT_USED"}


class RoutineError(ValueError):
    """The longform YMM4 import package is not safe to prepare."""


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RoutineError(f"{field}_must_be_nonempty_text")
    return value.strip()


def _object(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RoutineError(f"{field}_must_be_object")
    return value


def _unique_ids(rows: list[Mapping[str, Any]], field: str) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(rows, start=1):
        item_id = _nonempty(row.get("id"), f"{field}_{index}_id")
        if item_id in result:
            raise RoutineError(f"duplicate_{field}_id:{item_id}")
        result[item_id] = row
    return result


def _https_url(value: Any, field: str) -> str:
    url = _nonempty(value, field)
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise RoutineError(f"{field}_must_be_https_url")
    return url


def _string_id_list(value: Any, field: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise RoutineError(f"{field}_must_be_text_list")
    if nonempty and not value:
        raise RoutineError(f"{field}_must_not_be_empty")
    return [item.strip() for item in value]


def validate_profile(profile: Mapping[str, Any]) -> None:
    if profile.get("schema_version") != PROFILE_SCHEMA:
        raise RoutineError("profile_schema_drift")
    if profile.get("status") != "PREPRODUCTION_BLUEPRINT":
        raise RoutineError("profile_must_remain_preproduction")
    if profile.get("script_scaffold") != "examples/ymm4_research_explainer_script_template.json":
        raise RoutineError("profile_scaffold_path_drift")
    if profile.get("preparation_command") != "scripts/prepare_ymm4_research_explainer_package.py":
        raise RoutineError("profile_command_path_drift")
    fast = profile.get("fast_creation_goal") or {}
    if fast.get("target_wall_clock_minutes") != 10 or fast.get("measurement_required_before_claiming_target_met") is not True:
        raise RoutineError("ten_minute_target_must_remain_unmeasured_until_timed")
    safety = profile.get("safety") or {}
    required_safety = (
        "no_media_generated_by_preparation",
        "no_audio_generated_by_preparation",
        "no_network_or_paid_call_from_preparation",
        "do_not_publish_or_upload",
    )
    if any(safety.get(key) is not True for key in required_safety):
        raise RoutineError("preparation_safety_contract_drift")
    if profile.get("baseline_must_be_created_and_checked_on_windows") is not True:
        raise RoutineError("windows_baseline_gate_missing")


def validate_document(
    document: Mapping[str, Any],
) -> tuple[list[list[str]], dict[str, Any], dict[str, Any]]:
    _nonempty(document.get("title"), "title")
    _nonempty(document.get("research_cutoff_date"), "research_cutoff_date")
    duration = document.get("target_duration_minutes")
    if not isinstance(duration, list) or len(duration) != 2 or any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in duration
    ) or duration[0] > duration[1]:
        raise RoutineError("target_duration_minutes_must_be_ordered_positive_pair")

    raw_sources = document.get("source_ledger")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise RoutineError("source_ledger_must_be_nonempty_list")
    sources = [_object(row, "source") for row in raw_sources]
    source_map = _unique_ids(sources, "source")
    for source_id, source in source_map.items():
        _nonempty(source.get("title"), f"source_{source_id}_title")
        _https_url(source.get("url"), f"source_{source_id}_url")
        _nonempty(source.get("source_type"), f"source_{source_id}_source_type")
        _nonempty(source.get("checked_at"), f"source_{source_id}_checked_at")

    raw_claims = document.get("claim_ledger")
    if not isinstance(raw_claims, list) or not raw_claims:
        raise RoutineError("claim_ledger_must_be_nonempty_list")
    claims = [_object(row, "claim") for row in raw_claims]
    claim_map = _unique_ids(claims, "claim")
    for claim_id, claim in claim_map.items():
        _nonempty(claim.get("statement"), f"claim_{claim_id}_statement")
        source_ids = _string_id_list(
            claim.get("source_ids"), f"claim_{claim_id}_source_ids", nonempty=True
        )
        unknown = sorted(set(source_ids).difference(source_map))
        if unknown:
            raise RoutineError(f"claim_{claim_id}_unknown_source_ids:{','.join(unknown)}")
        status = claim.get("review_status")
        if not isinstance(status, str) or status not in CLAIM_STATUSES:
            raise RoutineError(f"claim_{claim_id}_review_status_invalid")
        if status == "BLOCKED":
            raise RoutineError(f"blocked_claim_cannot_be_packaged:{claim_id}")

    raw_visuals = document.get("visual_assets", [])
    if not isinstance(raw_visuals, list):
        raise RoutineError("visual_assets_must_be_list")
    visuals = [_object(row, "visual_asset") for row in raw_visuals]
    visual_map = _unique_ids(visuals, "visual_asset")
    for visual_id, visual in visual_map.items():
        _https_url(visual.get("source_url"), f"visual_{visual_id}_source_url")
        rights = visual.get("rights_status")
        if not isinstance(rights, str) or rights not in ASSET_RIGHTS_STATUSES:
            raise RoutineError(f"visual_{visual_id}_rights_status_invalid")
        claim_ids = _string_id_list(visual.get("claim_ids", []), f"visual_{visual_id}_claim_ids")
        unknown = sorted(set(claim_ids).difference(claim_map))
        if unknown:
            raise RoutineError(f"visual_{visual_id}_unknown_claim_ids:{','.join(unknown)}")

    raw_chapters = document.get("chapters")
    if not isinstance(raw_chapters, list) or not raw_chapters:
        raise RoutineError("chapters_must_be_nonempty_list")
    chapters = [_object(row, "chapter") for row in raw_chapters]
    chapter_map = _unique_ids(chapters, "chapter")
    flattened: list[dict[str, Any]] = []
    seen_lines: set[str] = set()
    for chapter_id, chapter in chapter_map.items():
        chapter_title = _nonempty(chapter.get("title"), f"chapter_{chapter_id}_title")
        _nonempty(chapter.get("purpose"), f"chapter_{chapter_id}_purpose")
        dialogue = chapter.get("dialogue")
        if not isinstance(dialogue, list) or not dialogue:
            raise RoutineError(f"chapter_{chapter_id}_dialogue_must_be_nonempty_list")
        for raw_line in dialogue:
            item = dict(_object(raw_line, "dialogue_line"))
            line_id = _nonempty(item.get("id"), "dialogue_line_id")
            if line_id in seen_lines:
                raise RoutineError(f"duplicate_dialogue_line_id:{line_id}")
            seen_lines.add(line_id)
            claim_ids = _string_id_list(item.get("source_claim_ids"), f"line_{line_id}_source_claim_ids")
            unknown = sorted(set(claim_ids).difference(claim_map))
            if unknown:
                raise RoutineError(f"line_{line_id}_unknown_claim_ids:{','.join(unknown)}")
            if not isinstance(item.get("claim_bearing"), bool):
                raise RoutineError(f"line_{line_id}_claim_bearing_must_be_boolean")
            if item["claim_bearing"] and not claim_ids:
                raise RoutineError(f"claim_bearing_line_requires_claim_ids:{line_id}")
            voice_text = _nonempty(item.get("voice_text"), f"line_{line_id}_voice_text")
            caption_text = _nonempty(item.get("caption_text"), f"line_{line_id}_caption_text")
            difference_reason = item.get("caption_difference_reason", "")
            if not isinstance(difference_reason, str):
                raise RoutineError(f"line_{line_id}_caption_difference_reason_must_be_text")
            if voice_text != caption_text and not difference_reason.strip():
                raise RoutineError(f"caption_difference_requires_review_reason:{line_id}")
            item["caption_difference_reason"] = difference_reason.strip()
            item["chapter_id"] = chapter_id
            item["chapter_title"] = chapter_title
            flattened.append(item)

    rows, cues_document = build_exports(
        {"title": document["title"], "dialogue": flattened},
        max_total_highlights=None,
        highlight_scope="chapter",
    )
    claim_ids_by_chapter: dict[str, list[str]] = {}
    for chapter_id in chapter_map:
        chapter_lines = [line for line in flattened if line["chapter_id"] == chapter_id]
        claim_ids_by_chapter[chapter_id] = list(dict.fromkeys(
            claim_id for line in chapter_lines for claim_id in line["source_claim_ids"]
        ))

    source_ids_by_claim = {
        claim_id: claim["source_ids"] for claim_id, claim in claim_map.items()
    }
    source_map_for_output = {
        source_id: {
            "source_id": source_id,
            "title": source["title"],
            "url": source["url"],
            "published_date": source.get("published_date"),
        }
        for source_id, source in source_map.items()
    }
    chapter_rows = []
    for chapter_id, chapter in chapter_map.items():
        chapter_claim_ids = claim_ids_by_chapter[chapter_id]
        chapter_source_ids = list(dict.fromkeys(
            source_id
            for claim_id in chapter_claim_ids
            for source_id in source_ids_by_claim[claim_id]
        ))
        chapter_lines = [line for line in flattened if line["chapter_id"] == chapter_id]
        chapter_rows.append({
            "chapter_id": chapter_id,
            "title": chapter["title"],
            "purpose": chapter["purpose"],
            "line_ids": [line["id"] for line in chapter_lines],
            "source_claim_ids": chapter_claim_ids,
            "source_ids": chapter_source_ids,
            "source_display": [source_map_for_output[source_id] for source_id in chapter_source_ids],
            "timing_status": "WAITING_FOR_MEASURED_VOICEVOX_WAV",
            "visual_rights_status": "REVIEW_REQUIRED",
        })

    claim_map_by_id = claim_map
    cue_by_id = {cue["id"]: cue for cue in cues_document["cues"]}
    for line in flattened:
        cue = cue_by_id[line["id"]]
        cue["claim_bearing"] = line["claim_bearing"]
        cue["claim_review_statuses"] = {
            claim_id: claim_map_by_id[claim_id]["review_status"]
            for claim_id in line["source_claim_ids"]
        }

    claims_requiring_review = [
        claim_id for claim_id, claim in claim_map.items()
        if claim["review_status"] == "REVIEW_REQUIRED"
    ]
    visuals_requiring_rights_review = [
        visual_id for visual_id, visual in visual_map.items()
        if visual["rights_status"] == "REVIEW_REQUIRED"
    ]
    register = {
        "chapters": chapter_rows,
        "claims": list(claims),
        "sources": list(sources),
        "visual_assets": list(visuals),
        "claims_requiring_editor_review": claims_requiring_review,
        "visual_assets_requiring_rights_review": visuals_requiring_rights_review,
        "visual_cues_without_claim_ids_for_review": [
            cue["id"] for cue in cues_document["cues"]
            if cue["visual_beat"] and not cue["source_claim_ids"]
        ],
    }
    return rows, cues_document, register


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoutineError(f"cannot_read_json:{path.name}:{exc}") from exc
    return _object(value, path.name)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_run_sheet(title: str, line_count: int, chapter_sheet: Mapping[str, Any]) -> str:
    chapters = chapter_sheet["chapters"]
    names = "\n".join(
        f"- {row['chapter_id']} — {row['title']}" for row in chapters
    )
    return (
        f"# YMM4 run sheet: {title}\n\n"
        f"Dialogue lines: {line_count}\n\n"
        "Chapter order (add exact timestamps only after measuring final WAV and timeline):\n"
        f"{names}\n\n"
        "1. Duplicate the checked Windows YMM4 baseline; keep the baseline unchanged.\n"
        "2. Import ymm4_script.csv. Built-in import contains speaker and voice text only.\n"
        "3. Use review cues and chapter sheet for caption_text, chapter cards, source IDs, visuals, and expressions.\n"
        "4. Confirm each factual line's claim IDs and show its exact supporting source title/date.\n"
        "5. Confirm selected visual assets have a cleared reuse status before rendering.\n"
        "6. Generate/reuse VOICEVOX audio after script lock, measure WAV durations, and align every caption.\n"
        "7. Render and validate each scene/chapter; perform machine, decode, and visual review.\n\n"
        "Package state: import inputs only. Audio timing, rights, YMM4 project, and final media remain unvalidated.\n"
    )


def prepare_package(input_path: Path, output_dir: Path) -> Mapping[str, Any]:
    input_path = input_path.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise RoutineError("output_directory_exists:choose_a_new_directory")
    profile = _read_json(PROFILE_PATH)
    validate_profile(profile)
    document = _read_json(input_path)
    rows, cues, register_data = validate_document(document)
    chapter_sheet = {
        "schema_version": "ymm4-longform-chapter-sheet-v1",
        "title": document["title"],
        "timing_status": "WAITING_FOR_MEASURED_VOICEVOX_WAV",
        **register_data,
    }
    source_register = {
        "schema_version": "ymm4-longform-source-claim-register-v1",
        "title": document["title"],
        "claim_verification": "NOT_PERFORMED_BY_PACKAGE_BUILDER",
        "claim_review_statuses_are_editor_assertions": True,
        **register_data,
    }

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        write_exports(rows, cues, temporary)
        _write_json(temporary / "ymm4_chapter_sheet.json", chapter_sheet)
        _write_json(temporary / "ymm4_source_claim_register.json", source_register)
        (temporary / "ymm4_run_sheet.md").write_text(
            _build_run_sheet(document["title"], len(rows), chapter_sheet),
            encoding="utf-8",
        )
        expected_without_manifest = PACKAGE_FILES - {"ymm4_package_manifest.json"}
        actual = {path.name for path in temporary.iterdir() if path.is_file()}
        if actual != expected_without_manifest:
            raise RoutineError("package_file_set_drift")
        manifest = {
            "schema_version": "ymm4-longform-package-manifest-v1",
            "status": "IMPORT_PACKAGE_READY_NOT_MEDIA_VALIDATED",
            "title": document["title"],
            "dialogue_line_count": len(rows),
            "chapter_count": len(chapter_sheet["chapters"]),
            "source_verification_status": "NOT_PERFORMED_BY_PACKAGE_BUILDER",
            "audio_timing_status": "NOT_YET_MEASURED",
            "visual_rights_status": "REVIEW_REQUIRED",
            "windows_baseline_status": "REQUIRED_SEPARATELY",
            "files_sha256": {
                path.name: _sha256(path)
                for path in sorted(temporary.iterdir())
                if path.is_file()
            },
            "input_sha256": _sha256(input_path),
            "profile_sha256": _sha256(PROFILE_PATH),
            "no_audio_project_or_video_generated": True,
        }
        if set(manifest["files_sha256"]) != expected_without_manifest:
            raise RoutineError("manifest_file_set_drift")
        _write_json(temporary / "ymm4_package_manifest.json", manifest)
        if {path.name for path in temporary.iterdir() if path.is_file()} != PACKAGE_FILES:
            raise RoutineError("package_file_set_drift")
        os.replace(temporary, output_dir)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare a reusable YMM4 longform script and review package; no media is generated."
    )
    parser.add_argument("--input", required=True, type=Path, help="canonical longform dialogue JSON")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = prepare_package(args.input, args.output_dir)
    except (RoutineError, ExportError) as exc:
        print(f"YMM4 preparation blocked: {exc}", file=sys.stderr)
        return 2
    print("YMM4 preparation: PASS")
    print(f"Dialogue lines: {manifest['dialogue_line_count']}")
    print(f"Chapters: {manifest['chapter_count']}")
    print(f"Import package: {args.output_dir.resolve()}")
    print("No audio, YMM4 project, or video was generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
