#!/usr/bin/env python3
"""Fail closed on regressions to permanent character, caption and searched-background standards."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config/media_audio_motion_retention_policy.json"
MANIFEST = ROOT / "config/permanent_standards_manifest.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    require(policy.get("schema_version") == "media-audio-motion-retention-v5", "media creative policy must remain v5 or be deliberately migrated with this validator")
    require(policy.get("status") == "ENFORCED_STANDARD", "media creative policy is not enforced")

    indexed = {
        item.get("machine_policy")
        for item in (manifest.get("required_standards") or [])
        if isinstance(item, dict)
    }
    require("config/media_audio_motion_retention_policy.json" in indexed, "media audio/motion/visual policy is no longer indexed by the permanent manifest")
    media_gate = manifest.get("media_command_gate") or {}
    require(media_gate.get("audio_motion_retention_policy") == "config/media_audio_motion_retention_policy.json", "media command gate no longer points to the creative policy")

    visual = policy.get("visual_asset_acquisition") or {}
    require(visual.get("generated_image_assets_allowed") is False, "generated image assets were re-enabled")
    require(visual.get("generated_video_assets_allowed") is False, "generated video assets were re-enabled")
    require(visual.get("generated_background_images_allowed") is False, "generated background images were re-enabled")
    require(visual.get("default_mode") == "SEARCH_DISCOVER_THEN_VERIFY_AND_MATERIALIZE", "search-first visual acquisition drifted")
    require(visual.get("news_background_default_mode") == "SEARCH_COLLECTED_RIGHTS_VERIFIED_REAL_OR_OFFICIAL_VISUAL", "news backgrounds no longer require searched rights-verified visuals")
    require(visual.get("news_background_must_semantically_match_current_narration_or_claim") is True, "news background semantic match rule drifted")
    require(visual.get("audio_waveform_or_voice_visualizer_is_not_default_background") is True, "audio visualizer became a default background again")

    direction = policy.get("character_direction") or {}
    require(direction.get("standard_cast_actual_character_visuals_required_when_series_or_task_uses_them") is True, "actual standard-cast character presence is no longer required")
    require(direction.get("voice_waveform_or_audio_visualizer_cannot_substitute_for_character_presence") is True, "voice waveform may substitute for character presence again")
    require(direction.get("placeholder_voice_panel_cannot_substitute_for_character_presence") is True, "placeholder voice panel may substitute for character presence again")
    require(direction.get("temporary_character_hide_or_reduction_allowed_when_evidence_or_caption_is_primary_hero") is True, "evidence-hero exception was lost")

    motion = policy.get("character_motion") or {}
    require(motion.get("no_long_static_talking_portrait") is True, "long static talking portraits were re-enabled")
    require(motion.get("stable_anchor_does_not_mean_frozen_character") is True, "stable anchor may freeze characters again")
    require(motion.get("visible_standard_cast_characters_require_state_driven_motion_or_expression_change") is True, "visible standard cast no longer requires state-driven motion/expression change")

    profile = policy.get("zundamon_metan_editing_profile") or {}
    require(profile.get("status") == "MANDATORY_SERIES_STANDARD", "Zundamon/Metan editing profile is no longer mandatory")

    authoring = profile.get("authoring_environment") or {}
    require(authoring.get("preferred_editor") == "YMM4", "YMM4 is no longer the preferred Zundamon/Metan editor")
    require(authoring.get("voice_engine") == "VOICEVOX", "VOICEVOX integration drifted")
    require(authoring.get("ymm4_voicevox_link_for_automatic_lipsync") is True, "YMM4/VOICEVOX automatic lip sync was disabled")
    require(authoring.get("automatic_mouth_animation_required") is True, "mouth animation is no longer required")
    require(authoring.get("automatic_blink_required") is True, "automatic blink is no longer required")
    require(authoring.get("character_assets_must_support_mouth_and_eye_state_changes") is True, "character assets no longer require eye/mouth states")
    require(authoring.get("non_ymm4_fallback_must_reproduce_same_visible_behavior") is True, "non-YMM4 fallback may now reduce visible behavior")

    bounce = profile.get("speech_start_bounce") or {}
    require(bounce.get("required") is True, "speech-start bounce was disabled")
    require(bounce.get("trigger") == "START_OF_EACH_SPEAKING_TURN", "speech-start bounce trigger drifted")
    require(bounce.get("scale_sequence") == [1.0, 1.05, 1.0], "speech-start scale bounce must remain 100%-105%-100%")
    require(bounce.get("vertical_bounce_required") is True, "Y-axis speech-start bounce was disabled")
    require(bounce.get("easing_required") is True, "speech-start easing was disabled")

    focus = profile.get("speaker_focus_switch") or {}
    active = focus.get("active_speaker") or {}
    inactive = focus.get("inactive_listener") or {}
    require(focus.get("required") is True, "speaker focus switching was disabled")
    require(active.get("scale") == 1.05, "active speaker scale must remain 1.05")
    require(active.get("brightness_percent") == 100, "active speaker brightness must remain 100%")
    require(active.get("z_order") == "FRONTMOST_CHARACTER", "active speaker must remain frontmost")
    require(inactive.get("scale") == 1.0, "inactive listener scale must remain 1.00")
    require(inactive.get("brightness_percent") == 80, "inactive listener brightness must remain 80%")
    require(focus.get("switch_on_voice_turn") is True, "speaker focus no longer switches on voice turn")
    require(focus.get("focus_change_must_follow_audio_turn_timing") is True, "speaker focus no longer follows audio timing")

    expression = profile.get("expression_and_pose") or {}
    require(expression.get("change_every_sentence_count_range") == [1, 2], "expression cadence must remain every 1-2 sentences")
    require(expression.get("emotion_mapped_expression_required") is True, "emotion-mapped expressions were disabled")
    require(expression.get("reaction_symbols_should_be_added_frequently_when_semantically_appropriate") is True, "reaction-symbol editing rule was disabled")
    required_symbols = {"sweat_mark", "anger_mark", "question_mark", "surprise_mark", "emphasis_symbol"}
    require(required_symbols.issubset(set(expression.get("reaction_symbols") or [])), "reaction symbol set regressed")

    caption = profile.get("caption_professional_style") or {}
    fonts = list(caption.get("preferred_fonts_in_order") or [])
    for required_font in ("ラグランパンチ", "キルゴシック", "源ノ角ゴシック Heavy", "コーポレート・ロゴ"):
        require(required_font in fonts, f"preferred caption font missing: {required_font}")
    require(caption.get("base_text_color") == "#FFFFFF", "base caption text must remain white")
    require(caption.get("double_outline_required") is True, "double-outline caption style was disabled")
    inner = caption.get("inner_outline") or {}
    outer = caption.get("outer_outline") or {}
    require(inner.get("color") == "#000000", "inner caption outline must remain black")
    require(inner.get("width_px_range") == [3, 5], "inner caption outline width drifted")
    require(outer.get("width_px_range") == [6, 10], "outer caption outline width drifted")
    require(outer.get("color_source") == "CURRENT_SPEAKER_CHARACTER_COLOR", "outer caption outline no longer follows speaker color")
    colors = caption.get("character_theme_colors") or {}
    require(colors.get("ずんだもん") == "#8BC34A", "Zundamon caption color drifted")
    require(colors.get("四国めたん") == "#E91E63", "Shikoku Metan caption color drifted")
    require(set(caption.get("emphasis_word_colors") or []) == {"#FFEB3B", "#F44336"}, "emphasis word colors drifted")
    require(caption.get("emphasis_word_scale") == 1.2, "emphasis word scale must remain 1.2")
    require(caption.get("caption_backplate_required") is True, "caption backplate was disabled")
    require(caption.get("max_characters_per_line") == 15, "caption line limit must remain 15 characters")
    require(caption.get("preferred_characters_per_line_range") == [13, 15], "caption preferred line range drifted")
    require(caption.get("timing_source") == "VOICE_AUDIO_BOUNDARY", "caption timing source must remain audio boundary")
    require(caption.get("timing_precision") == "MILLISECOND_LEVEL", "caption timing must remain millisecond-level")
    require(caption.get("caption_start_must_match_speech_start") is True, "caption start no longer matches speech start")
    require(caption.get("caption_switch_must_match_voice_turn_or_phrase_change") is True, "caption switch no longer follows voice turn/phrase change")

    contextual = policy.get("contextual_visuals") or {}
    require(contextual.get("news_backgrounds_prefer_search_collected_real_or_official_images_over_abstract_generated_visuals") is True, "searched real/official news-background preference drifted")
    require(contextual.get("audio_visualizer_is_not_a_semantic_news_background") is True, "audio visualizer is being treated as semantic news background again")

    forbidden = set((policy.get("anti_boredom_patterns") or {}).get("forbidden_default") or [])
    require("voice_waveform_as_character_substitute" in forbidden, "waveform character-substitute prohibition missing")
    require("audio_visualizer_as_default_news_background" in forbidden, "audio-visualizer background prohibition missing")

    qa = set((policy.get("evaluation") or {}).get("machine_qa") or [])
    for item in (
        "required_character_presence",
        "character_motion_present_when_required",
        "voice_waveform_not_used_as_character_substitute",
        "news_background_source_and_semantic_match",
        "ymm4_or_equivalent_lipsync_present",
        "blink_present",
        "speech_start_bounce_present",
        "speaker_focus_switch_present",
        "expression_change_cadence",
        "double_outline_caption_style",
        "caption_max_15_characters_per_line",
        "caption_audio_boundary_sync_ms",
    ):
        require(item in qa, f"media QA contract missing: {item}")

    print(json.dumps({
        "status": "PASS",
        "schema_version": policy.get("schema_version"),
        "permanent_manifest_indexed": True,
        "generated_image_ai_default": False,
        "actual_cast_visuals_required": True,
        "preferred_editor": authoring.get("preferred_editor"),
        "voice_engine": authoring.get("voice_engine"),
        "automatic_lipsync": authoring.get("ymm4_voicevox_link_for_automatic_lipsync"),
        "automatic_blink": authoring.get("automatic_blink_required"),
        "speech_start_scale_sequence": bounce.get("scale_sequence"),
        "active_speaker_scale": active.get("scale"),
        "inactive_brightness_percent": inactive.get("brightness_percent"),
        "caption_max_characters_per_line": caption.get("max_characters_per_line"),
        "caption_timing_precision": caption.get("timing_precision"),
        "news_background_mode": visual.get("news_background_default_mode"),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
