#!/usr/bin/env python3
"""Validate the scoped shortform Zundamon template and its cross-tab wiring.

News60 is intentionally conditional. Routine VIDEO_CREATION must not pay the
restore/coordination cost unless the user requests the 55–60 second Zundamon
news-short profile.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "config/zundamon_news60_template.json"
DOC = ROOT / "docs/ZUNDAMON_NEWS60_TEMPLATE.md"
GATE = ROOT / "config/media_command_read_gate.json"
ADMISSION = ROOT / "config/video_creation_admission_policy.json"
STATIC_POLICY = ROOT / "config/evidence_visual_static_character_policy.json"
MEDIA_HANDOFF = ROOT / "config/current_media_quality_handoff.json"
MANIFEST = ROOT / "config/permanent_standards_manifest.json"
YMM4_ROUTINE = ROOT / "config/ymm4_news60_routine.json"
YMM4_DOC = ROOT / "docs/YMM4_NEWS60_ROUTINE.md"
YMM4_SCAFFOLD = ROOT / "examples/ymm4_news60_script_template.json"
YMM4_PREP = ROOT / "scripts/prepare_ymm4_news60_routine.py"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path}: object required")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate() -> dict[str, Any]:
    template = load(TEMPLATE)
    gate = load(GATE)
    admission = load(ADMISSION)
    static_policy = load(STATIC_POLICY)
    media_handoff = load(MEDIA_HANDOFF)
    manifest = load(MANIFEST)
    routine = load(YMM4_ROUTINE)
    scaffold = load(YMM4_SCAFFOLD)

    require(DOC.is_file(), "shortform template human prompt is missing")
    require(YMM4_DOC.is_file() and YMM4_PREP.is_file(), "YMM4 routine documentation or preparation command is missing")
    require(template.get("schema_version") == "zundamon-news60-template-v1", "shortform template version drift")
    require(template.get("status") == "ENFORCED_DEFAULT_FOR_SCOPED_SHORTFORM", "shortform template is not enforced for its scope")

    output = template.get("output") or {}
    require(output.get("target_duration_seconds") == [55, 60], "shortform duration scope drift")
    require(output.get("aspect_ratio") == "9:16" and output.get("resolution") == [1080, 1920], "shortform output geometry drift")
    require(output.get("public_publish_requires_explicit_user_approval") is True, "public publish approval guard missing")

    production = template.get("production_time") or {}
    # Template-local historical observation may remain 10–15 minutes; the
    # current global speed policy owns the five-minute work target. Do not let
    # this scoped template override the active speed authority.
    require(production.get("is_goal_not_guarantee") is True, "template production target became a guarantee")
    require(production.get("max_independent_preparation_lanes") == 3, "shortform preparation lane ceiling drift")
    require(production.get("one_final_encode") is True, "one-pass final encode requirement missing")

    beat_ids = [str(x.get("id")) for x in (template.get("story_beats") or [])]
    require(beat_ids == ["HOOK", "WHAT_CHANGED", "WHY_IT_HAPPENED", "EVIDENCE", "LIMIT_OR_CAVEAT", "TAKEAWAY"], "shortform beat sequence drift")

    require(routine.get("schema_version") == "ymm4-news60-routine-v1" and routine.get("status") == "PREPRODUCTION_BLUEPRINT", "YMM4 routine blueprint drift")
    require(routine.get("baseline_must_be_created_and_checked_on_windows") is True, "unverified YMM4 baseline was promoted")
    require(routine.get("target_around_ten_minutes_is_conditional_not_guaranteed") is True, "YMM4 target was made a guarantee")
    require(routine.get("no_network_paid_call_audio_render_or_video_from_preparation") is True, "YMM4 preparation side-effect guard missing")
    require([line.get("semantic_beat_id") for line in (scaffold.get("dialogue") or [])] == beat_ids, "YMM4 scaffold beat order drift")
    require(scaffold.get("title") is None and all(line.get("voice_text") is None and line.get("caption_text") is None for line in scaffold["dialogue"]), "YMM4 scaffold no longer blocks unfilled scripts")

    script = template.get("script_contract") or {}
    require(script.get("full_spoken_text_caption_contract") == "FULL_SPOKEN_TEXT", "full-spoken caption contract missing")
    require(script.get("caption_timing_source") == "MEASURED_LOCAL_VOICEVOX_WAV", "caption timing is not tied to measured voice")
    required_dialogue = set(script.get("required_dialogue_fields") or [])
    require({"speaker", "voice_text", "caption_text", "emotion", "semantic_beat_id", "visual_beat", "source_claim_ids", "emphasis_terms", "emphasis_reason"}.issubset(required_dialogue), "structured dialogue fields incomplete")

    colors = template.get("caption_color") or {}
    require(colors.get("speaker_identity_colors") == {"ずんだもん": "#B8E6C8", "四国めたん": "#F2C4D7"}, "speaker caption color mapping drift")
    require(colors.get("automatic_keyword_highlighting") is False, "automatic keyword highlighting was re-enabled")
    require(colors.get("default_special_highlights_per_video") == 0, "special color is no longer opt-in")
    require(colors.get("maximum_special_highlights_per_semantic_beat") == 1, "too many special highlights per beat")
    require(colors.get("maximum_special_highlights_in_60_second_video") == 3, "60-second special highlight ceiling drift")
    require(colors.get("reason_required_for_every_special_highlight") is True, "highlight reason is not required")

    acting = template.get("character_performance") or {}
    require(acting.get("default_for_this_template") == "SPEAKING_MOUTH_SYNC_PLUS_SPARSE_SEMANTIC_EXPRESSION", "template acting mode drift")
    require(acting.get("mouth_animation_required_for_each_speaking_character") is True, "speaking-character mouth motion was disabled")
    require({"closed", "small_open", "open"}.issubset(set(acting.get("mouth_states") or [])), "required mouth states are incomplete")
    require("VOICEVOX" in str(acting.get("mouth_timing_source")), "mouth timing is not tied to VOICEVOX/audio")
    require(acting.get("semantic_expression_changes_required") is True, "semantic facial expression changes were disabled")
    require(acting.get("static_turn_focus_renderer_is_not_valid_for_this_profile") is True, "static renderer remains valid for the animated template")
    require(acting.get("fixture_must_pass_before_full_render_when_asset_or_anchor_identity_changes") is True, "full-face fixture gate is missing")

    approved = (static_policy.get("template_scoped_overrides") or {}).get("approved_profiles") or []
    profile = next((x for x in approved if x.get("template_id") == "zundamon_news60"), None)
    require(profile is not None, "static-character policy has no scoped shortform animation exception")
    require(profile.get("mouth_animation") is True and profile.get("semantic_facial_expression_changes") is True, "template motion exception is incomplete")
    require((static_policy.get("character_rendering") or {}).get("default_mode") == "SPEECH_SYNC_MOUTH_PLUS_SPARSE_SEMANTIC_EXPRESSION", "current character performance default drift")

    required_paths = {
        "config/zundamon_news60_template.json",
        "docs/ZUNDAMON_NEWS60_TEMPLATE.md",
        "scripts/validate_zundamon_news60_template.py",
    }
    video = ((gate.get("trigger_sets") or {}).get("VIDEO_CREATION") or {})
    video_required = set(video.get("required") or [])
    conditional = video.get("conditional") or {}
    scoped_shortform = set(conditional.get("if_user_requests_55_to_60_second_zundamon_news_short") or [])
    require(required_paths.issubset(scoped_shortform), "scoped News60 intent cannot restore the template contract")
    require(not required_paths.intersection(video_required), "News60 template leaked into every VIDEO_CREATION request")

    # Generic admission restores the speed-first foundation. News60 is layered
    # only after semantic intent resolution, so it must not inflate the generic
    # admission read set.
    admission_ref = admission.get("required_read_set_ref")
    require(admission_ref == "config/media_command_read_gate.json#/speed_first_delivery_override/read_set", "video admission no longer points at the canonical speed-first read set")

    standard = next((x for x in (manifest.get("required_standards") or []) if x.get("id") == "zundamon-news60-template"), None)
    require(standard is not None, "permanent manifest does not index the shortform template")
    require(standard.get("machine_policy") == "config/zundamon_news60_template.json", "manifest template policy path drift")
    require(standard.get("human_doc") == "docs/ZUNDAMON_NEWS60_TEMPLATE.md", "manifest prompt path drift")
    require(standard.get("validator") == "scripts/validate_zundamon_news60_template.py", "manifest template validator path drift")

    routine_paths = {
        "blueprint": "config/ymm4_news60_routine.json",
        "human_doc": "docs/YMM4_NEWS60_ROUTINE.md",
        "script_scaffold": "examples/ymm4_news60_script_template.json",
        "preparation_command": "scripts/prepare_ymm4_news60_routine.py",
    }
    require(standard.get("ymm4_routine") == routine_paths, "manifest YMM4 routine index drift")
    cross_tab = manifest.get("cross_tab_behavior") or {}
    require(cross_tab.get("zundamon_news60_template_survives_tab_change") is True, "shortform template cross-tab persistence missing")

    # The media handoff is a compact pointer summary. It should expose News60
    # through conditional references instead of carrying a duplicated global
    # authoritative file list.
    require(media_handoff.get("status") == "CURRENT_CROSS_TAB_CONTINUITY_SUMMARY", "media handoff is not compact continuity summary")
    shortform_ref = ((media_handoff.get("conditional_references") or {}).get("shortform") or {})
    require(shortform_ref.get("scope") == "ONLY_WHEN_USER_REQUESTS_55_TO_60_SECOND_ZUNDAMON_NEWS_SHORT", "media handoff shortform scope drift")
    require(required_paths.issubset(set(shortform_ref.get("files") or [])), "media handoff cannot restore scoped News60 files")

    return {
        "status": "PASS",
        "template": template["schema_version"],
        "duration_seconds": output["target_duration_seconds"],
        "highlight_policy": "EXPLICIT_ONLY_MAX_3",
        "voice_synchronized_mouth_motion": True,
        "semantic_facial_expression_changes": True,
        "cross_tab_read_gate": "CONDITIONAL_SHORTFORM_SCOPE",
        "ymm4_routine_indexed": True,
        "routine_video_overhead_added": False,
    }


def main() -> int:
    print(json.dumps(validate(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
