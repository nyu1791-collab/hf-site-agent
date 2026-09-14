#!/usr/bin/env python3
"""Fail closed on reaction-cache, non-vertical motion, and TTS/caption separation drift."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config/media_character_reaction_cache_policy.json"
GATE = ROOT / "config/media_command_read_gate.json"
MANIFEST = ROOT / "config/permanent_standards_manifest.json"
REUSABLE = ROOT / "config/media_reusable_asset_standard.json"
BUILDER = ROOT / "scripts/prepare_character_reaction_pack.py"
DOC = ROOT / "docs/ZUNDAMON_METAN_REACTION_AND_SUBTITLE_STANDARD.md"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path}: object required")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    policy = load(POLICY)
    gate = load(GATE)
    manifest = load(MANIFEST)
    reusable = load(REUSABLE)

    require(policy.get("schema_version") == "media-character-reaction-cache-v1", "reaction-cache policy schema drift")
    require(policy.get("status") == "ENFORCED_STANDARD", "reaction-cache policy is not enforced")
    require(BUILDER.is_file(), "reaction-pack builder is missing")
    require(DOC.is_file(), "human-readable reaction/subtitle standard is missing")

    authority = policy.get("authority") or {}
    require(authority.get("source_asset_registry") == "config/media_reusable_asset_standard.json", "reaction policy lost reusable registry authority")
    require(authority.get("source_asset_id") == "zm_shell_20230806", "reaction policy source shell drift")
    require(authority.get("reaction_pack_builder") == "scripts/prepare_character_reaction_pack.py", "reaction-pack builder path drift")
    require(authority.get("conversation_memory_is_not_source_of_truth") is True, "chat memory became reaction-policy authority")

    assets = {str(x.get("asset_id")): x for x in (reusable.get("assets") or []) if isinstance(x, dict)}
    require("zm_shell_20230806" in assets, "registered Zundamon/Metan shell disappeared")

    cache = policy.get("cache_contract") or {}
    for key in (
        "source_shell_download_once_when_cache_missing",
        "verified_source_cache_hit_forbids_redownload",
        "reaction_pack_build_only_when_missing_or_source_sha_changes",
        "verified_reaction_pack_hit_forbids_rebuild",
        "no_search_during_render",
        "no_network_during_reaction_pack_build",
        "no_per_scene_download",
        "no_per_line_download",
        "load_inventory_once_per_render",
        "preprocess_transparency_once",
        "index_all_character_pngs_once",
        "raw_third_party_assets_not_committed_to_repository",
        "derived_third_party_assets_not_committed_to_repository",
        "rights_recheck_before_publication_still_required",
    ):
        require(cache.get(key) is True, f"reaction cache guarantee missing: {key}")
    require(cache.get("derived_pack_root") == ".media-cache/derived/zm-reaction-pack-v1", "reaction pack cache root drift")

    inventory = policy.get("minimum_inventory") or {}
    require(int(inventory.get("source_png_total_min") or 0) >= 160, "reaction inventory minimum became too small")
    require(int(inventory.get("zundamon_png_min") or 0) >= 95, "Zundamon inventory minimum regressed")
    require(int(inventory.get("metan_png_min") or 0) >= 65, "Metan inventory minimum regressed")
    required_categories = set(inventory.get("required_categories") or [])
    for category in ("mouth", "eyes", "brows", "symbols", "face_tone", "right_arm", "left_arm"):
        require(category in required_categories, f"reaction category missing: {category}")
    required_symbols = set(inventory.get("required_generated_symbols") or [])
    require({"question", "surprise", "emphasis", "anger", "focus_flash"}.issubset(required_symbols), "generated reaction symbol set regressed")

    motion = policy.get("motion_quality_contract") or {}
    require(motion.get("vertical_only_motion_is_insufficient") is True, "vertical-only motion became acceptable")
    require(motion.get("speech_start_bounce_may_remain_as_micro_accent") is True, "speech-start bounce compatibility lost")
    require(motion.get("speech_start_bounce_must_not_be_the_only_visible_animation") is True, "bounce may be the only visible motion")
    require(motion.get("semantic_beat_requires_nonvertical_or_state_change") is True, "semantic-beat nonvertical/state change requirement lost")
    changes = set(motion.get("allowed_nonvertical_or_state_changes") or [])
    for change in ("x_translation", "scale_push_or_pull", "rotation_or_tilt", "pose_swap", "expression_swap", "reaction_symbol_overlay"):
        require(change in changes, f"nonvertical/state motion option missing: {change}")
    require(int(motion.get("expression_or_pose_change_within_sentence_count") or 999) <= 2, "expression/pose cadence became too sparse")
    require(motion.get("speaker_mouth_animation_required") is True, "speaker mouth animation disabled")
    require(motion.get("extended_visible_character_blink_required") is True, "blink requirement disabled")

    captions = policy.get("voice_caption_separation") or {}
    require(captions.get("separate_voice_text_and_caption_text_fields") is True, "voice/caption fields are no longer separate")
    require(captions.get("voice_text_may_use_katakana_pronunciation_for_foreign_terms") is True, "katakana TTS pronunciation support lost")
    require(captions.get("caption_text_prefers_official_latin_or_english_spelling") is True, "English caption spelling preference lost")
    require(captions.get("caption_text_must_not_copy_katakana_pronunciation_automatically") is True, "katakana TTS may leak into captions")
    examples = {(x.get("voice_text"), x.get("caption_text")) for x in (captions.get("examples") or []) if isinstance(x, dict)}
    for pair in (("エーアイ", "AI"), ("オープンエーアイ", "OpenAI"), ("アンスロピック", "Anthropic"), ("ハギングフェイス", "Hugging Face")):
        require(pair in examples, f"required TTS/caption mapping missing: {pair}")

    perf = policy.get("render_performance_contract") or {}
    for key in (
        "prefer_preprocessed_pack_over_raw_shell_scan",
        "avoid_per_frame_directory_scan",
        "avoid_per_frame_image_decode_when_reusable",
        "cache_scaled_variants_within_same_output_resolution",
        "reuse_expression_composites_within_same_render",
        "single_writer_for_pack_build",
        "atomic_pack_publish",
        "quality_gates_must_not_be_disabled_for_speed",
    ):
        require(perf.get(key) is True, f"reaction render performance guarantee missing: {key}")
    require(1 <= int(perf.get("parallel_preparation_max_workers") or 0) <= 4, "reaction preparation parallelism must stay bounded 1..4")

    common = set(gate.get("common_media_read_set") or [])
    require("config/media_character_reaction_cache_policy.json" in common, "media read gate no longer restores reaction-cache policy")
    require("docs/ZUNDAMON_METAN_REACTION_AND_SUBTITLE_STANDARD.md" in common, "media read gate no longer restores readable reaction/subtitle standard")
    session = gate.get("new_session_behavior") or {}
    require(session.get("character_reaction_cache_policy_must_be_re_read") is True, "new sessions may skip reaction-cache policy")
    require(session.get("reaction_subtitle_human_standard_must_be_re_read") is True, "new sessions may skip readable reaction/subtitle standard")

    standards = manifest.get("required_standards") or []
    indexed = {str(x.get("id")): x for x in standards if isinstance(x, dict)}
    reaction_standard = indexed.get("media-character-reaction-cache") or {}
    require(reaction_standard.get("machine_policy") == "config/media_character_reaction_cache_policy.json", "permanent manifest lost reaction-cache policy")
    require(reaction_standard.get("human_doc") == "docs/ZUNDAMON_METAN_REACTION_AND_SUBTITLE_STANDARD.md", "permanent manifest lost reaction/subtitle doc")
    require(reaction_standard.get("builder") == "scripts/prepare_character_reaction_pack.py", "permanent manifest lost reaction-pack builder")
    require(reaction_standard.get("validator") == "scripts/validate_media_character_reaction_cache.py", "permanent manifest lost reaction-cache validator")
    media_manifest = manifest.get("media_command_gate") or {}
    require(media_manifest.get("character_reaction_cache_policy") == "config/media_character_reaction_cache_policy.json", "manifest media gate lost reaction-cache policy")
    cross_tab = manifest.get("cross_tab_behavior") or {}
    require(cross_tab.get("media_character_reaction_cache_survives_tab_change") is True, "reaction-cache standard no longer survives tab change")

    forbidden = set(gate.get("forbidden_shortcuts") or [])
    require("SEARCH_OR_DOWNLOAD_ZUNDAMON_METAN_REACTION_ASSETS_PER_VIDEO" in forbidden, "read gate permits per-video reaction search/download")
    require("USE_VERTICAL_BOUNCE_AS_ONLY_CHARACTER_MOTION" in forbidden, "read gate permits vertical-only motion")
    require("COPY_KATAKANA_TTS_READING_DIRECTLY_TO_ENGLISH_CAPTION_TERM" in forbidden, "read gate permits katakana TTS leakage into captions")

    print(json.dumps({
        "status": "PASS",
        "policy": policy.get("schema_version"),
        "reaction_pack_root": cache.get("derived_pack_root"),
        "no_per_scene_download": True,
        "vertical_only_motion_blocked": True,
        "voice_caption_separated": True,
        "read_gate_restored": True,
        "permanent_manifest_indexed": True
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
