#!/usr/bin/env python3
"""Fail closed on evidence-visual and static-character production drift."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config/evidence_visual_static_character_policy.json"
SOURCE_POLICY = ROOT / "config/media_source_policy.json"
READ_GATE = ROOT / "config/media_command_read_gate.json"
DOC = ROOT / "docs/EVIDENCE_VISUAL_AND_STATIC_CHARACTER_STANDARD.md"


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
    source = load(SOURCE_POLICY)
    gate = load(READ_GATE)

    require(policy.get("schema_version") == "evidence-visual-static-character-v1", "static character policy schema drift")
    require(policy.get("status") == "MANDATORY_MEDIA_STANDARD", "static character policy is not mandatory")
    require(DOC.is_file(), "human-readable static character standard is missing")

    authority = policy.get("authority") or {}
    require(authority.get("precedence") == "OVERRIDES_CONFLICTING_OLDER_CHARACTER_MOTION_EXPRESSION_LIPSYNC_BLINK_AND_SPEECH_START_BOUNCE_DEFAULTS", "new policy no longer overrides legacy motion defaults")
    require(authority.get("conflicting_old_character_motion_rules_must_not_be_executed") is True, "legacy motion rules may execute despite override")

    visual = policy.get("evidence_visual_acquisition") or {}
    require(visual.get("default_mode") == "SEARCH_PRIMARY_OR_OFFICIAL_EVIDENCE_VISUAL_FIRST", "evidence visual mode is no longer search-first")
    require(visual.get("generated_background_image_default") is False, "generated factual/news backgrounds were re-enabled")
    require(visual.get("generated_evidence_image_default") is False, "generated evidence images were re-enabled")
    require(visual.get("generated_visual_must_not_be_used_as_factual_evidence") is True, "generated visuals may be presented as evidence")
    require(visual.get("do_not_call_image_generation_for_normal_news_or_factual_backgrounds") is True, "image generation may run for normal factual backgrounds")
    require(visual.get("search_before_creating_visual") is True, "search-first rule was disabled")
    source_order = list(visual.get("preferred_source_order") or [])
    require(source_order[:2] == ["OFFICIAL_PRIMARY_SOURCE", "OFFICIAL_PRESS_KIT_NEWSROOM_SUPPORT_OR_PRODUCT_PAGE"], "official/primary source priority drift")
    require(visual.get("source_visual_must_support_current_claim_or_narration") is True, "visual-to-claim semantic mapping was disabled")
    require(visual.get("source_page_required") is True, "source page is no longer required")
    require(visual.get("asset_locator_required") is True, "asset locator is no longer required")
    require(visual.get("claim_or_scene_mapping_required") is True, "claim/scene mapping is no longer required")
    require(visual.get("reuse_terms_or_license_state_required") is True, "reuse/license state is no longer required")
    require(visual.get("unknown_rights_block_public_use") is True, "unknown-rights visuals may enter public output")
    require(visual.get("search_result_thumbnail_is_discovery_not_license") is True, "search result thumbnail may be treated as a license")
    require(visual.get("public_output_requires_rights_cleared_or_otherwise_permissible_use") is True, "public rights gate was disabled")
    require(visual.get("cache_verified_assets_with_source_receipt") is True, "verified source assets are no longer cached with provenance")
    require(visual.get("do_not_redownload_identical_verified_asset_without_reason") is True, "duplicate source downloads were re-enabled")

    character = policy.get("character_rendering") or {}
    require(character.get("default_mode") == "STATIC_TURN_FOCUS", "character mode drifted from static turn focus")
    for field in (
        "character_idle_animation",
        "mouth_animation",
        "automatic_lipsync",
        "blink_animation",
        "head_tilt_animation",
        "pose_animation",
        "body_bob_or_vertical_bounce",
        "reaction_symbol_animation",
        "entry_exit_animation_per_line",
        "continuous_zoom_or_pan_on_character",
        "expression_swap_during_normal_dialogue",
    ):
        require(character.get(field) is False, f"unrequested character motion re-enabled: {field}")

    active = character.get("active_speaker") or {}
    inactive = character.get("inactive_listener") or {}
    require(float(active.get("scale")) == 1.08, "active speaker scale must remain 1.08")
    require(int(active.get("opacity_percent")) == 100, "active speaker must remain fully opaque")
    require(float(inactive.get("scale")) == 1.0, "inactive listener scale must remain 1.00")
    require(int(inactive.get("opacity_percent")) == 55, "inactive listener opacity must remain 55 percent by default")
    require(inactive.get("allowed_opacity_percent_range") == [50, 60], "inactive listener half-transparent range drift")

    focus = character.get("focus_switch") or {}
    require(focus.get("required") is True, "speaker focus switching is no longer required")
    require(focus.get("switch_on_voice_turn") is True, "speaker focus no longer follows voice turns")
    require(focus.get("must_follow_audio_turn_timing") is True, "speaker focus is no longer audio-timed")
    require(focus.get("animated_interpolation") is False, "speaker focus transition animation was re-enabled")
    require(focus.get("snap_state_change_at_turn_boundary") is True, "turn-boundary static focus switch was disabled")

    efficiency = policy.get("production_efficiency") or {}
    require(efficiency.get("do_not_build_or_regenerate_character_reaction_pack_for_normal_static_turn_focus") is True, "static mode may rebuild reaction packs")
    require(efficiency.get("do_not_run_full_face_mouth_fixture_for_unchanged_static_portraits") is True, "static unchanged portraits may run mouth fixture")
    require(efficiency.get("do_not_generate_background_images_when_searchable_evidence_visual_exists") is True, "background generation may replace searchable evidence visuals")
    require(efficiency.get("preserve_existing_voice_and_timing_when_only_visual_assets_or_focus_state_change") is True, "visual-only edits may regenerate voice/timing")
    require(efficiency.get("repair_smallest_affected_layer") is True, "smallest-layer repair rule was disabled")

    source_policy = source.get("policy") or {}
    require(source.get("generated_images_enabled_by_default") is False, "media source policy re-enabled generated images")
    require(source_policy.get("search_first") is True, "media source policy is no longer search-first")
    require(source_policy.get("source_page_required") is True, "media source policy no longer requires source pages")
    require(source_policy.get("asset_locator_required") is True, "media source policy no longer requires asset locators")
    require(source_policy.get("unknown_rights_blocked") is True, "unknown rights are no longer blocked")

    common = set(gate.get("common_media_read_set") or [])
    require("config/evidence_visual_static_character_policy.json" in common, "media read gate does not require the new policy")
    require("docs/EVIDENCE_VISUAL_AND_STATIC_CHARACTER_STANDARD.md" in common, "media read gate does not require the new human standard")

    knowhow = set((gate.get("know_how_that_must_be_recovered") or {}).get("common") or [])
    require("searched official or primary-source evidence visuals are the default for factual/news backgrounds; generated background images are not the default" in knowhow, "media read gate lost searched evidence visual rule")
    require("Zundamon and Shikoku Metan remain static by default; only turn-based speaker focus changes are required" in knowhow, "media read gate lost static character rule")

    forbidden = set(gate.get("forbidden_shortcuts") or [])
    for item in (
        "GENERATE_FACTUAL_BACKGROUND_WHEN_SEARCHABLE_EVIDENCE_VISUAL_EXISTS",
        "ANIMATE_ZUNDAMON_METAN_MOUTH_BLINK_POSE_OR_BOUNCE_BY_DEFAULT",
        "LEAVE_INACTIVE_LISTENER_FULLY_OPAQUE_BY_DEFAULT",
    ):
        require(item in forbidden, f"media read gate missing hard prohibition: {item}")

    print(json.dumps({
        "status": "PASS",
        "policy": policy.get("schema_version"),
        "visual_mode": visual.get("default_mode"),
        "generated_background_default": visual.get("generated_background_image_default"),
        "character_mode": character.get("default_mode"),
        "active_scale": active.get("scale"),
        "inactive_opacity_percent": inactive.get("opacity_percent"),
        "animated_focus_transition": focus.get("animated_interpolation"),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
