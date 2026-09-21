#!/usr/bin/env python3
"""Fail-closed continuity checks for the compact cross-tab commander handoff."""
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


def main() -> int:
    handoff = load_json("config/current_commander_handoff.json")
    manifest = load_json("config/permanent_standards_manifest.json")
    media_gate = load_json("config/media_command_read_gate.json")
    media_creative = load_json("config/media_audio_motion_retention_policy.json")
    longform = load_json("config/longform_video_reliability_policy.json")

    require(handoff.get("schema_version") == "top-commander-handoff-v12", "commander handoff schema is not current v12")
    continuity = handoff.get("continuity") or {}
    require(continuity.get("repository_is_source_of_truth") is True, "handoff lost repository source-of-truth rule")
    require(continuity.get("conversation_memory_is_not_source_of_truth") is True, "handoff made chat memory authoritative")
    require(continuity.get("read_order_is_bootstrap_not_full_standard_copy") is True, "handoff no longer treats startup list as compact bootstrap")

    read_order = list(continuity.get("on_new_session_required_read_order") or [])
    expected_prefix = [
        "README.md",
        "config/current_commander_handoff.json",
        "config/permanent_standards_manifest.json",
        "docs/AI_ARMY_MASTER_RULEBOOK.md",
    ]
    require(read_order[:4] == expected_prefix, "commander handoff startup order drifted")
    require(len(read_order) <= 6, "commander handoff duplicated too many task-specific standards")
    forbidden_bootstrap_duplicates = {
        "config/longform_video_objectives.json",
        "config/longform_video_reliability_policy.json",
        "config/media_audio_motion_retention_policy.json",
        "config/free_audio_source_registry.json",
        "config/dova_curated_bgm_catalog.json",
        "config/tiktok_shop_influence_policy.json",
        "config/authorized_clipping_monetization_policy.json",
    }
    require(not (forbidden_bootstrap_duplicates & set(read_order)), "task-specific standards leaked back into compact handoff bootstrap list")

    gates = continuity.get("task_specific_gate_resolution") or {}
    require(gates.get("media") == "config/media_command_read_gate.json", "handoff lost semantic media gate pointer")
    require(gates.get("monetization") == "config/monetization_command_read_gate.json", "handoff lost semantic monetization gate pointer")

    cross_tab = manifest.get("cross_tab_behavior") or {}
    require(cross_tab.get("priority_zero_manifest_is_expandable_startup_index") is True, "manifest startup index rule drifted")
    require(cross_tab.get("do_not_duplicate_full_required_standard_list_into_commander_handoff") is True, "manifest duplication guard drifted")
    require(cross_tab.get("media_command_read_gate_survives_tab_change") is True, "media gate no longer survives tab changes")

    active = handoff.get("active_standards") or {}
    for key, expected in {
        "permanent_manifest": "config/permanent_standards_manifest.json",
        "master_rulebook": "docs/AI_ARMY_MASTER_RULEBOOK.md",
        "media_command_gate": "config/media_command_read_gate.json",
        "media_audio_motion_retention": "config/media_audio_motion_retention_policy.json",
        "free_audio_source_registry": "config/free_audio_source_registry.json",
        "dova_curated_bgm_catalog": "config/dova_curated_bgm_catalog.json",
        "longform_objectives": "config/longform_video_objectives.json",
        "longform_policy": "config/longform_video_reliability_policy.json",
    }.items():
        require(active.get(key) == expected, f"handoff active-standard pointer drift: {key}")
        require((ROOT / expected).is_file(), f"handoff active-standard file missing: {expected}")

    handoff_longform = handoff.get("longform_fixed_rules") or {}
    require(handoff_longform.get("voice") == "VOICEVOX_ZUNDAMON_AND_SHIKOKU_METAN_LOCAL", "handoff regressed to a single VOICEVOX cast")
    require(handoff_longform.get("media_policy_source") == "config/media_command_read_gate.json", "handoff longform rules bypass media gate")
    require(handoff_longform.get("task_specific_media_execution_state_is_not_a_permanent_standard") is True, "handoff made task-specific media state permanent")
    require("video_creation_not_started_by_this_integration_mission" not in handoff_longform, "stale integration-mission media hold returned")

    serialized_handoff = json.dumps(handoff, ensure_ascii=False)
    require("VOICEVOX_ZUNDAMON_LOCAL" not in serialized_handoff, "stale Zundamon-only handoff token returned")
    require("media_production_hold" not in serialized_handoff, "media production hold was reintroduced into commander handoff")
    require("ACTIVE_UNTIL_EXPLICIT_USER_RELEASE" not in serialized_handoff, "legacy media production hold release marker was reintroduced")
    require("HOLD_MEDIA_PRODUCTION" not in serialized_handoff, "blanket media production hold marker was reintroduced")

    durable_cast = set(((media_creative.get("voice_prosody") or {}).get("durable_standard_cast") or []))
    longform_cast = set(((longform.get("voicevox_contract") or {}).get("standard_cast") or []))
    require(durable_cast == {"ずんだもん", "四国めたん"}, "creative policy durable cast drifted")
    require(longform_cast == durable_cast, "longform and creative VOICEVOX cast disagree")

    common_media = set(media_gate.get("common_media_read_set") or [])
    require("config/current_commander_handoff.json" in common_media, "media gate no longer rereads commander handoff")
    require("config/permanent_standards_manifest.json" in common_media, "media gate no longer rereads permanent manifest")

    overrides = handoff.get("temporary_user_overrides") or {}
    require("media_production_hold" not in overrides, "media production hold must stay absent from temporary overrides")

    gate_text = json.dumps(media_gate, ensure_ascii=False)
    require("ACTIVE_UNTIL_EXPLICIT_USER_RELEASE" not in gate_text, "legacy media production hold leaked into permanent media gate")
    require("HOLD_MEDIA_PRODUCTION" not in gate_text, "blanket media production hold leaked into permanent media gate")

    print(json.dumps({
        "status": "PASS",
        "handoff_schema": "v12",
        "compact_bootstrap": True,
        "semantic_task_gates": True,
        "voicevox_cast": ["ずんだもん", "四国めたん"],
        "media_production_hold_absent": True,
        "stale_single_voice_token_absent": True,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
