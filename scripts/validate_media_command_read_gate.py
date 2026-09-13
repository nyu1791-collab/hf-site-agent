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
    require("PLANNING_MEDIA_PIPELINE" in before, "read gate no longer precedes media planning")
    require("EXTERNAL_MEDIA_TOOL_CALL" in before, "read gate no longer precedes external media tool calls")
    require("ASSET_FETCH" in before, "read gate no longer precedes asset fetch")
    require("VOICE_GENERATION" in before, "read gate no longer precedes voice generation")
    require("RENDER" in before, "read gate no longer precedes render")
    require("PUBLISHING_HANDOFF" in before, "read gate no longer precedes publishing handoff")
    require(execution.get("conversation_memory_alone_is_insufficient") is True, "chat memory became sufficient for media gate")
    require(execution.get("tab_or_session_change_does_not_waive_gate") is True, "tab/session change now waives media gate")
    require(execution.get("measurement_plan_required_for_optimization_claim") is True, "optimization may be claimed without measurement plan")

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
            "config/cross_source_knowhow_evidence_matrix.json",
            "config/cross_domain_measurement_registry.json",
            "config/cross_source_second_pass_policy.json",
            "config/second_pass_artifact_contracts.json",
            "docs/CROSS_SOURCE_KNOWHOW_ADJUDICATION_2026-09-13.md",
            "docs/CROSS_SOURCE_SECOND_PASS_2026-09-13.md",
            "docs/MEDIA_PIPELINE.md",
            "config/longform_video_objectives.json",
            "config/longform_video_reliability_policy.json",
            "docs/LONGFORM_VIDEO_RELIABILITY_PLAYBOOK.md",
            "docs/AI_ARMY_LONGFORM_RESEARCH_SYNTHESIS_2026-09-12.md",
        },
        "common media read set",
    )

    trigger_sets = gate.get("trigger_read_sets") or {}
    video = trigger_sets.get("VIDEO_CREATION") or {}
    clip = trigger_sets.get("CLIPPING_REPURPOSING") or {}
    shop = trigger_sets.get("TIKTOK_SHOP_COMMERCE") or {}

    require_paths(list(video.get("required") or []), {"docs/LONGFORM_VIDEO_OBJECTIVES.md"}, "video creation read set")
    require_paths(
        list(clip.get("required") or []),
        {
            "config/authorized_clipping_monetization_policy.json",
            "docs/AUTHORIZED_CLIPPING_AND_MONETIZATION_PLAYBOOK.md",
            "config/batch_media_orchestration_policy.json",
            "docs/BATCH_MEDIA_ORCHESTRATION.md",
            "docs/MEDIA_BATCH_COMMAND_CENTER.md",
        },
        "clipping read set",
    )
    require_paths(
        list(shop.get("required") or []),
        {"config/tiktok_shop_influence_policy.json", "docs/TIKTOK_SHOP_INFLUENCE_PLAYBOOK.md"},
        "TikTok Shop read set",
    )
    conditional_shop = (shop.get("conditional_required") or {}).get("if_existing_or_third_party_media_is_repurposed") or []
    require_paths(
        list(conditional_shop),
        {"config/authorized_clipping_monetization_policy.json", "docs/AUTHORIZED_CLIPPING_AND_MONETIZATION_PLAYBOOK.md"},
        "TikTok Shop repurposing conditional read set",
    )

    video_know_how = set(((gate.get("know_how_that_must_be_recovered") or {}).get("VIDEO_CREATION") or []))
    for required_know_how in (
        "state_driven_character_motion_instead_of_long_static_portraits",
        "voice_prosody_and_emotion_mapped_to_semantic_beats",
        "one_primary_attention_hero_per_beat",
        "large_semantically_chunked_captions_with_baseline_emphasis_peak_hierarchy",
        "one_strongest_keyword_or_phrase_per_beat_by_default",
        "no_background_prose_or_headline_wall",
        "contextual_visuals_switch_on_semantic_events_not_fixed_intervals",
        "evidence_visuals_and_illustrative_visuals_are_distinct",
        "search_engine_result_is_discovery_not_license",
        "DOVA_OR_OPENTRACKS_PREFERRED_THIRD_PARTY_FREE_BGM_SOURCE",
        "Noraneko_wa_Uchu_wo_Mezashita_is_high_priority_candidate_when_mood_fits",
        "rotate_bgm_across_videos_and_avoid_mechanical_repetition",
        "bgm_ducking_and_sfx_collision_avoidance_preserve_narration_intelligibility",
        "sfx_has_semantic_grammar_and_silence_is_first_class_event",
        "full_narration_caption_coverage",
        "caption_safe_zones_and_visual_hierarchy",
        "rights_manifest_and_asset_provenance",
        "failed_scene_only_retry_and_previous_good_preservation",
        "ffprobe_and_full_decode_machine_QA",
    ):
        require(required_know_how in video_know_how, f"video creative know-how missing: {required_know_how}")

    new_session = gate.get("new_session_behavior") or {}
    require(new_session.get("master_rulebook_must_be_re_read") is True, "new session lost master rulebook reread")
    require(new_session.get("audio_motion_retention_policy_must_be_re_read") is True, "new session lost creative standard reread")
    require(new_session.get("free_audio_source_registry_must_be_re_read") is True, "new session lost free audio registry reread")
    require(new_session.get("dova_curated_bgm_catalog_must_be_re_read") is True, "new session lost DOVA catalog reread")
    require(new_session.get("do_not_rely_on_prior_tab_summary_as_substitute") is True, "prior tab summary became substitute for repository reread")

    creative_default = media_creative.get("semantic_default") or {}
    creative_durability = media_creative.get("durability") or {}
    voice = media_creative.get("voice_prosody") or {}
    motion = media_creative.get("character_motion") or {}
    collision = media_creative.get("collision_avoidance") or {}
    require(creative_default.get("auto_apply_on_media_intent") is True, "creative standard no longer auto-applies")
    require(creative_default.get("user_does_not_need_to_repeat_rules") is True, "creative standard now requires repeated user instruction")
    require(creative_durability.get("must_be_re_read_after_new_tab_or_session") is True, "creative standard cross-tab reread lost")
    require(voice.get("default_primary_voice") == "ずんだもん", "Zundamon default voice rule drifted")
    require(voice.get("secondary_voice_when_dialogue_helps") == "四国めたん", "Shikoku Metan dialogue rule drifted")
    require(motion.get("no_long_static_talking_portrait") is True, "anti-static character motion rule drifted")
    require(motion.get("motion_is_state_driven") is True, "state-driven character motion rule drifted")
    require(collision.get("max_attention_dominant_elements_per_beat") == 1, "one-primary-hero collision rule drifted")
    require(collision.get("never_stack_major_sfx_major_zoom_major_caption_pop_and_character_entry_without_explicit_reason") is True, "major-effect collision guard drifted")

    manifest_gate = manifest.get("media_command_gate") or {}
    require(manifest_gate.get("policy") == "config/media_command_read_gate.json", "permanent manifest lost media gate policy")
    require(manifest_gate.get("human_doc") == "docs/MEDIA_COMMAND_READ_GATE.md", "permanent manifest lost media gate doc")
    require(manifest_gate.get("mixed_media_intents_are_additive") is True, "manifest mixed-intent media rule missing")
    require(manifest_gate.get("conversation_memory_is_not_a_substitute") is True, "manifest allows chat memory to replace media source read")
    require(manifest_gate.get("re_read_current_repository_versions_after_tab_or_session_change") is True, "manifest no longer requires cross-tab media reread")
    require(manifest_gate.get("cross_source_evidence_and_measurement_rules_are_required") is True, "manifest lost cross-source media governance")
    require(manifest_gate.get("audio_motion_retention_policy") == "config/media_audio_motion_retention_policy.json", "manifest lost creative policy pointer")
    require(manifest_gate.get("free_audio_source_registry") == "config/free_audio_source_registry.json", "manifest lost free audio registry pointer")
    require(manifest_gate.get("dova_curated_bgm_catalog") == "config/dova_curated_bgm_catalog.json", "manifest lost DOVA catalog pointer")
    require(manifest_gate.get("video_creation_reads_audio_motion_caption_contextual_visual_rules") is True, "manifest no longer requires creative media rules")
    require(manifest_gate.get("background_music_prefers_curated_verified_free_sources") is True, "manifest lost curated free BGM preference")

    standards = manifest.get("required_standards") or []
    by_id = {entry.get("id"): entry for entry in standards if isinstance(entry, dict)}
    media_entries = [entry for entry in standards if isinstance(entry, dict) and entry.get("id") == "media-command-read-gate"]
    require(len(media_entries) == 1, "media command read gate must appear exactly once in permanent required standards")
    require(media_entries[0].get("priority") == 0, "media command read gate must remain priority 0")
    for standard_id, policy_path in (
        ("media-audio-motion-retention", "config/media_audio_motion_retention_policy.json"),
        ("free-audio-source-registry", "config/free_audio_source_registry.json"),
        ("dova-curated-bgm-catalog", "config/dova_curated_bgm_catalog.json"),
    ):
        require(standard_id in by_id, f"permanent standards lost {standard_id}")
        require(by_id[standard_id].get("machine_policy") == policy_path, f"permanent standard path drift: {standard_id}")
        require(by_id[standard_id].get("priority") == 1, f"permanent standard priority drift: {standard_id}")

    read_order = ((handoff.get("continuity") or {}).get("on_new_session_required_read_order") or [])
    require("config/permanent_standards_manifest.json" in read_order, "new session no longer reads permanent standards manifest")
    require((handoff.get("continuity") or {}).get("repository_is_source_of_truth") is True, "repository is no longer handoff source of truth")

    print(json.dumps({
        "status": "PASS",
        "media_gate": "ENFORCED_STANDARD",
        "semantic_intent": True,
        "mixed_intents_additive": True,
        "cross_tab_reread": True,
        "blanket_media_production_hold_absent": True,
        "creative_standard_reread": True,
        "zundamon_metan_voice_rules": True,
        "character_motion_rules": True,
        "effect_collision_guard": True,
        "free_audio_registry_reread": True,
        "dova_catalog_reread": True,
        "cross_source_evidence_read": True,
        "measurement_registry_read": True,
        "video_creation_know_how": True,
        "clipping_know_how": True,
        "tiktok_shop_know_how": True,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
