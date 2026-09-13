#!/usr/bin/env python3
"""Fail closed on regressions to the permanent character-visual/news-background standard."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config/media_audio_motion_retention_policy.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    require(policy.get("schema_version") == "media-audio-motion-retention-v4", "media creative policy must remain v4 or be deliberately migrated with this validator")
    require(policy.get("status") == "ENFORCED_STANDARD", "media creative policy is not enforced")

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
    ):
        require(item in qa, f"media QA contract missing: {item}")

    print(json.dumps({
        "status": "PASS",
        "generated_image_ai_default": False,
        "actual_cast_visuals_required": True,
        "character_motion_required": True,
        "waveform_character_substitution": False,
        "news_background_mode": visual.get("news_background_default_mode"),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
