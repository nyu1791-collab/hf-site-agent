#!/usr/bin/env python3
"""Fail closed if the permanent free-execution boundary drifts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load(path: str) -> dict[str, Any]:
    value = json.loads((ROOT / path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path}: object required")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    guard = load("config/free_execution_guard.json")
    handoff = load("config/current_commander_handoff.json")
    manifest = load("config/permanent_standards_manifest.json")
    media_gate = load("config/media_command_read_gate.json")

    require(guard.get("schema_version") == "free-execution-guard-v1", "free execution guard schema drift")
    require(guard.get("status") == "ENFORCED_PERMANENT_STANDARD", "free execution guard is not enforced")
    default = guard.get("default_runtime") or {}
    for key in ("free_only_mode", "allow_paid_model", "allow_paid_fallback", "auto_top_up"):
        expected = key == "free_only_mode"
        require(default.get(key) is expected, f"free execution default drift: {key}")
    require(default.get("unknown_cost_route") == "BLOCK", "unknown cost must block")
    require(default.get("trial_credit_or_freemium_route_counts_as_free") is False, "trial credits became free")
    require(default.get("paid_route_must_not_be_probed_to_discover_availability") is True, "paid route probing is enabled")

    media = guard.get("media_boundary") or {}
    for key in ("paid_or_freemium_video_generation", "paid_or_freemium_video_editing", "paid_caption_or_tts_service", "paid_media_tool_discovery"):
        require(media.get(key) is False, f"paid media boundary drift: {key}")
    require(media.get("free_route_unavailable_action") == "BLOCK_AND_REPORT_NO_PAID_SUBSTITUTION", "free media failure may fall back to paid")
    blocked = {str(x).lower() for x in (media.get("blocked_tools") or [])}
    require({"runway", "fal", "fal.ai", "descript", "veed", "heygen", "higgsfield"}.issubset(blocked), "blocked media tool list is incomplete")

    video = guard.get("video_creation_admission") or {}
    require(video.get("policy") == "config/video_creation_admission_policy.json", "video admission policy pointer drift")
    require(video.get("runtime") == "scripts/video_creation_admission.py", "video admission runtime pointer drift")
    require(video.get("must_restore_before_every_video_request") is True, "video admission must restore on every request")
    require(video.get("required_local_engine") == "VOICEVOX_LOCAL", "video admission engine drift")
    require(video.get("required_primary_voice") == "ずんだもん", "video admission primary voice drift")
    require(video.get("voicevox_unavailable_action") == "BLOCK_BEFORE_RENDER", "missing VOICEVOX may not render")
    require(video.get("silent_video_fallback") is False, "silent video fallback was enabled")

    exceptions = guard.get("narrow_preauthorized_exceptions") or {}
    require((exceptions.get("jev") or {}).get("media_generation") is False, "Jev exception expanded to media generation")
    require((exceptions.get("deepseek") or {}).get("media_generation") is False, "DeepSeek exception expanded to media generation")

    cross_tab = guard.get("cross_tab_continuity") or {}
    for key in ("must_be_read_from_repository_on_new_tab", "must_be_re_read_when_media_intent_is_detected", "chat_memory_cannot_override_this_guard", "free_only_rule_survives_tab_change", "paid_media_block_survives_tab_change"):
        require(cross_tab.get(key) is True, f"free guard cross-tab continuity drift: {key}")

    required = manifest.get("required_standards") or []
    by_id = {str(item.get("id")): item for item in required if isinstance(item, dict)}
    standard = by_id.get("free-execution-guard") or {}
    require(standard.get("machine_policy") == "config/free_execution_guard.json", "manifest lost free execution guard")
    require(standard.get("priority") == 0, "free execution guard must be priority 0")
    video_standard = by_id.get("video-creation-admission") or {}
    require(video_standard.get("machine_policy") == "config/video_creation_admission_policy.json", "manifest lost video admission policy")
    require(video_standard.get("runtime") == "scripts/video_creation_admission.py", "manifest lost video admission runtime")
    require(video_standard.get("priority") == 0, "video admission must be priority 0")

    paths = ((handoff.get("active_standards") or {}).get("free_execution_guard"))
    require(paths == "config/free_execution_guard.json", "handoff lost free execution guard")
    common = set(media_gate.get("common_media_read_set") or [])
    require("config/free_execution_guard.json" in common, "media read gate does not restore free execution guard")
    require("config/video_creation_admission_policy.json" in common, "media read gate does not restore video admission policy")
    video_trigger = ((media_gate.get("trigger_sets") or {}).get("VIDEO_CREATION") or {})
    video_required = set(video_trigger.get("required") or [])
    require("config/video_creation_admission_policy.json" in video_required, "VIDEO_CREATION does not require video admission policy")
    require("scripts/video_creation_admission.py" in video_required, "VIDEO_CREATION does not require video admission runtime")
    manifest_cross_tab = manifest.get("cross_tab_behavior") or {}
    require(manifest_cross_tab.get("free_execution_guard_survives_tab_change") is True, "manifest free guard cross-tab continuity missing")
    require(manifest_cross_tab.get("video_creation_admission_survives_tab_change") is True, "manifest video admission cross-tab continuity missing")
    require(manifest_cross_tab.get("video_requests_require_voicevox_zundamon_preflight") is True, "manifest VOICEVOX preflight continuity missing")
    checks = set(handoff.get("specific_checks") or [])
    require("free execution guard is restored from the repository on every new tab before media or external provider work" in checks, "handoff free guard restore check missing")
    require("every video request restores config/video_creation_admission_policy.json and runs scripts/video_creation_admission.py before rendering" in checks, "handoff video admission check missing")
    require("video rendering is blocked unless local VOICEVOX and the ずんだもん standard cast are available; silent fallback is forbidden" in checks, "handoff VOICEVOX block check missing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
