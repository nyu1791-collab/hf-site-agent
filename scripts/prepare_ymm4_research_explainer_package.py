#!/usr/bin/env python3
"""Prepare a longform YMM4 script package; do not create media."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import wave
from datetime import date, datetime
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
    "ymm4_voice_credit_checklist.json",
    "ymm4_run_sheet.md",
    "ymm4_package_manifest.json",
}
OPTIONAL_PACKAGE_FILES = {"ymm4_caption_timeline.srt", "ymm4_wav_timing_manifest.json"}
DEFAULT_INTER_LINE_PAUSE_MS = 180
CLAIM_STATUSES = {"REVIEW_REQUIRED", "VERIFIED", "QUALIFIED", "BLOCKED"}
SOURCE_TYPES = {"PRIMARY_OFFICIAL", "PRIMARY_RESEARCH", "SECONDARY_REPUTABLE", "DATASET_OR_METHOD", "OTHER"}
ASSET_RIGHTS_STATUSES = {"REVIEW_REQUIRED", "CLEARED", "NOT_USED"}
VOICE_CREDIT_TEXT = {
    "ずんだもん": "VOICEVOX:ずんだもん",
    "四国めたん": "VOICEVOX:四国めたん",
}


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
    normalized = [item.strip() for item in value]
    if len(normalized) != len(set(normalized)):
        raise RoutineError(f"{field}_must_not_contain_duplicates")
    return normalized


def _iso_date(value: Any, field: str) -> date:
    text = _nonempty(value, field)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise RoutineError(f"{field}_must_be_iso_date")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise RoutineError(f"{field}_must_be_iso_date") from exc


def _aware_timestamp(value: Any, field: str) -> datetime:
    text = _nonempty(value, field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RoutineError(f"{field}_must_be_timezone_aware_iso_timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RoutineError(f"{field}_must_be_timezone_aware_iso_timestamp")
    return parsed


def _normalize_caption(value: str) -> str:
    return unicodedata.normalize("NFKC", "".join(str(value).split()))


def _srt_time(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _chapter_visual_status(asset_ids: list[str], asset_map: Mapping[str, Mapping[str, Any]]) -> str:
    if not asset_ids:
        return "NOT_SELECTED"
    if any(asset_map[asset_id]["rights_status"] != "CLEARED" for asset_id in asset_ids):
        return "REVIEW_REQUIRED"
    return "CLEARED_FOR_LISTED_ASSETS"


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
    timed_import = profile.get("timed_caption_import") or {}
    if (
        timed_import.get("output_file") != "ymm4_caption_timeline.srt"
        or timed_import.get("generated_only_when_per_line_wav_files_are_supplied") is not True
        or timed_import.get("requires_one_unique_wav_basename_per_dialogue_line") is not True
        or timed_import.get("uses_measured_wav_duration_not_character_count") is not True
        or timed_import.get("srt_import_creates_text_items_only") is not True
        or timed_import.get("csv_remains_voice_text_import") is not True
        or timed_import.get("wav_files_are_referenced_not_copied") is not True
        or timed_import.get("windows_preview_required_before_reuse") is not True
    ):
        raise RoutineError("timed_caption_import_preview_gate_missing")
    configured_files = profile.get("package_files")
    if not isinstance(configured_files, list) or any(not isinstance(item, str) for item in configured_files):
        raise RoutineError("profile_package_files_must_be_text_list")
    configured_names = [item.split(" ", 1)[0] for item in configured_files]
    if len(configured_names) != len(set(configured_names)) or set(configured_names) != PACKAGE_FILES | OPTIONAL_PACKAGE_FILES:
        raise RoutineError("profile_package_file_set_drift")
    research = profile.get("claim_review_contract") or {}
    if set(research.get("source_type_values") or []) != SOURCE_TYPES:
        raise RoutineError("source_type_contract_drift")
    if set(research.get("per_source_support_required_fields_for_verified_or_qualified_claims") or []) != {"source_id", "locator", "support_summary"}:
        raise RoutineError("claim_source_support_contract_drift")
    if set(research.get("verified_or_qualified_required_fields") or []) != {"reviewed_by", "review_method", "reviewed_at"}:
        raise RoutineError("claim_review_contract_drift")
    if research.get("qualified_claim_also_requires_qualification_note") is not True or research.get("source_dates_must_not_exceed_research_cutoff") is not True:
        raise RoutineError("claim_qualification_or_freshness_contract_drift")
    if research.get("builder_fetches_or_independently_verifies_sources") is not False:
        raise RoutineError("builder_source_verification_claim_drift")
    credits = (profile.get("voice_and_rights") or {}).get("required_credits") or []
    if set(credits) != set(VOICE_CREDIT_TEXT.values()):
        raise RoutineError("voice_credit_contract_drift")
    rights_fields = profile.get("visual_asset_provenance_required_fields") or []
    if set(rights_fields) != {"source_page", "asset_locator", "license_or_public_domain_state", "scene_or_claim_mapping", "retrieval_or_verification_timestamp"}:
        raise RoutineError("visual_provenance_contract_drift")
    clearance_fields = profile.get("visual_rights_clearance_required_fields") or []
    if set(clearance_fields) != {
        "rights_basis_locator", "reuse_conditions_summary", "rights_review_method",
        "rights_reviewed_by", "rights_reviewed_at",
    }:
        raise RoutineError("visual_rights_clearance_contract_drift")


def validate_document(
    document: Mapping[str, Any],
) -> tuple[list[list[str]], dict[str, Any], dict[str, Any]]:
    title = _nonempty(document.get("title"), "title")
    cutoff = _iso_date(document.get("research_cutoff_date"), "research_cutoff_date")
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
        source_type = source.get("source_type")
        if not isinstance(source_type, str) or source_type not in SOURCE_TYPES:
            raise RoutineError(f"source_{source_id}_source_type_invalid")
        if source_type == "OTHER":
            _nonempty(source.get("source_type_note"), f"source_{source_id}_source_type_note")
        checked_at = _iso_date(source.get("checked_at"), f"source_{source_id}_checked_at")
        if checked_at > cutoff:
            raise RoutineError(f"source_{source_id}_checked_after_research_cutoff")
        published = source.get("published_date")
        if published is not None:
            published_at = _iso_date(published, f"source_{source_id}_published_date")
            if published_at > cutoff:
                raise RoutineError(f"source_{source_id}_published_after_research_cutoff")

    pronunciation_dictionary = document.get("pronunciation_dictionary", [])
    if not isinstance(pronunciation_dictionary, list):
        raise RoutineError("pronunciation_dictionary_must_be_list")
    dictionary = []
    seen_readings: set[str] = set()
    for index, raw_entry in enumerate(pronunciation_dictionary, start=1):
        entry = _object(raw_entry, f"pronunciation_dictionary_{index}")
        row = {
            "surface_term": _nonempty(entry.get("surface_term"), f"pronunciation_dictionary_{index}_surface_term"),
            "voice_reading": _nonempty(entry.get("voice_reading"), f"pronunciation_dictionary_{index}_voice_reading"),
            "caption_spelling": _nonempty(entry.get("caption_spelling"), f"pronunciation_dictionary_{index}_caption_spelling"),
            "revision": _nonempty(entry.get("revision"), f"pronunciation_dictionary_{index}_revision"),
        }
        if _normalize_caption(row["surface_term"]) != _normalize_caption(row["caption_spelling"]):
            raise RoutineError(f"pronunciation_dictionary_{index}_surface_term_must_equal_caption_spelling")
        if any("\n" in row[field] or "\r" in row[field] or len(row[field]) > 80 for field in ("surface_term", "voice_reading", "caption_spelling")):
            raise RoutineError(f"pronunciation_dictionary_{index}_term_must_be_single_line_and_at_most_80_characters")
        if row["voice_reading"] in seen_readings:
            raise RoutineError(f"duplicate_pronunciation_dictionary_reading:{row['voice_reading']}")
        seen_readings.add(row["voice_reading"])
        dictionary.append(row)
    dictionary.sort(key=lambda item: len(item["voice_reading"]), reverse=True)

    raw_claims = document.get("claim_ledger")
    if not isinstance(raw_claims, list) or not raw_claims:
        raise RoutineError("claim_ledger_must_be_nonempty_list")
    claims = [_object(row, "claim") for row in raw_claims]
    claim_map = _unique_ids(claims, "claim")
    support_by_claim: dict[str, dict[str, Mapping[str, str]]] = {}
    missing_support_claims: list[str] = []
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

        raw_support = claim.get("source_support", [])
        if not isinstance(raw_support, list):
            raise RoutineError(f"claim_{claim_id}_source_support_must_be_list")
        support_map: dict[str, Mapping[str, str]] = {}
        seen_support_ids: set[str] = set()
        for index, raw_item in enumerate(raw_support, start=1):
            item = _object(raw_item, f"claim_{claim_id}_source_support_{index}")
            source_id = _nonempty(item.get("source_id"), f"claim_{claim_id}_support_{index}_source_id")
            if source_id not in source_ids:
                raise RoutineError(f"claim_{claim_id}_support_source_not_in_claim_source_ids:{source_id}")
            if source_id in seen_support_ids:
                raise RoutineError(f"claim_{claim_id}_duplicate_source_support:{source_id}")
            seen_support_ids.add(source_id)
            locator = item.get("locator")
            summary = item.get("support_summary")
            if locator is None and summary is None and status == "REVIEW_REQUIRED":
                continue
            locator = _nonempty(locator, f"claim_{claim_id}_support_{source_id}_locator")
            summary = _nonempty(summary, f"claim_{claim_id}_support_{source_id}_summary")
            support_map[source_id] = {"locator": locator, "support_summary": summary}
        support_by_claim[claim_id] = support_map
        missing = sorted(set(source_ids).difference(support_map))
        if missing:
            missing_support_claims.append(claim_id)
        if status in {"VERIFIED", "QUALIFIED"}:
            if missing:
                raise RoutineError(
                    f"claim_{claim_id}_verified_requires_source_locator_and_summary:{','.join(missing)}"
                )
            _nonempty(claim.get("reviewed_by"), f"claim_{claim_id}_reviewed_by")
            _nonempty(claim.get("review_method"), f"claim_{claim_id}_review_method")
            reviewed_at = _iso_date(claim.get("reviewed_at"), f"claim_{claim_id}_reviewed_at")
            if reviewed_at > cutoff:
                raise RoutineError(f"claim_{claim_id}_reviewed_after_research_cutoff")
        if status == "QUALIFIED":
            _nonempty(claim.get("qualification_note"), f"claim_{claim_id}_qualification_note")

    raw_chapters = document.get("chapters")
    if not isinstance(raw_chapters, list) or not raw_chapters:
        raise RoutineError("chapters_must_be_nonempty_list")
    chapters = [_object(row, "chapter") for row in raw_chapters]
    chapter_map = _unique_ids(chapters, "chapter")
    flattened: list[dict[str, Any]] = []
    seen_lines: set[str] = set()
    raw_visuals = document.get("visual_assets", [])
    if not isinstance(raw_visuals, list):
        raise RoutineError("visual_assets_must_be_list")
    visuals = [_object(row, "visual_asset") for row in raw_visuals]
    visual_map = _unique_ids(visuals, "visual_asset")

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
            expected_caption = voice_text
            for entry in dictionary:
                expected_caption = expected_caption.replace(
                    entry["voice_reading"], entry["caption_spelling"]
                )
            if _normalize_caption(expected_caption) != _normalize_caption(caption_text):
                raise RoutineError(f"caption_content_mismatch_or_omission:{line_id}")
            difference_reason = item.get("caption_difference_reason", "")
            if not isinstance(difference_reason, str):
                raise RoutineError(f"line_{line_id}_caption_difference_reason_must_be_text")
            if _normalize_caption(voice_text) != _normalize_caption(caption_text) and not difference_reason.strip():
                raise RoutineError(f"caption_dictionary_difference_requires_reason:{line_id}")
            item["caption_difference_reason"] = difference_reason.strip()

            visual_asset_ids = _string_id_list(item.get("visual_asset_ids", []), f"line_{line_id}_visual_asset_ids")
            item["visual_asset_ids"] = visual_asset_ids
            audio_filename = item.get("voice_audio_file")
            if audio_filename is not None:
                audio_filename = _nonempty(audio_filename, f"line_{line_id}_voice_audio_file")
            item["voice_audio_file"] = audio_filename
            pause = item.get("pause_after_ms", DEFAULT_INTER_LINE_PAUSE_MS)
            if isinstance(pause, bool) or not isinstance(pause, int) or not 0 <= pause <= 5000:
                raise RoutineError(f"line_{line_id}_pause_after_ms_must_be_0_to_5000_integer")
            item["pause_after_ms"] = pause
            item["chapter_id"] = chapter_id
            item["chapter_title"] = chapter_title
            flattened.append(item)

    line_map = {line["id"]: line for line in flattened}
    used_visual_ids: set[str] = set()
    for visual_id, visual in visual_map.items():
        rights = visual.get("rights_status")
        if not isinstance(rights, str) or rights not in ASSET_RIGHTS_STATUSES:
            raise RoutineError(f"visual_{visual_id}_rights_status_invalid")
        linked_by_lines = [line["id"] for line in flattened if visual_id in line["visual_asset_ids"]]
        if rights == "NOT_USED":
            if linked_by_lines:
                raise RoutineError(f"not_used_visual_is_linked:{visual_id}")
            continue
        _https_url(visual.get("source_page"), f"visual_{visual_id}_source_page")
        _nonempty(visual.get("asset_locator"), f"visual_{visual_id}_asset_locator")
        license_state = _nonempty(
            visual.get("license_or_public_domain_state"),
            f"visual_{visual_id}_license_or_public_domain_state",
        )
        retrieved_at = _aware_timestamp(
            visual.get("retrieval_or_verification_timestamp"),
            f"visual_{visual_id}_retrieval_or_verification_timestamp",
        )
        if retrieved_at.date() > cutoff:
            raise RoutineError(f"visual_{visual_id}_retrieved_after_research_cutoff")
        mapping = _object(visual.get("scene_or_claim_mapping"), f"visual_{visual_id}_scene_or_claim_mapping")
        mapped_lines = _string_id_list(mapping.get("line_ids"), f"visual_{visual_id}_line_ids", nonempty=True)
        mapped_chapters = _string_id_list(mapping.get("chapter_ids", []), f"visual_{visual_id}_chapter_ids")
        mapped_claims = _string_id_list(mapping.get("claim_ids", []), f"visual_{visual_id}_claim_ids")
        unknown_lines = sorted(set(mapped_lines).difference(line_map))
        unknown_chapters = sorted(set(mapped_chapters).difference(chapter_map))
        unknown_claims = sorted(set(mapped_claims).difference(claim_map))
        if unknown_lines:
            raise RoutineError(f"visual_{visual_id}_unknown_line_ids:{','.join(unknown_lines)}")
        if unknown_chapters:
            raise RoutineError(f"visual_{visual_id}_unknown_chapter_ids:{','.join(unknown_chapters)}")
        if unknown_claims:
            raise RoutineError(f"visual_{visual_id}_unknown_claim_ids:{','.join(unknown_claims)}")
        if set(linked_by_lines) != set(mapped_lines):
            raise RoutineError(f"visual_{visual_id}_line_mapping_mismatch")
        for line_id in mapped_lines:
            line = line_map[line_id]
            if visual_id not in line["visual_asset_ids"]:
                raise RoutineError(f"visual_{visual_id}_not_referenced_by_line:{line_id}")
            if line["claim_bearing"] and not set(mapped_claims).intersection(line["source_claim_ids"]):
                raise RoutineError(f"visual_{visual_id}_claim_mapping_missing:{line_id}")
        if rights == "CLEARED":
            if license_state.upper() in {"UNKNOWN", "UNKNOWN_PENDING_REVIEW", "UNVERIFIED"}:
                raise RoutineError(f"cleared_visual_{visual_id}_has_unknown_license_state")
            normalized_license = re.sub(r"[^A-Z0-9]+", "_", license_state.upper()).strip("_")
            if normalized_license in {
                "ALL_RIGHTS_RESERVED", "NONCOMMERCIAL_ONLY", "NO_DERIVATIVES",
                "EDITORIAL_ONLY_UNVERIFIED", "NC", "CC_BY_NC", "CC_BY_NC_SA",
                "CC_BY_NC_ND", "CC_BY_ND",
            } or any(token in normalized_license for token in ("NONCOMMERCIAL", "NO_DERIVATIVES")) or "_NC_" in f"_{normalized_license}_" or "_ND_" in f"_{normalized_license}_":
                raise RoutineError(f"cleared_visual_{visual_id}_has_blocked_license_state")
            rights_basis = _nonempty(
                visual.get("rights_basis_locator"), f"visual_{visual_id}_rights_basis_locator"
            )
            if normalized_license == "WRITTEN_PERMISSION":
                if not rights_basis.startswith("PERMISSION_RECORD:") and not rights_basis.startswith("https://"):
                    raise RoutineError(f"cleared_visual_{visual_id}_permission_basis_must_be_traceable")
            else:
                _https_url(rights_basis, f"visual_{visual_id}_rights_basis_locator")
            _nonempty(
                visual.get("reuse_conditions_summary"), f"visual_{visual_id}_reuse_conditions_summary"
            )
            _nonempty(
                visual.get("rights_review_method"), f"visual_{visual_id}_rights_review_method"
            )
            _nonempty(visual.get("rights_reviewed_by"), f"visual_{visual_id}_rights_reviewed_by")
            rights_reviewed_at = _iso_date(visual.get("rights_reviewed_at"), f"visual_{visual_id}_rights_reviewed_at")
            if rights_reviewed_at > cutoff:
                raise RoutineError(f"visual_{visual_id}_rights_reviewed_after_research_cutoff")
        used_visual_ids.add(visual_id)
    for line in flattened:
        unknown_visuals = sorted(set(line["visual_asset_ids"]).difference(visual_map))
        if unknown_visuals:
            raise RoutineError(f"line_{line['id']}_unknown_visual_asset_ids:{','.join(unknown_visuals)}")
        for visual_id in line["visual_asset_ids"]:
            if visual_map[visual_id]["rights_status"] == "NOT_USED":
                raise RoutineError(f"line_{line['id']}_references_not_used_visual:{visual_id}")
    for visual_id, visual in visual_map.items():
        if visual.get("rights_status") == "CLEARED" and visual_id not in used_visual_ids:
            raise RoutineError(f"cleared_visual_{visual_id}_is_not_used")

    rows, cues_document = build_exports(
        {"title": title, "dialogue": flattened},
        max_total_highlights=None,
        highlight_scope="chapter",
    )
    claim_ids_by_chapter: dict[str, list[str]] = {}
    source_map_for_output = {
        source_id: {
            "source_id": source_id,
            "title": source["title"],
            "url": source["url"],
            "source_type": source["source_type"],
            "published_date": source.get("published_date"),
            "checked_at": source["checked_at"],
        }
        for source_id, source in source_map.items()
    }
    support_display_by_claim: dict[str, list[dict[str, Any]]] = {}
    for claim_id, claim in claim_map.items():
        support = support_by_claim[claim_id]
        support_display_by_claim[claim_id] = [
            {
                **source_map_for_output[source_id],
                "claim_id": claim_id,
                "claim_statement": claim["statement"],
                "locator": support.get(source_id, {}).get("locator"),
                "support_summary": support.get(source_id, {}).get("support_summary"),
                "review_status": claim["review_status"],
            }
            for source_id in claim["source_ids"]
        ]

    for chapter_id in chapter_map:
        chapter_lines = [line for line in flattened if line["chapter_id"] == chapter_id]
        claim_ids_by_chapter[chapter_id] = list(dict.fromkeys(
            claim_id for line in chapter_lines for claim_id in line["source_claim_ids"]
        ))
    chapter_rows = []
    for chapter_id, chapter in chapter_map.items():
        chapter_lines = [line for line in flattened if line["chapter_id"] == chapter_id]
        chapter_claim_ids = claim_ids_by_chapter[chapter_id]
        chapter_source_display = [
            item
            for claim_id in chapter_claim_ids
            for item in support_display_by_claim[claim_id]
        ]
        chapter_visual_ids = list(dict.fromkeys(
            visual_id for line in chapter_lines for visual_id in line["visual_asset_ids"]
        ))
        chapter_visual_status = _chapter_visual_status(chapter_visual_ids, visual_map)
        chapter_rows.append({
            "chapter_id": chapter_id,
            "title": chapter["title"],
            "purpose": chapter["purpose"],
            "line_ids": [line["id"] for line in chapter_lines],
            "source_claim_ids": chapter_claim_ids,
            "source_ids": list(dict.fromkeys(
                source_id for claim_id in chapter_claim_ids for source_id in claim_map[claim_id]["source_ids"]
            )),
            "source_display": chapter_source_display,
            "visual_asset_ids": chapter_visual_ids,
            "visual_rights_status": chapter_visual_status,
            "timing_status": "WAITING_FOR_MEASURED_VOICEVOX_WAV",
        })

    cue_by_id = {cue["id"]: cue for cue in cues_document["cues"]}
    for line in flattened:
        cue = cue_by_id[line["id"]]
        cue["caption_matches_voice_text_normalized"] = (
            _normalize_caption(line["voice_text"]) == _normalize_caption(line["caption_text"])
        )
        cue["claim_bearing"] = line["claim_bearing"]
        cue["claim_review_statuses"] = {
            claim_id: claim_map[claim_id]["review_status"]
            for claim_id in line["source_claim_ids"]
        }
        cue["source_ids"] = list(dict.fromkeys(
            source_id
            for claim_id in line["source_claim_ids"]
            for source_id in claim_map[claim_id]["source_ids"]
        ))
        cue["source_display"] = [
            item for claim_id in line["source_claim_ids"]
            for item in support_display_by_claim[claim_id]
        ]
        cue["visual_asset_ids"] = list(line["visual_asset_ids"])
        cue["caption_content_status"] = "FULL_SPOKEN_TEXT_MATCH_AFTER_DICTIONARY_AND_UNICODE_NORMALIZATION"
        cue["caption_matches_after_dictionary"] = True
        cue["caption_match_method"] = "VOICE_READING_TO_CAPTION_SPELLING_DICTIONARY_WITH_NFKC_AND_WHITESPACE_NORMALIZATION"
        cue["voice_audio_file"] = line.get("voice_audio_file")
        cue["pause_after_ms"] = line["pause_after_ms"]

    selected_visual_ids = set(visual_id for line in flattened for visual_id in line["visual_asset_ids"])
    rights_review_ids = sorted(
        visual_id for visual_id in selected_visual_ids
        if visual_map[visual_id]["rights_status"] != "CLEARED"
    )
    claim_bearing_without_visuals = [
        line["id"] for line in flattened
        if line["claim_bearing"] and not line["visual_asset_ids"]
    ]
    register = {
        "research_cutoff_date": cutoff.isoformat(),
        "target_duration_minutes": list(duration),
        "chapters": chapter_rows,
        "claims": list(claim_map.values()),
        "sources": list(source_map.values()),
        "visual_assets": list(visual_map.values()),
        "claim_verification": "NOT_PERFORMED_BY_PACKAGE_BUILDER",
        "claim_review_statuses_are_editor_assertions": True,
        "visual_rights_statuses_are_editor_assertions": True,
        "claims_requiring_editor_review": [
            claim_id for claim_id, claim in claim_map.items()
            if claim["review_status"] == "REVIEW_REQUIRED"
        ],
        "claims_missing_source_locator_or_summary": sorted(missing_support_claims),
        "visual_assets_requiring_rights_review": rights_review_ids,
        "claim_bearing_lines_without_linked_visual_asset": claim_bearing_without_visuals,
        "unreferenced_visual_asset_ids": sorted(set(visual_map).difference(selected_visual_ids)),
    }
    visual_rights_status = (
        "NOT_SELECTED" if not selected_visual_ids
        else "REVIEW_REQUIRED" if rights_review_ids
        else "CLEARED_FOR_LISTED_ASSETS"
    )
    register["visual_rights_status"] = visual_rights_status
    cues_document["caption_contract"] = "FULL_SPOKEN_TEXT"
    cues_document["research_cutoff_date"] = cutoff.isoformat()
    cues_document["source_verification_status"] = "NOT_PERFORMED_BY_PACKAGE_BUILDER"
    cues_document["claim_review_statuses_are_editor_assertions"] = True
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


def _build_voice_credit_checklist(cues_document: Mapping[str, Any]) -> dict[str, Any]:
    speakers = sorted({cue["speaker"] for cue in cues_document["cues"]})
    credit_text = VOICE_CREDIT_TEXT
    terms_url = {
        "ずんだもん": "https://zunko.jp/con_ongen_kiyaku.html",
        "四国めたん": "https://zunko.jp/con_ongen_kiyaku.html",
    }
    return {
        "schema_version": "ymm4-voice-credit-checklist-v1",
        "status": "REVIEW_REQUIRED",
        "usage_terms_reviewed_by_package_builder": False,
        "voicevox_software_terms_url": "https://voicevox.hiroshiba.jp/term/",
        "voice_credits": [
            {
                "speaker": speaker,
                "required_for_this_script": speaker in speakers,
                "exact_credit_text": credit_text[speaker],
                "terms_url": terms_url[speaker],
                "placement": "VIDEO_OR_DESCRIPTION_AS_REQUIRED_BY_CURRENT_TERMS",
                "status": "REVIEW_REQUIRED" if speaker in speakers else "NOT_USED",
            }
            for speaker in credit_text
        ],
        "manual_checks": [
            "Confirm each used voice library's current terms, including any external illustration/character rights.",
            "Place the exact required credit where the applicable terms require it before release.",
            "Keep voice-library terms separate from standing-art, background, and source-image rights.",
        ],
    }


def _build_timed_caption_outputs(
    cues_document: dict[str, Any], audio_dir: Path | None
) -> tuple[str, dict[str, Any]] | None:
    cues = cues_document["cues"]
    referenced = [cue.get("voice_audio_file") for cue in cues]
    if audio_dir is None:
        if any(referenced):
            raise RoutineError("voice_audio_directory_required_when_line_audio_is_named")
        return None
    try:
        resolved_dir = audio_dir.expanduser().resolve(strict=True)
    except OSError as exc:
        raise RoutineError(f"voice_audio_directory_not_found:{audio_dir}") from exc
    if not resolved_dir.is_dir():
        raise RoutineError("voice_audio_directory_must_be_directory")
    if not cues or any(not value for value in referenced):
        raise RoutineError("every_dialogue_line_requires_one_voice_audio_file_for_timing")

    srt_blocks: list[str] = []
    entries: list[dict[str, Any]] = []
    used_names: set[str] = set()
    cursor_ms = 0
    for cue in cues:
        filename = _nonempty(cue.get("voice_audio_file"), f"line_{cue['id']}_voice_audio_file")
        if filename != Path(filename).name or "/" in filename or "\\" in filename:
            raise RoutineError(f"line_{cue['id']}_voice_audio_file_must_be_a_basename")
        if Path(filename).suffix.lower() != ".wav":
            raise RoutineError(f"line_{cue['id']}_voice_audio_file_must_be_wav")
        normalized_name = filename.casefold()
        if normalized_name in used_names:
            raise RoutineError(f"voice_audio_file_reused:{filename}")
        used_names.add(normalized_name)
        path = resolved_dir / filename
        if path.is_symlink():
            raise RoutineError(f"voice_audio_symlink_not_allowed:{filename}")
        try:
            resolved_path = path.resolve(strict=True)
            if resolved_path.parent != resolved_dir or not resolved_path.is_file():
                raise RoutineError(f"voice_audio_file_outside_directory_or_missing:{filename}")
            with wave.open(str(resolved_path), "rb") as wav:
                frame_count = wav.getnframes()
                sample_rate = wav.getframerate()
                channels = wav.getnchannels()
                sample_width = wav.getsampwidth()
                compression = wav.getcomptype()
        except (OSError, wave.Error, EOFError) as exc:
            raise RoutineError(f"invalid_voice_audio_wav:{filename}:{exc}") from exc
        if frame_count <= 0 or sample_rate <= 0 or channels <= 0 or sample_width <= 0 or compression != "NONE":
            raise RoutineError(f"voice_audio_wav_has_invalid_format_or_empty_frames:{filename}")
        duration_ms = math.ceil(frame_count * 1000 / sample_rate)
        if duration_ms <= 0:
            raise RoutineError(f"voice_audio_wav_has_zero_duration:{filename}")
        start_ms = cursor_ms
        end_ms = start_ms + duration_ms
        pause_ms = cue["pause_after_ms"]
        cue["start_ms"] = start_ms
        cue["end_ms"] = end_ms
        cue["measured_audio_duration_ms"] = duration_ms
        cue["caption_timing_status"] = "MEASURED_FROM_WAV_FRAME_COUNT_WINDOWS_PREVIEW_REQUIRED"
        srt_blocks.append(
            f"{len(srt_blocks) + 1}\n{_srt_time(start_ms)} --> {_srt_time(end_ms)}\n{cue['caption_text']}"
        )
        entries.append({
            "line_id": cue["id"],
            "chapter_id": cue["chapter_id"],
            "voice_audio_file": filename,
            "sha256": _sha256(resolved_path),
            "frame_count": frame_count,
            "sample_rate_hz": sample_rate,
            "channels": channels,
            "sample_width_bytes": sample_width,
            "duration_ms": duration_ms,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "pause_after_ms": pause_ms,
            "caption_text": cue["caption_text"],
        })
        cursor_ms = end_ms + pause_ms
    unused_wavs = sorted(
        path.name for path in resolved_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".wav" and path.name.casefold() not in used_names
    )
    timing_manifest = {
        "schema_version": "ymm4-wav-caption-timing-manifest-v1",
        "status": "MEASURED_FROM_WAV_FRAME_COUNT_WINDOWS_PREVIEW_REQUIRED",
        "audio_files_are_referenced_in_place_not_copied": True,
        "timing_method": "ceil(frame_count * 1000 / sample_rate_hz); configured pause_after_ms between lines",
        "unused_wav_files": unused_wavs,
        "entries": entries,
    }
    return "\n\n".join(srt_blocks) + "\n", timing_manifest


def _build_run_sheet(
    title: str,
    line_count: int,
    chapter_sheet: Mapping[str, Any],
    *,
    timed_captions_available: bool,
) -> str:
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
        "2. Import ymm4_script.csv for speaker/voice items. The built-in CSV path carries only those two columns.\n"
        "3. Use review cues for full caption text, chapter cards, source IDs, visuals, expressions, and emphasis.\n"
        + ("4. Import ymm4_caption_timeline.srt as text items, then check offsets, wrapping, safe areas, and overlaps in Windows YMM4.\n" if timed_captions_available else "4. Generate/reuse VOICEVOX audio and measure one WAV per line before creating timed captions.\n")
        + "5. Check every factual line against its source locator and summarize the evidence that supports it.\n"
        + "6. Review image, standing-art, voice, and background terms separately; complete the included credit checklist.\n"
        + "7. Render and validate each scene/chapter only after the later production run is authorized.\n\n"
        + "Warm-run stopwatch (start only after research, script, baseline, voice settings, and cleared-asset cache are ready):\n\n"
        + "| Stage | Minutes | Notes |\n|---|---:|---|\n"
        + "| VOICEVOX generation and audio QA |  |  |\n| WAV timing and caption check |  |  |\n| Import and timeline assembly |  |  |\n| Render |  |  |\n| Machine and decode QA |  |  |\n| Visual and phone-size review |  |  |\n| Total |  |  |\n\n"
        + "Ten minutes is a measured warm-run target: log three consecutive clean runs before claiming it is met.\n\n"
        + "Package state: import inputs only. Rights, Windows YMM4 preview, render, and final media remain unvalidated.\n"
    )


def prepare_package(
    input_path: Path,
    output_dir: Path,
    voice_audio_dir: Path | None = None,
) -> Mapping[str, Any]:
    input_path = input_path.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise RoutineError("output_directory_exists:choose_a_new_directory")
    profile = _read_json(PROFILE_PATH)
    validate_profile(profile)
    document = _read_json(input_path)
    rows, cues, register_data = validate_document(document)
    timed_outputs = _build_timed_caption_outputs(cues, voice_audio_dir)
    timing_manifest = timed_outputs[1] if timed_outputs else None
    timed_captions_available = timing_manifest is not None
    if timing_manifest:
        timing_by_line = {entry["line_id"]: entry for entry in timing_manifest["entries"]}
        for chapter in register_data["chapters"]:
            line_timings = [timing_by_line[line_id] for line_id in chapter["line_ids"]]
            chapter["start_ms"] = line_timings[0]["start_ms"]
            chapter["end_ms"] = line_timings[-1]["end_ms"]
            chapter["timing_status"] = "MEASURED_FROM_WAV_FRAME_COUNT_WINDOWS_PREVIEW_REQUIRED"
        register_data["audio_timing_status"] = timing_manifest["status"]
    else:
        register_data["audio_timing_status"] = "NOT_YET_MEASURED"
    voice_credit_checklist = _build_voice_credit_checklist(cues)
    chapter_sheet = {
        "schema_version": "ymm4-longform-chapter-sheet-v1",
        "title": document["title"],
        "timing_status": timing_manifest["status"] if timing_manifest else "WAITING_FOR_MEASURED_VOICEVOX_WAV",
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
        _write_json(temporary / "ymm4_voice_credit_checklist.json", voice_credit_checklist)
        _write_json(temporary / "ymm4_chapter_sheet.json", chapter_sheet)
        _write_json(temporary / "ymm4_source_claim_register.json", source_register)
        if timed_outputs:
            (temporary / "ymm4_caption_timeline.srt").write_text(timed_outputs[0], encoding="utf-8-sig")
            _write_json(temporary / "ymm4_wav_timing_manifest.json", timing_manifest)
        (temporary / "ymm4_run_sheet.md").write_text(
            _build_run_sheet(
                document["title"],
                len(rows),
                chapter_sheet,
                timed_captions_available=timed_captions_available,
            ),
            encoding="utf-8",
        )
        expected_without_manifest = PACKAGE_FILES - {"ymm4_package_manifest.json"}
        if timed_outputs:
            expected_without_manifest |= OPTIONAL_PACKAGE_FILES
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
            "audio_timing_status": timing_manifest["status"] if timing_manifest else "NOT_YET_MEASURED",
            "visual_rights_status": register_data["visual_rights_status"],
            "voice_credit_status": voice_credit_checklist["status"],
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
        expected_final = PACKAGE_FILES | (OPTIONAL_PACKAGE_FILES if timed_outputs else set())
        if {path.name for path in temporary.iterdir() if path.is_file()} != expected_final:
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
    parser.add_argument(
        "--voice-audio-dir",
        type=Path,
        help="optional directory containing one measured VOICEVOX WAV per dialogue line; files are not copied",
    )
    args = parser.parse_args(argv)
    try:
        manifest = prepare_package(args.input, args.output_dir, args.voice_audio_dir)
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
