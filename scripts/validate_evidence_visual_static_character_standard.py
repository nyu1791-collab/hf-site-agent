#!/usr/bin/env python3
"""Validate evidence-visual and character-performance contracts without bloating routine media restore."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config/evidence_visual_static_character_policy.json"
SOURCE_POLICY = ROOT / "config/media_source_policy.json"
READ_GATE = ROOT / "config/media_command_read_gate.json"
MANIFEST = ROOT / "config/permanent_standards_manifest.json"
DOC = ROOT / "docs/EVIDENCE_VISUAL_AND_STATIC_CHARACTER_STANDARD.md"
SYNTH = ROOT / "scripts/synthesize_longform_voicevox.py"
CAPTION_VALIDATOR = ROOT / "scripts/validate_video_caption_contract.py"


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
    manifest = load(MANIFEST)

    require(policy.get("schema_version") == "evidence-visual-static-character-v1", "evidence visual policy schema drift")
    require(policy.get("status") == "MANDATORY_MEDIA_STANDARD", "evidence visual policy is not enforced when applicable")
    for required_file in (DOC, ROOT / "scripts/render_reusable_short.py", ROOT / "scripts/render_reusable_longform.py", SYNTH, CAPTION_VALIDATOR):
        require(required_file.is_file(), f"required media file missing: {required_file}")

    visual = policy.get("evidence_visual_acquisition") or {}
    require(visual.get("default_mode") == "SEARCH_PRIMARY_OR_OFFICIAL_EVIDENCE_VISUAL_FIRST", "evidence visual path is not primary/search first")
    require(visual.get("generated_background_image_default") is False, "generated factual background became default")
    require(visual.get("generated_evidence_image_default") is False, "generated evidence image became default")
    require(visual.get("generated_visual_must_not_be_used_as_factual_evidence") is True, "generated visual may act as factual evidence")
    require(visual.get("real_photo_or_primary_screenshot_preferred_when_available") is True, "real/primary visual preference missing")
    require(visual.get("source_page_required") is True and visual.get("asset_locator_required") is True, "visual provenance floor weakened")
    require(visual.get("unknown_rights_block_public_use") is True, "unknown rights may enter public use")
    require(visual.get("media_region_only_required") is True and visual.get("whole_page_or_feed_as_default") is False, "media-region capture contract drift")

    voice = policy.get("voice_delivery") or {}
    require(float(voice.get("voicevox_speed_scale") or 0) == 1.2, "VOICEVOX speed default drift")
    require(voice.get("actual_generated_wav_must_be_remeasured_after_speed_change") is True, "measured timing after speed change disabled")
    require(voice.get("timeline_and_caption_cues_follow_measured_audio") is True, "captions no longer follow measured audio")

    captions = policy.get("caption_rendering") or {}
    require(captions.get("full_spoken_text_contract") == "FULL_SPOKEN_TEXT", "full spoken caption contract missing")
    require(float(captions.get("caption_coverage_ratio_minimum") or 0) == 1.0, "caption coverage floor weakened")
    require(captions.get("speaker_colored_caption_text_required") is True, "speaker caption colors disabled")
    require(captions.get("zundamon_caption_text_color_role") == "PALE_MINT_GREEN", "Zundamon caption color drift")
    require(captions.get("metan_caption_text_color_role") == "PALE_ROSE_PINK", "Metan caption color drift")
    require(captions.get("automatic_keyword_highlighting", False) is False, "automatic keyword highlighting re-enabled")

    character = policy.get("character_rendering") or {}
    require(character.get("default_mode") == "SPEECH_SYNC_MOUTH_PLUS_SPARSE_SEMANTIC_EXPRESSION", "character mode drift")
    for field in ("mouth_animation", "automatic_lipsync", "expression_swap_during_normal_dialogue", "expression_change_must_follow_authored_or_typed_semantic_state"):
        require(character.get(field) is True, f"character performance disabled: {field}")
    for field in ("character_idle_animation", "blink_animation", "body_bob_or_vertical_bounce", "reaction_symbol_animation", "entry_exit_animation_per_line", "continuous_zoom_or_pan_on_character"):
        require(character.get(field) is False, f"unrequested character motion re-enabled: {field}")

    efficiency = policy.get("production_efficiency") or {}
    require(efficiency.get("canonical_dynamic_short_renderer") == "scripts/render_reusable_short.py", "dynamic short renderer drift")
    require(efficiency.get("canonical_dynamic_longform_renderer") == "scripts/render_reusable_longform.py", "dynamic longform renderer drift")
    require(efficiency.get("full_spoken_caption_validator") == "scripts/validate_video_caption_contract.py", "caption validator drift")

    source_policy = source.get("policy") or {}
    require(source.get("generated_images_enabled_by_default") is False, "source policy re-enabled generated images")
    require(source_policy.get("search_first") is True, "source policy is not search-first")
    require(source_policy.get("unknown_rights_blocked") is True, "source policy no longer blocks unknown rights")

    # This detailed evidence/character standard is intentionally not part of
    # every routine video restore. It remains indexed by the permanent media
    # command registry and is loaded by scoped evidence/character profiles.
    common = set(gate.get("common_media_read_set") or [])
    require("config/evidence_visual_static_character_policy.json" not in common, "heavy evidence policy leaked into routine hot path")
    media_index = manifest.get("media_command_gate") or {}
    require(media_index.get("shortform_animation_exception_policy") == "config/evidence_visual_static_character_policy.json", "manifest lost evidence/animation policy pointer")
    require((manifest.get("cross_tab_behavior") or {}).get("related_visual_provenance_contract_survives_tab_change") is True, "visual provenance continuity lost")

    hard = set(policy.get("hard_fail_conditions") or [])
    require("VISIBLE_SPEAKING_CHARACTER_WITHOUT_MEASURED_MOUTH_MOTION" in hard, "missing measured-mouth failure condition")
    require("VISIBLE_CHARACTERS_WITHOUT_AUTHORED_EXPRESSION_CHANGES" in hard, "missing expression failure condition")
    require("SPOKEN_TEXT_MISSING_FROM_CAPTIONS" in hard, "missing caption coverage failure condition")

    synth = SYNTH.read_text(encoding="utf-8")
    require("DEFAULT_SPEED_SCALE=1.20" in synth, "synthesizer lost 1.20 speed default")
    require('query["speedScale"]=args.speed_scale' in synth, "VOICEVOX query lost configured speed")

    print(json.dumps({
        "status": "PASS",
        "policy": policy.get("schema_version"),
        "voicevox_speed_scale": voice.get("voicevox_speed_scale"),
        "character_mode": character.get("default_mode"),
        "routine_hot_path_added": False,
        "provenance_floor": "ENFORCED_WHEN_APPLICABLE",
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
