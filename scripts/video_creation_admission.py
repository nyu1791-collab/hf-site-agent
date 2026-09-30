#!/usr/bin/env python3
"""Fail-closed admission for every repository VIDEO_CREATION route.

The command restores the current media policies from the repository and,
when requested, probes only the local VOICEVOX engine.  It never contacts a
paid/freemium provider and never treats a missing voice engine as permission
to render a silent video.
"""
from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "video_creation_admission_policy.json"
READ_GATE_PATH = ROOT / "config" / "media_command_read_gate.json"
SPEED_POLICY_PATH = ROOT / "config" / "media_speed_quality_policy.json"
ROUTINE_READ_SET_REF = "config/media_command_read_gate.json#/speed_first_delivery_override/read_set"
ROUTINE_OUTPUT_CHECKS_REF = "config/media_speed_quality_policy.json#/speed_first_delivery/automatic_completion_check"
DEFAULT_VOICEVOX_URL = "http://127.0.0.1:50021"
VOICEVOX_TIMEOUT_SECONDS = 10


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _local_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("VOICEVOX admission accepts localhost only")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("VOICEVOX URL must not contain credentials, query, or fragment")
    return value.rstrip("/")


def http_json(url: str, *, method: str = "GET", payload: Mapping[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "User-Agent": "hf-site-agent-video-admission/1.0"},
    )
    with urllib.request.urlopen(request, timeout=VOICEVOX_TIMEOUT_SECONDS) as response:  # nosec B310 -- URL is localhost-validated
        raw = response.read(10_000_000)
    return json.loads(raw.decode("utf-8"))


def _select_style(speakers: Any, name: str) -> dict[str, Any] | None:
    if not isinstance(speakers, list):
        return None
    for speaker in speakers:
        if not isinstance(speaker, Mapping) or speaker.get("name") != name:
            continue
        styles = speaker.get("styles") if isinstance(speaker.get("styles"), list) else []
        selected = next((x for x in styles if isinstance(x, Mapping) and x.get("name") == "ノーマル"), None)
        selected = selected or next((x for x in styles if isinstance(x, Mapping)), None)
        if not isinstance(selected, Mapping) or not isinstance(selected.get("id"), int):
            return None
        return {
            "speaker_uuid": speaker.get("speaker_uuid"),
            "style_name": selected.get("name"),
            "style_id": selected["id"],
        }
    return None


def static_admission() -> dict[str, Any]:
    policy = load_json(POLICY_PATH)
    failures: list[str] = []
    if policy.get("status") != "ENFORCED_PERMANENT_STANDARD":
        failures.append("video creation admission policy is not enforced")
    if policy.get("required_read_set_ref") != ROUTINE_READ_SET_REF:
        failures.append("routine media read set must reference the canonical speed-first gate")
    try:
        gate = load_json(READ_GATE_PATH)
        raw_required = gate.get("speed_first_delivery_override", {}).get("read_set")
        required = [str(x) for x in raw_required] if isinstance(raw_required, list) else []
        if not required:
            failures.append("canonical speed-first read set is missing or invalid")
    except Exception as exc:
        required = []
        failures.append(f"canonical media read gate unavailable: {type(exc).__name__}: {exc}")
    missing = [path for path in required if not (ROOT / path).exists()]
    if missing:
        failures.append(f"required media policy files missing: {missing}")
    voice = policy.get("voice_contract") if isinstance(policy.get("voice_contract"), Mapping) else {}
    if voice.get("engine") != "VOICEVOX_LOCAL":
        failures.append("VOICEVOX_LOCAL is not the required engine")
    if voice.get("primary_voice") != "ずんだもん":
        failures.append("ずんだもん is not the required primary voice")
    if voice.get("silent_video_fallback") is not False:
        failures.append("silent video fallback must remain disabled")
    free = policy.get("free_execution_contract") if isinstance(policy.get("free_execution_contract"), Mapping) else {}
    if free.get("paid_or_freemium_tts") is not False or free.get("paid_media_substitution") is not False:
        failures.append("paid/freemium voice substitution is enabled")
    captions = policy.get("caption_contract") if isinstance(policy.get("caption_contract"), Mapping) else {}
    if captions.get("full_spoken_text_required") is not True:
        failures.append("full spoken captions are not required")
    if captions.get("summary_caption_may_not_replace_narration") is not True:
        failures.append("summary captions may replace narration")
    if captions.get("caption_contract_name") != "FULL_SPOKEN_TEXT":
        failures.append("FULL_SPOKEN_TEXT caption contract is missing")
    renderers = captions.get("renderers") if isinstance(captions.get("renderers"), Mapping) else {}
    for profile in ("shortform", "longform"):
        renderer = renderers.get(profile)
        if not isinstance(renderer, str) or not renderer.startswith("scripts/") or not renderer.endswith(".py"):
            failures.append(f"caption renderer missing for {profile} profile")
        elif not (ROOT / renderer).is_file():
            failures.append(f"caption renderer file missing for {profile} profile: {renderer}")
    try:
        speed_policy = load_json(SPEED_POLICY_PATH)
        routine_checks = speed_policy.get("speed_first_delivery", {}).get("automatic_completion_check")
        artifact_contract = policy.get("artifact_contract") if isinstance(policy.get("artifact_contract"), Mapping) else {}
        if artifact_contract.get("routine_output_checks_ref") != ROUTINE_OUTPUT_CHECKS_REF:
            failures.append("routine output checks must reference the speed-first policy")
        if not isinstance(routine_checks, list) or not routine_checks:
            failures.append("speed-first routine output checks are missing or invalid")
        clarity = speed_policy.get("script_clarity_contract") if isinstance(speed_policy.get("script_clarity_contract"), Mapping) else {}
        headings = clarity.get("topic_headings") if isinstance(clarity.get("topic_headings"), Mapping) else {}
        narration = clarity.get("narration") if isinstance(clarity.get("narration"), Mapping) else {}
        for key in ("use_clear_heading_for_each_main_topic", "heading_names_the_topic_in_plain_words"):
            if headings.get(key) is not True:
                failures.append(f"script clarity rule missing: topic_headings.{key}")
        for key in ("state_main_point_before_details", "prefer_common_words", "keep_only_details_that_change_understanding_or_action", "avoid_repeating_caveats_or_availability_notes"):
            if narration.get(key) is not True:
                failures.append(f"script clarity rule missing: narration.{key}")
    except Exception as exc:
        routine_checks = []
        failures.append(f"speed-first output policy unavailable: {type(exc).__name__}: {exc}")
    visuals = policy.get("visual_asset_contract") if isinstance(policy.get("visual_asset_contract"), Mapping) else {}
    for key in ("claim_bearing_news_requires_related_visual_plan", "source_page_required", "asset_locator_required", "license_or_public_domain_state_required", "scene_or_claim_mapping_required", "semantic_match_required"):
        if visuals.get(key) is not True:
            failures.append(f"related visual provenance rule missing: {key}")
    if visuals.get("provenance_scope") != "EXTERNAL_OR_REUSED_VISUAL_ASSETS_ONLY":
        failures.append("visual provenance scope must distinguish external assets from original simple visuals")
    if visuals.get("original_simple_visuals_may_use_creator_provenance") is not True:
        failures.append("original simple visuals must remain available on the speed-first route")
    if visuals.get("unknown_rights_action") != "BLOCK_BEFORE_RENDER":
        failures.append("unknown visual rights must remain blocked")
    character = policy.get("character_output_contract") if isinstance(policy.get("character_output_contract"), Mapping) else {}
    if character.get("applies_only_when_character_led_profile_is_selected_or_user_requests_it") is not True:
        failures.append("character motion requirements must be scoped to a selected/requested character-led profile")
    return {
        "schema_version": policy.get("schema_version"),
        "status": "PASS" if not failures else "BLOCKED",
        "required_read_set": required,
        "missing_read_set": missing,
        "failures": failures,
        "primary_voice": voice.get("primary_voice"),
        "standard_cast": voice.get("standard_cast"),
        "voicevox_unavailable_action": voice.get("voicevox_unavailable_action"),
        "paid_or_freemium_tts": free.get("paid_or_freemium_tts"),
        "caption_contract": captions.get("caption_contract_name"),
        "caption_renderers": dict(renderers),
        "routine_output_checks": routine_checks,
        "related_visual_plan_required": visuals.get("claim_bearing_news_requires_related_visual_plan"),
    }


def runtime_admission(base_url: str = DEFAULT_VOICEVOX_URL) -> dict[str, Any]:
    report = static_admission()
    failures = list(report["failures"])
    cast: dict[str, Any] = {}
    if not failures:
        try:
            base = _local_url(base_url)
            version = http_json(f"{base}/version")
            speakers = http_json(f"{base}/speakers")
            for name in ("ずんだもん", "四国めたん"):
                cast[name] = _select_style(speakers, name)
                if cast[name] is None:
                    failures.append(f"VOICEVOX standard cast unavailable: {name}")
            report["voicevox"] = {"url": base, "version": version, "cast": cast}
        except Exception as exc:
            failures.append(f"VOICEVOX local preflight failed: {type(exc).__name__}: {exc}")
    report["failures"] = failures
    report["status"] = "PASS" if not failures else "BLOCKED"
    return report


def require_runtime_admission(base_url: str = DEFAULT_VOICEVOX_URL) -> dict[str, Any]:
    report = runtime_admission(base_url)
    if report["status"] != "PASS":
        raise RuntimeError("VIDEO_CREATION_BLOCKED: " + " | ".join(report["failures"]))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", action="store_true", help="also probe local VOICEVOX and its standard cast")
    parser.add_argument("--voicevox-url", default=DEFAULT_VOICEVOX_URL)
    args = parser.parse_args()
    report = runtime_admission(args.voicevox_url) if args.runtime else static_admission()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
