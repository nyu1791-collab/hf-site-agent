#!/usr/bin/env python3
"""Fail closed on evidence-visual, caption-color, pacing, voice-speed, and static-character drift."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config/evidence_visual_static_character_policy.json"
SOURCE_POLICY = ROOT / "config/media_source_policy.json"
READ_GATE = ROOT / "config/media_command_read_gate.json"
DOC = ROOT / "docs/EVIDENCE_VISUAL_AND_STATIC_CHARACTER_STANDARD.md"
LEGACY_RENDERER = ROOT / "scripts/render_zundamon_metan_longform.py"
STATIC_RENDERER = ROOT / "scripts/render_static_speaker_color_longform.py"
SYNTH = ROOT / "scripts/synthesize_longform_voicevox.py"


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
    for required_file in (DOC, LEGACY_RENDERER, STATIC_RENDERER, SYNTH):
        require(required_file.is_file(), f"required media file missing: {required_file}")

    authority = policy.get("authority") or {}
    require("OVERRIDES_CONFLICTING_OLDER" in str(authority.get("precedence")), "policy precedence missing")
    require(authority.get("conflicting_old_character_motion_rules_must_not_be_executed") is True, "legacy motion rules may still execute")

    visual = policy.get("evidence_visual_acquisition") or {}
    require(visual.get("default_mode") == "SEARCH_PRIMARY_OR_OFFICIAL_EVIDENCE_VISUAL_FIRST", "visual path is no longer search/primary first")
    require(visual.get("generated_background_image_default") is False, "generated factual backgrounds re-enabled")
    require(visual.get("generated_evidence_image_default") is False, "generated evidence visuals re-enabled")
    require(visual.get("generated_visual_must_not_be_used_as_factual_evidence") is True, "generated visuals may be factual evidence")
    require(visual.get("search_before_creating_visual") is True, "search-first disabled")
    require(visual.get("real_photo_or_primary_screenshot_preferred_when_available") is True, "real/primary visual preference missing")
    require(visual.get("photo_or_primary_visual_preferred_over_image_generation_even_when_generation_is_possible") is True, "photo-first no-generation rule missing")
    require(visual.get("do_not_generate_visual_merely_to_increase_scene_count") is True, "scene-count image generation allowed")
    require(visual.get("reuse_rights_verified_photos_across_semantically_compatible_scenes") is True, "verified photo reuse disabled")
    require(visual.get("generated_visual_is_last_resort_non_evidentiary_or_explicit_request") is True, "generated visual is no longer last-resort/non-evidentiary")
    require(visual.get("source_page_required") is True and visual.get("asset_locator_required") is True, "visual provenance requirement removed")
    require(visual.get("unknown_rights_block_public_use") is True, "unknown rights may enter public use")

    voice = policy.get("voice_delivery") or {}
    require(float(voice.get("voicevox_speed_scale") or 0) == 1.2, "VOICEVOX speed default must remain 1.20")
    require(voice.get("actual_generated_wav_must_be_remeasured_after_speed_change") is True, "measured timing after speed change disabled")
    require(voice.get("timeline_and_caption_cues_follow_measured_audio") is True, "timeline no longer follows measured audio")

    captions = policy.get("caption_rendering") or {}
    require(captions.get("full_caption_coverage_required") is True, "full caption coverage disabled")
    require(captions.get("every_spoken_turn_must_have_visible_caption_text") is True, "spoken turns may omit captions")
    require(captions.get("caption_text_must_not_be_truncated_by_fixed_line_count") is True, "caption truncation re-enabled")
    require(captions.get("speaker_colored_border_required") is True, "speaker-colored border removed")
    require(captions.get("speaker_colored_caption_text_required") is True, "speaker-colored caption text removed")
    require(captions.get("zundamon_caption_text_color_role") == "BRIGHT_GREEN", "Zundamon caption text color drift")
    require(captions.get("metan_caption_text_color_role") == "BRIGHT_PINK_MAGENTA", "Metan caption text color drift")
    require(captions.get("caption_body_color_role") == "MATCH_ACTIVE_SPEAKER_ACCENT", "caption body is no longer speaker-colored")
    require(captions.get("white_caption_body_as_default_for_zundamon_metan") is False, "white caption body re-enabled as default")
    require(captions.get("topic_heading_granularity") == "SEMANTIC_CONTENT_BLOCK_NOT_EVERY_UTTERANCE", "heading granularity drift")
    require(captions.get("per_utterance_heading_forbidden_by_default") is True, "per-utterance headings re-enabled")

    character = policy.get("character_rendering") or {}
    require(character.get("default_mode") == "STATIC_TURN_FOCUS", "character mode drift")
    for field in (
        "character_idle_animation", "mouth_animation", "automatic_lipsync", "blink_animation",
        "head_tilt_animation", "pose_animation", "body_bob_or_vertical_bounce",
        "reaction_symbol_animation", "entry_exit_animation_per_line",
        "continuous_zoom_or_pan_on_character", "expression_swap_during_normal_dialogue",
    ):
        require(character.get(field) is False, f"unrequested character motion re-enabled: {field}")
    active = character.get("active_speaker") or {}
    inactive = character.get("inactive_listener") or {}
    require(float(active.get("scale")) == 1.08 and int(active.get("opacity_percent")) == 100, "active speaker focus drift")
    require(float(inactive.get("scale")) == 1.0 and int(inactive.get("opacity_percent")) == 55, "inactive listener focus drift")

    pacing = policy.get("production_pacing") or {}
    require(pacing.get("longform_target_duration_seconds") == [360, 720], "longform target must remain 6-12 minutes")
    require(pacing.get("continuous_information_flow_required") is True, "continuous information flow disabled")
    require(pacing.get("deliberate_padding_for_duration_forbidden") is True, "duration padding allowed")
    require(float(pacing.get("default_inter_turn_pause_max_seconds") or 9) <= 0.35, "inter-turn dead-air limit drift")
    require(float(pacing.get("default_section_transition_pause_max_seconds") or 9) <= 0.45, "section-transition dead-air limit drift")
    require(pacing.get("image_generation_wait_must_not_block_normal_photo_first_production") is True, "image generation can block normal photo-first production")

    efficiency = policy.get("production_efficiency") or {}
    require(efficiency.get("canonical_static_speaker_color_renderer") == "scripts/render_static_speaker_color_longform.py", "canonical static renderer drift")
    require(efficiency.get("do_not_generate_background_images_when_searchable_evidence_visual_exists") is True, "searchable visual may be replaced by generated background")

    source_policy = source.get("policy") or {}
    require(source.get("generated_images_enabled_by_default") is False, "media source policy re-enabled generated images")
    require(source_policy.get("search_first") is True, "media source policy is no longer search-first")
    require(source_policy.get("source_page_required") is True, "media source policy no longer requires source pages")
    require(source_policy.get("unknown_rights_blocked") is True, "unknown rights are no longer blocked")

    common = set(gate.get("common_media_read_set") or [])
    require("config/evidence_visual_static_character_policy.json" in common, "media read gate does not require policy")
    require("docs/EVIDENCE_VISUAL_AND_STATIC_CHARACTER_STANDARD.md" in common, "media read gate does not require human standard")

    static_renderer = STATIC_RENDERER.read_text(encoding="utf-8")
    require("ACTIVE_SCALE = 1.08" in static_renderer, "static renderer lost 1.08 active scale")
    require("INACTIVE_OPACITY = 0.55" in static_renderer, "static renderer lost 55% inactive opacity")
    require("fill=accent" in static_renderer, "static renderer no longer colors caption text by speaker")
    require("mouth_animation" in static_renderer and '"mouth_animation": False' in static_renderer, "static renderer contract lost mouth-animation=false evidence")
    require("SCENE_ASSET" in static_renderer and "Photo:" in static_renderer, "static renderer lost photo-first scene path")
    require("pause_after" in static_renderer and "0.45" in static_renderer, "static renderer lost dead-air gate")

    synth = SYNTH.read_text(encoding="utf-8")
    require("DEFAULT_SPEED_SCALE=1.20" in synth, "synthesizer lost 1.20 default speed")
    require('query["speedScale"]=args.speed_scale' in synth, "VOICEVOX audio query lost configured speed")

    hard = set(policy.get("hard_fail_conditions") or [])
    for item in (
        "IMAGE_GENERATION_USED_WHEN_RIGHTS_VERIFIED_REAL_OR_PRIMARY_VISUAL_ALREADY_FITS",
        "CAPTION_TEXT_COLOR_DOES_NOT_MATCH_ACTIVE_SPEAKER",
        "SPOKEN_TEXT_MISSING_FROM_CAPTIONS",
        "CAPTION_TRUNCATED_BY_FIXED_LINE_LIMIT",
        "LONGFORM_OUTSIDE_6_TO_12_MINUTES_WITHOUT_EXPLICIT_REASON",
        "DELIBERATE_PADDING_OR_EXCESSIVE_DEAD_AIR",
        "VOICE_SPEED_DEFAULT_NOT_1_20_FOR_ZUNDAMON_METAN",
    ):
        require(item in hard, f"hard-fail condition missing: {item}")

    print(json.dumps({
        "status": "PASS",
        "policy": policy.get("schema_version"),
        "voicevox_speed_scale": voice.get("voicevox_speed_scale"),
        "caption_body_color_role": captions.get("caption_body_color_role"),
        "photo_first": visual.get("photo_or_primary_visual_preferred_over_image_generation_even_when_generation_is_possible"),
        "longform_target_duration_seconds": pacing.get("longform_target_duration_seconds"),
        "character_mode": character.get("default_mode"),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
