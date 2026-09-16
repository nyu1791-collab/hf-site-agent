#!/usr/bin/env python3
"""Fail closed on Zundamon/Metan production-quality and speed-standard drift."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config/zundamon_metan_production_quality_policy.json"
MEDIA = ROOT / "config/media_audio_motion_retention_policy.json"
REACTION = ROOT / "config/media_character_reaction_cache_policy.json"
BATCH = ROOT / "config/batch_media_orchestration_policy.json"
GATE = ROOT / "config/media_command_read_gate.json"
MANIFEST = ROOT / "config/permanent_standards_manifest.json"
DOC = ROOT / "docs/ZUNDAMON_METAN_PRODUCTION_QUALITY_STANDARD.md"


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
    media = load(MEDIA)
    reaction = load(REACTION)
    batch = load(BATCH)
    gate = load(GATE)
    manifest = load(MANIFEST)

    require(policy.get("schema_version") == "zundamon-metan-production-quality-v1", "production quality policy schema drift")
    require(policy.get("status") == "ENFORCED_STANDARD", "production quality policy is not enforced")
    require(DOC.is_file(), "production quality human-readable standard is missing")

    extends = policy.get("extends") or {}
    require(extends.get("creative_policy") == "config/media_audio_motion_retention_policy.json", "creative policy authority drift")
    require(extends.get("reaction_cache_policy") == "config/media_character_reaction_cache_policy.json", "reaction cache authority drift")
    require(extends.get("reusable_asset_standard") == "config/media_reusable_asset_standard.json", "reusable asset authority drift")

    template = policy.get("authoring_template") or {}
    require(template.get("preferred_editor") == "YMM4", "YMM4 is no longer preferred")
    require(template.get("base_project_template_preferred") is True, "base project template is no longer preferred")
    require(template.get("base_project_extension") == ".ymmp", "YMM4 project extension drift")
    require(template.get("headless_fallback_uses_equivalent_machine_template") is True, "headless equivalent template was disabled")
    require(template.get("headless_fallback_must_not_claim_ymmp_when_no_ymmp_exists") is True, "headless path may falsely claim ymmp")
    require(template.get("caption_style_preset_saved_once_and_reused") is True, "caption style preset reuse was disabled")

    character = policy.get("character_source_preparation") or {}
    require(character.get("psd_or_layered_character_source_allowed_when_rights_verified") is True, "rights-verified PSD use was disabled")
    require(character.get("psd_is_not_required_when_registered_shell_already_satisfies_needed_states") is True, "PSD became an unnecessary hard dependency")
    require(character.get("registered_reaction_pack_preferred_before_new_character_asset_acquisition") is True, "registered reaction pack is no longer preferred")
    require(character.get("no_per_video_psd_or_reaction_redownload") is True, "per-video character redownload was re-enabled")

    mouth = policy.get("mouth_compositing_integrity") or {}
    require(mouth.get("moving_mouth_is_not_sufficient_for_pass") is True, "moving mouth alone became sufficient QA")
    require(mouth.get("per_character_mouth_anchor_calibration_required") is True, "character-specific mouth anchors are no longer required")
    alignment_models = set(mouth.get("accepted_alignment_models") or [])
    require({"SHARED_FULL_CANVAS_COORDINATES", "EXPLICIT_ANCHOR_METADATA"}.issubset(alignment_models), "mouth alignment model set regressed")
    require(mouth.get("exactly_one_active_mouth_state_per_character") is True, "multiple mouth layers may be active")
    require(mouth.get("mouth_state_must_remain_inside_intended_face_socket") is True, "mouth may leave face socket")
    require(mouth.get("mouth_state_must_not_float_detach_or_double") is True, "floating/double mouth is not blocked")
    require(mouth.get("alpha_or_black_key_preprocessing_must_not_cut_skin_or_line_art") is True, "mouth preprocessing may damage face art")
    fixture_states = set(mouth.get("fixture_states_required") or [])
    require({"closed", "small_open", "open_or_wide_open"}.issubset(fixture_states), "mouth fixture state coverage regressed")
    fixture_chars = set(mouth.get("fixture_required_for_each_visible_character") or [])
    require({"zundamon", "metan"}.issubset(fixture_chars), "mouth fixture no longer covers both characters")
    require(mouth.get("fixture_pass_required_before_full_render_when_asset_revision_or_geometry_changes") is True, "mouth fixture no longer gates changed geometry")
    require(mouth.get("fixture_result_reusable_when_source_sha_pack_revision_output_geometry_and_anchor_profile_match") is True, "unchanged mouth fixture cannot be reused")

    script = policy.get("script_first_pipeline") or {}
    require(script.get("structured_script_before_timeline_assembly") is True, "script-first workflow was disabled")
    fields = set(script.get("required_dialogue_fields") or [])
    for field in ("speaker", "voice_text", "caption_text", "emotion", "visual_beat", "sfx_beat"):
        require(field in fields, f"structured dialogue field missing: {field}")
    require(script.get("batch_import_to_ymm4_when_supported") is True, "YMM4 batch import preference lost")
    require(script.get("ai_generated_dialogue_draft_allowed") is True, "AI dialogue drafting unexpectedly disabled")
    require(script.get("ai_generated_factual_claims_still_require_claim_and_source_gates") is True, "AI claims may bypass evidence gates")

    dictionary = policy.get("voicevox_pronunciation_dictionary") or {}
    require(dictionary.get("reusable_dictionary_required_for_recurring_foreign_and_technical_terms") is True, "VOICEVOX pronunciation dictionary reuse lost")
    require(dictionary.get("voice_reading_may_be_katakana") is True, "katakana pronunciation support lost")
    require(dictionary.get("caption_spelling_prefers_official_latin_or_english") is True, "official English caption spelling preference lost")
    require(dictionary.get("caption_text_must_not_be_rewritten_to_match_katakana_voice_reading") is True, "voice reading may leak into captions")

    segmentation = policy.get("caption_semantic_segmentation") or {}
    require(segmentation.get("semantic_boundary_before_character_count") is True, "character count now outranks semantic subtitle boundaries")
    priority = list(segmentation.get("preferred_break_priority") or [])
    require(priority[:3] == ["sentence_or_punctuation_boundary", "clause_boundary", "natural_phrase_or_case_marker_boundary"], "caption semantic break priority drift")
    require(segmentation.get("japanese_kinsoku_required") is True, "Japanese kinsoku is no longer required")
    require(segmentation.get("latin_brand_product_person_and_technical_tokens_are_indivisible") is True, "Latin proper/technical tokens may be split")
    require(segmentation.get("raw_codepoint_slicing_forbidden") is True, "raw codepoint caption slicing became allowed")
    require(segmentation.get("existing_max_characters_per_line_remains_ceiling_not_target") is True, "caption line cap became a fill target")
    require(segmentation.get("breaking_earlier_at_natural_boundary_is_preferred") is True, "natural early caption break preference lost")
    require(segmentation.get("rendered_pixel_width_check_preferred_when_available") is True, "caption layout no longer prefers rendered width")
    require(segmentation.get("caption_chunks_should_align_with_spoken_phrase_boundaries") is True, "caption chunks may ignore spoken phrasing")

    prosody = policy.get("voice_prosody_starting_heuristics") or {}
    require(prosody.get("shortform_or_news_speed_scale_range") == [1.10, 1.20], "speed starting range drift")
    require(prosody.get("shortform_or_news_intonation_scale_range") == [1.10, 1.20], "intonation starting range drift")
    require(prosody.get("hard_require_range") is False, "prosody heuristic became a hard requirement")
    require(prosody.get("clarity_and_naturalness_override_speed_target") is True, "clarity no longer overrides speed")
    require(prosody.get("actual_wav_duration_from_ffprobe_drives_timeline") is True, "timeline no longer uses measured WAV duration")

    mix = policy.get("audio_mix_and_sfx") or {}
    require(mix.get("speech_ducking_required") is True, "speech ducking disabled")
    require(float(mix.get("bgm_ducking_nominal_db")) == -18.0, "nominal BGM ducking should remain -18 dB")
    require(mix.get("nominal_is_starting_point_not_mastering_law") is True, "-18 dB became an inflexible mastering target")
    require(mix.get("sfx_should_align_with_semantic_visual_beats") is True, "semantic SFX synchronization disabled")
    require(mix.get("sfx_on_every_visual_change_required") is False, "SFX became mandatory on every visual change")
    require(mix.get("sfx_on_every_cut_forbidden") is True, "SFX-on-every-cut prohibition lost")

    media_mix = media.get("audio_mix") or {}
    require(media_mix.get("automatic_or_keyframed_ducking_under_speech") is True, "base media policy lost speech ducking")
    duck_range = media_mix.get("bgm_level_starting_heuristic_relative_to_narration_lu") or []
    require(len(duck_range) == 2 and float(duck_range[0]) <= -18 <= float(duck_range[1]), "base media ducking range no longer contains -18")
    require(media_mix.get("heuristics_are_not_universal_mastering_targets") is True, "base media audio heuristics became hard targets")

    effects = policy.get("camera_and_screen_effects") or {}
    require(effects.get("zoom_and_pan_are_semantic_accents") is True, "camera motion became non-semantic")
    require(effects.get("typical_emphasis_zoom_range") == [1.08, 1.20], "emphasis zoom range drift")
    require(float(effects.get("normal_emphasis_zoom_ceiling")) == 1.20, "normal emphasis zoom ceiling drift")
    require(effects.get("constant_zoom_forbidden") is True, "constant zoom prohibition lost")
    require(effects.get("sensitive_or_serious_topic_reduces_comedic_shake_and_filters") is True, "sensitive-topic restraint lost")

    panels = policy.get("explanation_panel_visual_brightness") or {}
    require(panels.get("general_news_explainer_default_is_lifted_midtones_not_near_black") is True, "general explainer panels may default to near-black")
    require(panels.get("large_near_black_panel_as_general_default") is False, "near-black large panel became general default")
    require(panels.get("background_dimming_must_not_make_entire_frame_muddy") is True, "background dimming may muddy the whole frame")
    require(panels.get("diagram_nodes_should_use_distinct_brighter_fills") is True, "diagram nodes lost brighter separation")
    panel_luma = panels.get("dark_theme_panel_average_luma_percent_starting_range") or []
    accent_luma = panels.get("diagram_accent_fill_luma_percent_starting_range") or []
    require(panel_luma == [24, 45], "dark-theme panel luma starting range drift")
    require(accent_luma == [35, 70], "diagram accent luma starting range drift")
    require(panels.get("luma_ranges_are_heuristics_not_hard_mastering_targets") is True, "panel luma heuristic became a hard target")
    require(panels.get("serious_or_grave_topic_may_intentionally_use_darker_treatment_with_reason") is True, "serious-topic dark treatment exception disappeared")

    cadence = policy.get("visual_cadence") or {}
    require(cadence.get("shortform_target_seconds_between_meaningful_visual_state_change") == [2.0, 3.0], "shortform visual cadence heuristic drift")
    require(cadence.get("target_is_heuristic_not_universal_hard_cut_rule") is True, "2-3 second cadence became a hard cut rule")
    require(cadence.get("forced_new_downloaded_asset_every_interval") is False, "cadence now forces asset downloads")
    require(cadence.get("semantic_relevance_over_change_for_change_sake") is True, "visual churn outranks semantic relevance")

    hook = policy.get("opening_hook") or {}
    require(hook.get("shortform_and_news_explainer_default_hook_window_seconds") == [1, 3], "opening hook window drift")
    require(hook.get("skip_generic_greeting_by_default") is True, "generic greeting became default again")
    require(hook.get("hook_must_match_actual_video_content") is True, "hook may become misleading")

    jet = policy.get("jet_cut_and_pause_policy") or {}
    require(jet.get("remove_unintentional_dead_air") is True, "unintentional dead air trimming disabled")
    require(jet.get("complete_removal_of_all_silence_forbidden") is True, "all silence may now be stripped")
    require(jet.get("preserve_intentional_silence") is True, "intentional silence preservation lost")
    require(jet.get("audio_cut_must_not_damage_word_endings_or_natural_phrasing") is True, "jet cuts may damage phrasing")

    preview = policy.get("preview_visual_qa") or {}
    require(preview.get("low_cost_preview_before_expensive_full_render_required_when_visual_layers_changed") is True, "low-cost visual preview no longer gates changed visual layers")
    require(preview.get("risk_based_sampling_over_fixed_timestamp_only_sampling") is True, "visual QA reverted to fixed timestamps only")
    samples = set(preview.get("required_risk_samples") or [])
    for sample in ("zundamon_closed_and_open_mouth", "metan_closed_and_open_mouth", "densest_caption", "longest_latin_token", "darkest_explainer_panel", "safe_zone_edge"):
        require(sample in samples, f"risk-based visual QA sample missing: {sample}")
    require(preview.get("preview_failure_blocks_full_render") is True, "failed preview may proceed to expensive full render")
    require(preview.get("technical_decode_pass_alone_is_not_visual_quality_pass") is True, "decode pass incorrectly became visual-quality proof")

    repair = policy.get("incremental_repair_contract") or {}
    require(repair.get("repair_smallest_affected_layer_or_stage") is True, "smallest-stage repair rule lost")
    require(repair.get("caption_only_change_preserves_voice_character_and_evidence_layers") is True, "caption-only fixes may regenerate healthy upstream layers")
    require(repair.get("panel_palette_or_layout_change_preserves_voice_and_character_assets") is True, "panel-only fixes may regenerate healthy voice/character assets")
    require(repair.get("mouth_anchor_or_sprite_change_preserves_unchanged_voice_and_timing") is True, "mouth-only fixes may regenerate unchanged audio")
    require(repair.get("full_rerender_requires_dependency_invalidation_not_convenience") is True, "full rerender can be chosen for convenience")
    require(repair.get("healthy_verified_artifacts_are_content_hash_reusable") is True, "healthy stage reuse lost")

    incremental = batch.get("incremental_stage_reuse_and_preview") or {}
    require(incremental.get("content_hash_each_stage") is True, "batch media stages are no longer content-addressable")
    require(incremental.get("invalidate_only_changed_stage_and_true_dependents") is True, "batch media invalidation is broader than dependencies")
    require(incremental.get("caption_only_fix_invalidates") == ["caption_overlay", "final_composite_and_mux"], "caption-only invalidation scope drift")
    require(incremental.get("panel_palette_or_layout_fix_invalidates") == ["evidence_and_panel_layer", "final_composite_and_mux"], "panel-only invalidation scope drift")
    require(incremental.get("mouth_anchor_or_character_sprite_fix_invalidates") == ["character_layer", "final_composite_and_mux"], "mouth-only invalidation scope drift")
    require(incremental.get("low_cost_visual_preview_before_expensive_full_render_when_visual_layers_changed") is True, "batch path may skip low-cost visual preview")
    require(incremental.get("preview_failure_blocks_expensive_full_render") is True, "batch path may full-render after preview failure")

    anti = set((media.get("anti_boredom_patterns") or {}).get("forbidden_default") or [])
    require("constant_zoom" in anti, "base policy no longer forbids constant zoom")
    require("sound_effect_on_every_cut" in anti, "base policy no longer forbids sound effect on every cut")
    contextual = media.get("contextual_visuals") or {}
    require(contextual.get("no_universal_switch_every_n_seconds_rule") is True, "base policy now forces fixed scene-change timing")
    sound_grammar = media.get("sound_grammar") or {}
    require("SILENCE" in sound_grammar, "intentional silence grammar disappeared")

    reaction_motion = reaction.get("motion_quality_contract") or {}
    require(reaction_motion.get("vertical_only_motion_is_insufficient") is True, "reaction policy permits vertical-only motion")
    reaction_mouth = reaction.get("mouth_alignment_contract") or {}
    require(reaction_mouth.get("asset_existence_is_not_alignment_proof") is True, "reaction cache treats mouth existence as alignment proof")
    require(reaction_mouth.get("cropped_layer_requires_explicit_anchor_metadata") is True, "cropped mouth may lack anchor metadata")
    require(reaction_mouth.get("exactly_one_active_mouth_layer_per_character") is True, "reaction cache permits double mouth layers")
    require(reaction_mouth.get("visible_alignment_failure_blocks_full_character_render") is True, "reaction cache may render after visible mouth failure")

    common = set(gate.get("common_media_read_set") or [])
    require("config/zundamon_metan_production_quality_policy.json" in common, "media read gate does not restore production quality policy")
    require("docs/ZUNDAMON_METAN_PRODUCTION_QUALITY_STANDARD.md" in common, "media read gate does not restore production quality human standard")
    session = gate.get("new_session_behavior") or {}
    require(session.get("zundamon_metan_production_quality_policy_must_be_re_read") is True, "new sessions may skip production quality policy")
    require(session.get("zundamon_metan_production_quality_human_standard_must_be_re_read") is True, "new sessions may skip production quality doc")

    standards = manifest.get("required_standards") or []
    indexed = {str(x.get("id")): x for x in standards if isinstance(x, dict)}
    item = indexed.get("zundamon-metan-production-quality") or {}
    require(item.get("machine_policy") == "config/zundamon_metan_production_quality_policy.json", "permanent manifest lost production quality policy")
    require(item.get("human_doc") == "docs/ZUNDAMON_METAN_PRODUCTION_QUALITY_STANDARD.md", "permanent manifest lost production quality doc")
    cross_tab = manifest.get("cross_tab_behavior") or {}
    require(cross_tab.get("zundamon_metan_production_quality_survives_tab_change") is True, "production quality standard no longer survives tab change")

    print(json.dumps({
        "status": "PASS",
        "policy": policy.get("schema_version"),
        "ymm4_template": True,
        "voicevox_dictionary": True,
        "mouth_alignment_fixture": True,
        "semantic_caption_segmentation": True,
        "lifted_explainer_panels": True,
        "risk_preview_before_full_render": True,
        "incremental_stage_reuse": True,
        "hook_window_seconds": hook.get("shortform_and_news_explainer_default_hook_window_seconds"),
        "visual_cadence_seconds": cadence.get("shortform_target_seconds_between_meaningful_visual_state_change"),
        "bgm_ducking_nominal_db": mix.get("bgm_ducking_nominal_db"),
        "intentional_silence_preserved": True,
        "fixed_interval_asset_download_forbidden": True
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
