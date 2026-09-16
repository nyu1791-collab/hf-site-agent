#!/usr/bin/env python3
"""Fail closed on evidence-visual, caption, voice-speed, and static-character drift."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config/evidence_visual_static_character_policy.json"
SOURCE_POLICY = ROOT / "config/media_source_policy.json"
READ_GATE = ROOT / "config/media_command_read_gate.json"
DOC = ROOT / "docs/EVIDENCE_VISUAL_AND_STATIC_CHARACTER_STANDARD.md"
RENDERER = ROOT / "scripts/render_zundamon_metan_longform.py"
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
    require(DOC.is_file(), "human-readable static character standard is missing")
    require(RENDERER.is_file(), "longform renderer is missing")
    require(SYNTH.is_file(), "longform VOICEVOX synthesizer is missing")

    authority = policy.get("authority") or {}
    require("OVERRIDES_CONFLICTING_OLDER_CHARACTER_MOTION" in str(authority.get("precedence")), "new policy no longer overrides legacy motion defaults")
    require(authority.get("conflicting_old_character_motion_rules_must_not_be_executed") is True, "legacy motion rules may execute despite override")

    visual = policy.get("evidence_visual_acquisition") or {}
    require(visual.get("default_mode") == "SEARCH_PRIMARY_OR_OFFICIAL_EVIDENCE_VISUAL_FIRST", "evidence visual mode is no longer search-first")
    require(visual.get("generated_background_image_default") is False, "generated factual/news backgrounds were re-enabled")
    require(visual.get("generated_evidence_image_default") is False, "generated evidence images were re-enabled")
    require(visual.get("generated_visual_must_not_be_used_as_factual_evidence") is True, "generated visuals may be presented as evidence")
    require(visual.get("search_before_creating_visual") is True, "search-first rule was disabled")
    require(visual.get("source_page_required") is True, "source page is no longer required")
    require(visual.get("asset_locator_required") is True, "asset locator is no longer required")
    require(visual.get("claim_or_scene_mapping_required") is True, "claim/scene mapping is no longer required")
    require(visual.get("unknown_rights_block_public_use") is True, "unknown-rights visuals may enter public output")

    voice = policy.get("voice_delivery") or {}
    require(float(voice.get("voicevox_speed_scale") or 0) == 1.2, "VOICEVOX speed default must remain 1.20")
    require(voice.get("actual_generated_wav_must_be_remeasured_after_speed_change") is True, "speed change may skip measured WAV timing")
    require(voice.get("timeline_and_caption_cues_follow_measured_audio") is True, "caption timeline no longer follows measured audio")
    require(voice.get("do_not_time_stretch_finished_audio_as_substitute_for_voice_generation") is True, "finished-audio time stretch may replace correct synthesis")

    captions = policy.get("caption_rendering") or {}
    require(captions.get("full_caption_coverage_required") is True, "full caption coverage disabled")
    require(captions.get("every_spoken_turn_must_have_visible_caption_text") is True, "spoken turns may omit captions")
    require(captions.get("caption_text_must_not_be_truncated_by_fixed_line_count") is True, "fixed-line caption truncation re-enabled")
    require(captions.get("caption_panel_reserved_safe_zone_required") is True, "caption safe zone removed")
    require(captions.get("caption_panel_must_not_overlap_characters") is True, "caption/character overlap allowed")
    require(captions.get("speaker_colored_border_required") is True, "speaker-colored caption border removed")
    require(captions.get("zundamon_border_color_role") == "BRIGHT_GREEN", "Zundamon caption border color drift")
    require(captions.get("metan_border_color_role") == "BRIGHT_PINK_MAGENTA", "Metan caption border color drift")
    require(captions.get("topic_heading_required") is True, "topic heading disabled")
    require(captions.get("topic_heading_granularity") == "SEMANTIC_CONTENT_BLOCK_NOT_EVERY_UTTERANCE", "heading granularity drifted to utterance-level")
    require(captions.get("topic_heading_changes_only_when_subject_or_argument_block_changes") is True, "heading may churn on every utterance")
    require(captions.get("per_utterance_heading_forbidden_by_default") is True, "per-utterance headings became default")

    character = policy.get("character_rendering") or {}
    require(character.get("default_mode") == "STATIC_TURN_FOCUS", "character mode drifted from static turn focus")
    for field in (
        "character_idle_animation", "mouth_animation", "automatic_lipsync", "blink_animation",
        "head_tilt_animation", "pose_animation", "body_bob_or_vertical_bounce",
        "reaction_symbol_animation", "entry_exit_animation_per_line",
        "continuous_zoom_or_pan_on_character", "expression_swap_during_normal_dialogue",
    ):
        require(character.get(field) is False, f"unrequested character motion re-enabled: {field}")

    active = character.get("active_speaker") or {}
    inactive = character.get("inactive_listener") or {}
    require(float(active.get("scale")) == 1.08, "active speaker scale must remain 1.08")
    require(int(active.get("opacity_percent")) == 100, "active speaker must remain fully opaque")
    require(float(inactive.get("scale")) == 1.0, "inactive listener scale must remain 1.00")
    require(int(inactive.get("opacity_percent")) == 55, "inactive listener opacity must remain 55 percent")
    require(inactive.get("allowed_opacity_percent_range") == [50, 60], "inactive listener half-transparent range drift")

    focus = character.get("focus_switch") or {}
    require(focus.get("switch_on_voice_turn") is True, "speaker focus no longer follows voice turns")
    require(focus.get("must_follow_audio_turn_timing") is True, "speaker focus is no longer audio-timed")
    require(focus.get("animated_interpolation") is False, "speaker focus transition animation was re-enabled")
    require(focus.get("snap_state_change_at_turn_boundary") is True, "turn-boundary static focus switch disabled")

    source_policy = source.get("policy") or {}
    require(source.get("generated_images_enabled_by_default") is False, "media source policy re-enabled generated images")
    require(source_policy.get("search_first") is True, "media source policy is no longer search-first")
    require(source_policy.get("source_page_required") is True, "media source policy no longer requires source pages")
    require(source_policy.get("unknown_rights_blocked") is True, "unknown rights are no longer blocked")

    common = set(gate.get("common_media_read_set") or [])
    require("config/evidence_visual_static_character_policy.json" in common, "media read gate does not require the policy")
    require("docs/EVIDENCE_VISUAL_AND_STATIC_CHARACTER_STANDARD.md" in common, "media read gate does not require the human standard")

    renderer = RENDERER.read_text(encoding="utf-8")
    synth = SYNTH.read_text(encoding="utf-8")
    require("ACTIVE_SCALE=1.08" in renderer, "renderer lost 1.08 active speaker scale")
    require("INACTIVE_OPACITY=0.55" in renderer, "renderer lost 55 percent listener opacity")
    require("CAPTION_BOX=" in renderer and "character overlaps reserved caption safe zone" in renderer, "renderer no longer hard-blocks caption/character overlap")
    require("speaker_colored_border_required" not in renderer, "renderer should implement behavior, not duplicate policy JSON")
    require("current_topic_heading" in renderer and "topic_heading" in renderer and "section_heading" in renderer, "renderer lost semantic heading hierarchy")
    require("fit_caption" in renderer, "renderer lost full-caption fit path")
    require("lines[:3]" not in renderer, "renderer reintroduced fixed three-line caption truncation")
    require("mouth_open" not in renderer and "_open.jpg" not in renderer, "renderer reintroduced mouth animation")
    require("DEFAULT_SPEED_SCALE=1.20" in synth, "synthesizer lost 1.20 default speed")
    require('query["speedScale"]=args.speed_scale' in synth, "VOICEVOX audio_query no longer receives configured speed")
    require('"voicevox_speed_scale":args.speed_scale' in synth, "timing metadata lost voice speed evidence")

    hard = set(policy.get("hard_fail_conditions") or [])
    for item in (
        "SPOKEN_TEXT_MISSING_FROM_CAPTIONS",
        "CAPTION_TRUNCATED_BY_FIXED_LINE_LIMIT",
        "CAPTION_BORDER_DOES_NOT_MATCH_ACTIVE_SPEAKER",
        "CAPTION_OVERLAPS_CHARACTER",
        "TOPIC_HEADING_CHANGES_ON_EVERY_UTTERANCE_WITHOUT_CONTENT_CHANGE",
        "VOICE_SPEED_DEFAULT_NOT_1_20_FOR_ZUNDAMON_METAN",
    ):
        require(item in hard, f"new media hard-fail condition missing: {item}")

    print(json.dumps({
        "status": "PASS",
        "policy": policy.get("schema_version"),
        "voicevox_speed_scale": voice.get("voicevox_speed_scale"),
        "full_caption_coverage": captions.get("full_caption_coverage_required"),
        "topic_heading_granularity": captions.get("topic_heading_granularity"),
        "character_mode": character.get("default_mode"),
        "active_scale": active.get("scale"),
        "inactive_opacity_percent": inactive.get("opacity_percent"),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
