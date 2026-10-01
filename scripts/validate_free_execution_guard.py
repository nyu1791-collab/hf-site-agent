#!/usr/bin/env python3
"""Fail closed if the permanent free-execution boundary drifts.

The current media stack has a deliberately lean routine read-set. This validator
checks current machine authority and executable paths instead of requiring old
handoff prose to contain specific literal phrases.
"""
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
    video_policy = load("config/video_creation_admission_policy.json")
    paid_route = load("config/paid_agent_route_eligibility_policy.json")

    require(guard.get("schema_version") == "free-execution-guard-v1", "free execution guard schema drift")
    require(guard.get("status") == "ENFORCED_PERMANENT_STANDARD", "free execution guard is not enforced")
    default = guard.get("default_runtime") or {}
    require(default.get("free_only_mode") is False, "obsolete global free-only mode is active")
    require(default.get("allow_paid_model") is True, "paid API routes remain globally disabled")
    require(default.get("paid_route_eligibility_policy") == "config/paid_agent_route_eligibility_policy.json", "paid route evidence gate pointer drift")
    require(default.get("paid_route_requires_verified_lower_total_cost_and_materially_better_task_performance") is True, "paid route evidence gate disabled")
    require(paid_route.get("schema_version") == "paid-agent-route-eligibility-v1", "paid route policy schema drift")
    require(paid_route.get("status") == "AUTHORIZED_ONLY_THROUGH_EVIDENCE_GATE", "paid route policy is not evidence-gated")
    routing = paid_route.get("routing") or {}
    require(routing.get("paid_candidate_may_be_selected_as_primary_after_gate") is True, "eligible paid route cannot be selected as primary")
    precedence = load("config/project_rule_precedence_policy.json")
    require(precedence.get("schema_version") == "project-rule-precedence-v1", "rule precedence policy schema drift")
    require(precedence.get("status") == "CANONICAL", "rule precedence policy is not canonical")
    require(routing.get("automatic_paid_fallback") is False, "paid fallback enabled")
    require(routing.get("automatic_paid_sibling_substitution") is False, "paid sibling substitution enabled")
    require(default.get("allow_paid_fallback") is False, "paid fallback enabled")
    require(default.get("auto_top_up") is False, "auto top-up enabled")
    require(default.get("unknown_cost_route") == "BLOCK", "unknown cost must block")
    require(default.get("trial_credit_or_freemium_route_counts_as_free") is False, "trial credits became free")
    require(default.get("paid_route_must_not_be_probed_to_discover_availability") is True, "paid route probing is enabled")

    media = guard.get("media_boundary") or {}
    for key in (
        "paid_or_freemium_video_generation",
        "paid_or_freemium_video_editing",
        "paid_caption_or_tts_service",
        "paid_media_tool_discovery",
    ):
        require(media.get(key) is False, f"paid media boundary drift: {key}")
    require(media.get("free_route_unavailable_action") == "BLOCK_AND_REPORT_NO_PAID_SUBSTITUTION", "free media failure may fall back to paid")
    blocked = {str(x).lower() for x in (media.get("blocked_tools") or [])}
    require({"runway", "fal", "fal.ai", "descript", "veed", "heygen", "higgsfield"}.issubset(blocked), "blocked media tool list is incomplete")

    video = guard.get("video_creation_admission") or {}
    bootstrap = (video_policy.get("voice_contract") or {}).get("runtime_bootstrap") or {}
    require(video.get("policy") == "config/video_creation_admission_policy.json", "video admission policy pointer drift")
    require(video.get("runtime") == "scripts/video_creation_admission.py", "video admission runtime pointer drift")
    require(video.get("must_restore_before_every_video_request") is True, "video admission must restore on every request")
    require(video.get("required_local_engine") == "VOICEVOX_LOCAL", "video admission engine drift")
    require(video.get("required_primary_voice") == "ずんだもん", "video admission primary voice drift")
    require(video.get("voicevox_unavailable_action") == "BLOCK_BEFORE_RENDER", "missing VOICEVOX may render")
    require(video.get("silent_video_fallback") is False, "silent video fallback enabled")
    require(video.get("start_local_engine_before_blocking") is True, "VOICEVOX startup-before-block drift")
    require(bootstrap.get("document") == "docs/VOICEVOX_RUNTIME.md", "VOICEVOX runtime document missing")
    require(bootstrap.get("launcher") == "scripts/with_local_voicevox.sh", "VOICEVOX launcher missing")
    require(bootstrap.get("start_local_engine_before_declaring_unavailable") is True, "VOICEVOX may block before startup attempt")

    exceptions = guard.get("narrow_preauthorized_exceptions") or {}
    require((exceptions.get("jev") or {}).get("media_generation") is False, "Jev exception expanded to media generation")
    require((exceptions.get("deepseek") or {}).get("media_generation") is False, "DeepSeek exception expanded to media generation")

    continuity = guard.get("cross_tab_continuity") or {}
    for key in (
        "must_be_read_from_repository_on_new_tab",
        "must_be_re_read_when_media_intent_is_detected",
        "chat_memory_cannot_override_this_guard",
        "paid_api_route_eligibility_survives_tab_change",
        "paid_media_block_survives_tab_change",
    ):
        require(continuity.get(key) is True, f"free guard continuity drift: {key}")

    standards = manifest.get("required_standards") or []
    by_id = {str(item.get("id")): item for item in standards if isinstance(item, dict)}
    standard = by_id.get("free-execution-guard") or {}
    require(standard.get("machine_policy") == "config/free_execution_guard.json", "manifest lost free execution guard")
    require(standard.get("priority") == 0, "free execution guard must remain priority 0")
    video_standard = by_id.get("video-creation-admission") or {}
    require(video_standard.get("machine_policy") == "config/video_creation_admission_policy.json", "manifest lost video admission policy")
    require(video_standard.get("runtime") == "scripts/video_creation_admission.py", "manifest lost video admission runtime")
    require(video_standard.get("priority") == 0, "video admission must remain priority 0")
    require(video_standard.get("runtime_bootstrap") == "docs/VOICEVOX_RUNTIME.md", "manifest lost VOICEVOX runtime document")
    require(video_standard.get("launcher") == "scripts/with_local_voicevox.sh", "manifest lost VOICEVOX startup launcher")

    active = handoff.get("active_standards") or {}
    require(active.get("free_execution_guard") == "config/free_execution_guard.json", "handoff lost free execution guard pointer")
    # Video admission continuity is machine-checked through the priority-0
    # manifest entry plus the current media gate; literal handoff prose is not authority.

    common = set(media_gate.get("common_media_read_set") or [])
    speed_override = media_gate.get("speed_first_delivery_override") or {}
    speed_reads = set(speed_override.get("read_set") or [])
    require("config/free_execution_guard.json" in common, "common media path lost free execution guard")
    require("config/free_execution_guard.json" in speed_reads, "routine speed-first path lost free execution guard")
    require("config/video_creation_admission_policy.json" in common or "config/video_creation_admission_policy.json" in speed_reads, "media path lost video admission policy")

    video_trigger = ((media_gate.get("trigger_sets") or {}).get("VIDEO_CREATION") or {})
    video_required = set(video_trigger.get("required") or [])
    require("config/video_creation_admission_policy.json" in video_required, "VIDEO_CREATION does not require video admission policy")
    require("scripts/video_creation_admission.py" in video_required, "VIDEO_CREATION does not require video admission runtime")
    conditional = video_trigger.get("conditional") or {}
    conditional_paths = {
        str(path)
        for paths in conditional.values()
        if isinstance(paths, list)
        for path in paths
    }
    startup_paths = video_required | conditional_paths | speed_reads
    require("docs/VOICEVOX_RUNTIME.md" in startup_paths, "video creation cannot restore VOICEVOX startup instructions")
    require("scripts/with_local_voicevox.sh" in startup_paths, "video creation cannot restore VOICEVOX startup launcher")

    cross_tab = manifest.get("cross_tab_behavior") or {}
    require(cross_tab.get("free_execution_guard_survives_tab_change") is True, "manifest free guard continuity missing")
    require(cross_tab.get("paid_api_route_eligibility_survives_tab_change") is True, "manifest paid route continuity missing")
    require(cross_tab.get("video_creation_admission_survives_tab_change") is True, "manifest video admission continuity missing")
    require(cross_tab.get("video_requests_require_voicevox_zundamon_preflight") is True, "manifest VOICEVOX preflight continuity missing")

    print(json.dumps({
        "status": "PASS",
        "free_only": False,
        "paid_api_route_evidence_gate": True,
        "paid_fallback": False,
        "auto_top_up": False,
        "speed_path_guarded": True,
        "handoff_literal_dependency": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
