#!/usr/bin/env python3
"""Fail-closed validator for the permanent media command read gate."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load_json(path: str) -> dict[str, Any]:
    obj = json.loads((ROOT / path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise AssertionError(f"{path}: object required")
    return obj


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_paths(paths: list[str], required: set[str], label: str) -> None:
    missing = required - set(paths)
    require(not missing, f"{label}: missing required read paths: {sorted(missing)}")
    for path in required:
        require((ROOT / path).is_file(), f"{label}: repository source missing: {path}")


def main() -> int:
    gate = load_json("config/media_command_read_gate.json")
    manifest = load_json("config/permanent_standards_manifest.json")
    handoff = load_json("config/current_commander_handoff.json")
    media_creative = load_json("config/media_audio_motion_retention_policy.json")
    longform = load_json("config/longform_video_reliability_policy.json")
    objectives = load_json("config/longform_video_objectives.json")
    free_audio = load_json("config/free_audio_source_registry.json")
    dova = load_json("config/dova_curated_bgm_catalog.json")
    pipeline_text = (ROOT / "docs/MEDIA_PIPELINE.md").read_text(encoding="utf-8")

    obsolete_hold_path = ROOT / "config/current_media_execution_state.json"
    require(not obsolete_hold_path.exists(), "obsolete blanket media-production hold state was reintroduced")
    gate_text = json.dumps(gate, ensure_ascii=False)
    require("HOLD_MEDIA_PRODUCTION" not in gate_text, "blanket media-production hold marker was reintroduced into media gate")
    require("current_media_execution_state" not in gate_text, "obsolete media execution-state dependency was reintroduced")

    require(gate.get("status") == "ENFORCED_STANDARD", "media command read gate is not enforced")
    semantic = gate.get("semantic_triggering") or {}
    require(semantic.get("exact_keyword_match_required") is False, "media trigger drifted to exact keyword matching")
    require(semantic.get("classify_by_user_intent") is True, "semantic media intent classification disabled")
    require(semantic.get("mixed_intents_are_additive") is True, "mixed media intents are no longer additive")

    execution = gate.get("execution_gate") or {}
    before = set(execution.get("must_complete_before") or [])
    for phase in (
        "PLANNING_MEDIA_PIPELINE",
        "EXTERNAL_MEDIA_TOOL_CALL",
        "ASSET_FETCH",
        "VOICE_GENERATION",
        "RENDER",
        "PUBLISHING_HANDOFF",
    ):
        require(phase in before, f"read gate no longer precedes {phase}")
    require(execution.get("conversation_memory_alone_is_insufficient") is True, "chat memory became sufficient for media gate")
    require(execution.get("tab_or_session_change_does_not_waive_gate") is True, "tab/session change now waives media gate")
    require(execution.get("blanket_media_production_hold_is_not_part_of_permanent_media_standard") is True, "blanket media production hold became permanent again")

    common = list(gate.get("common_media_read_set") or [])
    require_paths(
        common,
        {
            "config/current_commander_handoff.json",
            "config/permanent_standards_manifest.json",
            "docs/AI_ARMY_MASTER_RULEBOOK.md",
            "config/media_audio_motion_retention_policy.json",
            "config/free_audio_source_registry.json",
            "config/dova_curated_bgm_catalog.json",
            "docs/MEDIA_PIPELINE.md",
            "config/longform_video_objectives.json",
            "config/longform_video_reliability_policy.json",
            "docs/LONGFORM_VIDEO_RELIABILITY_PLAYBOOK.md",
            "docs/AI_ARMY_LONGFORM_RESEARCH_SYNTHESIS_2026-09-12.md",
        },
        "common media read set",
    )

    video = (gate.get("trigger_read_sets") or {}).get("VIDEO_CREATION") or {}
    require_paths(list(video.get("required") or []), {"docs/LONGFORM_VIDEO_OBJECTIVES.md"}, "video creation read set")

    video_know_how = set(((gate.get("know_how_that_must_be_recovered") or {}).get("VIDEO_CREATION") or []))
    for item in (
        "chatgpt_top_commander_and_final_integrator",
        "deepseek_high_value_structure_and_technical_supervisor_not_bulk_coder",
        "specialize_agents_by_pipeline_stage_instead_of_duplicate_same_task_generation",
        "scene_render_checkpoint_join_longform_pattern",
        "content_driven_duration_not_fixed_ten_minutes",
        "VOICEVOX_ZUNDAMON_AND_SHIKOKU_METAN_LOCAL_STANDARD_CAST",
        "stable_anchor_does_not_mean_frozen_character",
        "state_driven_character_motion_instead_of_long_static_portraits",
        "voice_prosody_and_emotion_mapped_to_semantic_beats",
        "one_primary_attention_hero_per_beat",
        "longform_title_chapter_subheading_body_caption_hierarchy",
        "NO_GENERATED_IMAGE_OR_VIDEO_ASSETS_SEARCH_AND_RIGHTS_VERIFIED_COLLECTION_ONLY",
        "NO_PAID_OR_FREEMIUM_OR_TRIAL_CAPTION_OR_VIDEO_EDITING_APPS_OR_SITES",
        "DOVA_OR_OPENTRACKS_PREFERRED_THIRD_PARTY_FREE_BGM_SOURCE",
        "full_narration_caption_coverage",
        "normalized_narration_and_caption_text_coverage_check",
        "rights_manifest_and_asset_provenance",
        "failed_scene_only_retry_and_previous_good_preservation",
        "first_fatal_error_and_root_cause_before_cascade_errors",
        "concat_copy_preferred_when_scene_contract_matches",
        "ffprobe_and_full_decode_machine_QA",
        "finished_mp4_is_completion_not_intermediate_stage_success",
    ):
        require(item in video_know_how, f"video know-how missing: {item}")

    new_session = gate.get("new_session_behavior") or {}
    for key in (
        "master_rulebook_must_be_re_read",
        "media_pipeline_must_be_re_read",
        "longform_objectives_must_be_re_read",
        "longform_reliability_policy_must_be_re_read",
        "audio_motion_retention_policy_must_be_re_read",
        "free_audio_source_registry_must_be_re_read",
        "dova_curated_bgm_catalog_must_be_re_read",
        "do_not_rely_on_prior_tab_summary_as_substitute",
    ):
        require(new_session.get(key) is True, f"new session continuity lost: {key}")

    creative_default = media_creative.get("semantic_default") or {}
    creative_durability = media_creative.get("durability") or {}
    voice = media_creative.get("voice_prosody") or {}
    motion = media_creative.get("character_motion") or {}
    collision = media_creative.get("collision_avoidance") or {}
    visual_acquisition = media_creative.get("visual_asset_acquisition") or {}
    editing = media_creative.get("editing_tool_policy") or {}
    require(creative_default.get("auto_apply_on_media_intent") is True, "creative standard no longer auto-applies")
    require(creative_default.get("user_does_not_need_to_repeat_rules") is True, "creative standard now requires repeated user instruction")
    require(creative_default.get("blanket_media_production_hold_is_not_a_default_rule") is True, "creative standard reintroduced media hold")
    require(creative_durability.get("must_be_re_read_after_new_tab_or_session") is True, "creative standard cross-tab reread lost")
    require(set(voice.get("durable_standard_cast") or []) == {"ずんだもん", "四国めたん"}, "VOICEVOX durable cast drifted")
    require(voice.get("default_primary_voice") == "ずんだもん", "Zundamon default voice rule drifted")
    require(voice.get("secondary_voice_when_dialogue_helps") == "四国めたん", "Shikoku Metan dialogue rule drifted")
    require(motion.get("no_long_static_talking_portrait") is True, "anti-static character motion rule drifted")
    require(motion.get("stable_anchor_does_not_mean_frozen_character") is True, "stable-anchor/frozen-character distinction drifted")
    require(collision.get("max_attention_dominant_elements_per_beat") == 1, "one-primary-hero collision rule drifted")
    require(collision.get("never_stack_major_sfx_major_zoom_major_caption_pop_and_character_entry_without_explicit_reason") is True, "major-effect collision guard drifted")
    require(visual_acquisition.get("generated_image_assets_allowed") is False, "generated image assets were re-enabled")
    require(visual_acquisition.get("generated_video_assets_allowed") is False, "generated video assets were re-enabled")
    require(visual_acquisition.get("default_mode") == "SEARCH_DISCOVER_THEN_VERIFY_AND_MATERIALIZE", "search-collected visual default drifted")
    require(editing.get("paid_video_editing_apps_or_sites_allowed") is False, "paid video editing app/site was re-enabled")
    require(editing.get("paid_caption_apps_or_sites_allowed") is False, "paid caption app/site was re-enabled")
    require(editing.get("freemium_or_trial_credit_video_editing_allowed") is False, "freemium/trial video editing was re-enabled")
    require(editing.get("freemium_or_trial_credit_captioning_allowed") is False, "freemium/trial captioning was re-enabled")

    require(longform.get("status") == "PERMANENT_STANDARD", "longform reliability is no longer permanent")
    division = longform.get("ai_division") or {}
    require(division.get("top_commander") == "CHATGPT_WORK", "ChatGPT top commander rule drifted")
    require(division.get("executive_supervisor") == "DEEPSEEK", "DeepSeek supervisor rule drifted")
    require(division.get("deepseek_is_not_bulk_coder") is True, "DeepSeek was turned into bulk coder")
    require(division.get("specialize_by_stage_instead_of_duplicate_generation") is True, "AI stage specialization rule drifted")
    visual = longform.get("visual_asset_policy") or {}
    require(visual.get("generated_image_assets") is False, "longform generated image assets re-enabled")
    require(visual.get("generated_video_assets") is False, "longform generated video assets re-enabled")
    require(visual.get("default_source") == "SEARCH_AND_SOURCE_COLLECTION", "longform visual sourcing drifted")
    paid = longform.get("paid_policy") or {}
    require(paid.get("caption_or_video_editing_paid_app_execution") is False, "paid editing app execution re-enabled")
    require(paid.get("caption_or_video_editing_paid_site_execution") is False, "paid editing site execution re-enabled")
    require(paid.get("caption_or_video_editing_trial_or_temporary_free_execution") is False, "trial/temporary-free editing execution re-enabled")
    voicevox = longform.get("voicevox_contract") or {}
    require(set(voicevox.get("standard_cast") or []) == {"ずんだもん", "四国めたん"}, "longform VOICEVOX standard cast drifted")
    content = longform.get("content_design") or {}
    require(content.get("target_duration_is_content_driven_not_fixed") is True, "longform duration became fixed")
    require(content.get("padding_by_rephrasing_or_repetition") is False, "longform padding by repetition re-enabled")
    character = longform.get("character_contract") or {}
    require(character.get("stable_anchor_preferred_for_longform_news_or_explainer") is True, "stable anchor preference drifted")
    require(character.get("long_frozen_portrait_allowed") is False, "long frozen portrait re-enabled")
    final_validation = longform.get("final_validation") or {}
    require(final_validation.get("heavy_ai_full_video_visual_review_required_before_handoff") is False, "heavy AI visual review became mandatory before handoff")

    require(objectives.get("status") == "ENFORCED_STANDARD", "longform objectives no longer enforced")
    asset_objectives = objectives.get("asset_objectives") or {}
    require(asset_objectives.get("generated_image_asset_count") == 0, "generated image objective no longer zero")
    require(asset_objectives.get("generated_video_asset_count") == 0, "generated video objective no longer zero")
    voice_objectives = objectives.get("voice_objectives") or {}
    require(set(voice_objectives.get("standard_cast") or []) == {"ずんだもん", "四国めたん"}, "objective VOICEVOX cast drifted")
    cost_objectives = objectives.get("cost_and_route_objectives") or {}
    require(cost_objectives.get("paid_caption_or_video_editing_app_execution_count") == 0, "paid caption/video editing objective no longer zero")
    require(cost_objectives.get("trial_or_temporary_free_caption_or_video_editing_execution_count") == 0, "trial editing objective no longer zero")
    require((objectives.get("continuity_objectives") or {}).get("blanket_media_production_hold_allowed_as_permanent_standard") is False, "objectives reintroduced permanent production hold")

    # Human documentation must state both the engine and the two durable voices.
    # Do not require a brittle exact concatenation such as "VOICEVOXずんだもん".
    require("VOICEVOX" in pipeline_text and "ずんだもん" in pipeline_text, "media pipeline lost VOICEVOX Zundamon")
    require("VOICEVOX" in pipeline_text and "四国めたん" in pipeline_text, "media pipeline lost VOICEVOX Shikoku Metan")
    require("画像生成" in pipeline_text and "動画生成" in pipeline_text, "media pipeline missing generated-asset prohibition documentation")
    require("Scene" in pipeline_text and "Checkpoint" in pipeline_text, "media pipeline lost scene/checkpoint production pattern")

    require(free_audio.get("status") == "ENFORCED_SOURCE_REGISTRY", "free audio source registry is not enforced")
    source_ids = {str(x.get("id")) for x in (free_audio.get("sources") or []) if isinstance(x, dict)}
    require("dova-syndrome" in source_ids, "DOVA missing from free audio source registry")
    require(dova.get("status") == "ENFORCED_MEDIA_DEFAULT", "DOVA curated catalog is not enforced")

    manifest_gate = manifest.get("media_command_gate") or {}
    require(manifest_gate.get("policy") == "config/media_command_read_gate.json", "permanent manifest lost media gate policy")
    require(manifest_gate.get("conversation_memory_is_not_a_substitute") is True, "manifest allows chat memory to replace media source read")
    require(manifest_gate.get("re_read_current_repository_versions_after_tab_or_session_change") is True, "manifest no longer requires cross-tab media reread")
    require(manifest_gate.get("audio_motion_retention_policy") == "config/media_audio_motion_retention_policy.json", "manifest lost creative policy pointer")
    require(manifest_gate.get("free_audio_source_registry") == "config/free_audio_source_registry.json", "manifest lost free audio registry pointer")
    require(manifest_gate.get("dova_curated_bgm_catalog") == "config/dova_curated_bgm_catalog.json", "manifest lost DOVA catalog pointer")
    require(manifest_gate.get("video_creation_reads_audio_motion_caption_contextual_visual_rules") is True, "manifest no longer requires creative media rules")
    require(manifest_gate.get("background_music_prefers_curated_verified_free_sources") is True, "manifest lost curated free BGM preference")

    standards = manifest.get("required_standards") or []
    by_id = {entry.get("id"): entry for entry in standards if isinstance(entry, dict)}
    media_entries = [entry for entry in standards if isinstance(entry, dict) and entry.get("id") == "media-command-read-gate"]
    require(len(media_entries) == 1, "media command read gate must appear exactly once")
    require(media_entries[0].get("priority") == 0, "media command read gate must remain priority 0")
    for standard_id, policy_path in (
        ("media-audio-motion-retention", "config/media_audio_motion_retention_policy.json"),
        ("free-audio-source-registry", "config/free_audio_source_registry.json"),
        ("dova-curated-bgm-catalog", "config/dova_curated_bgm_catalog.json"),
        ("longform-video-objectives", "config/longform_video_objectives.json"),
        ("longform-video-reliability", "config/longform_video_reliability_policy.json"),
    ):
        require(standard_id in by_id, f"permanent standards lost {standard_id}")
        require(by_id[standard_id].get("machine_policy") == policy_path, f"permanent standard path drift: {standard_id}")
        require(by_id[standard_id].get("priority") == 1, f"permanent standard priority drift: {standard_id}")

    cross_tab = manifest.get("cross_tab_behavior") or {}
    require(cross_tab.get("priority_zero_manifest_is_expandable_startup_index") is True, "priority-zero startup index drift")
    require(cross_tab.get("do_not_duplicate_full_required_standard_list_into_commander_handoff") is True, "handoff duplication guard drift")
    require(cross_tab.get("media_command_read_gate_survives_tab_change") is True, "media gate no longer survives tab change")
    require(cross_tab.get("media_task_re_reads_repository_know_how_before_media_work") is True, "media rules no longer re-read on media work")

    continuity = handoff.get("continuity") or {}
    read_order = continuity.get("on_new_session_required_read_order") or []
    require("config/permanent_standards_manifest.json" in read_order, "new session no longer reads permanent standards manifest")
    require(continuity.get("repository_is_source_of_truth") is True, "repository is no longer handoff source of truth")

    print(json.dumps({
        "status": "PASS",
        "media_gate": "ENFORCED_STANDARD",
        "cross_tab_reread": True,
        "blanket_media_production_hold_absent": True,
        "ai_division_longform_rules": True,
        "zundamon_metan_voice_rules": True,
        "search_only_visual_assets": True,
        "no_paid_freemium_trial_editing": True,
        "scene_checkpoint_join": True,
        "character_motion_rules": True,
        "effect_collision_guard": True,
        "free_audio_registry_reread": True,
        "dova_catalog_reread": True,
        "final_mp4_completion_contract": True,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
