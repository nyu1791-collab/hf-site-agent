#!/usr/bin/env python3
"""Validate draft-agent and media-plan contracts without third-party packages.

This is deliberately a small standard-library gate. It does not execute any
agent output; it checks that packets remain bounded, typed, and approval-gated.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

HEX64 = re.compile(r"^[0-9a-f]{64}$")
SECRET = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._~+/=-]{12,}|"
    r"(?:sk|gsk|hf|sk-or-v1)-[A-Za-z0-9_-]{12,})"
)
SECRET_KEY = re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|password|secret)")
ROLES = {"research", "product", "content", "video", "code", "qa", "metrics", "specialist"}


class ContractError(ValueError):
    pass


def fail(message: str) -> None:
    raise ContractError(message)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"{path}: invalid JSON ({exc})")
    if not isinstance(value, dict):
        fail(f"{path}: top-level value must be an object")
    return value


def walk_for_secrets(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if SECRET_KEY.search(key_text) and key_text not in {
                "required_confirmation",
                "subagents_cannot",
                "prohibited",
                "safety_flags",
            }:
                fail(f"{path}: secret-like field name is not allowed: {key_text}")
            walk_for_secrets(item, f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            walk_for_secrets(item, f"{path}[{index}]")
    elif isinstance(value, str) and SECRET.search(value):
        fail(f"{path}: secret-shaped value is not allowed")


def require(value: dict[str, Any], *keys: str) -> None:
    missing = [key for key in keys if key not in value]
    if missing:
        fail("missing fields: " + ", ".join(missing))


def check_bool(value: Any, expected: bool, label: str) -> None:
    if value is not expected:
        fail(f"{label} must be {expected}")


def check_work_order(item: Any, label: str, max_instruction: int = 1200) -> None:
    if not isinstance(item, dict):
        fail(f"{label} must be an object")
    require(item, "id", "role", "instruction", "execution_mode",
            "requires_commander_approval", "execution_allowed")
    if not isinstance(item["id"], str) or not item["id"] or len(item["id"]) > 100:
        fail(f"{label}.id is invalid")
    if not isinstance(item["role"], str) or not item["role"]:
        fail(f"{label}.role is invalid")
    if not isinstance(item["instruction"], str) or not item["instruction"] or len(item["instruction"]) > max_instruction:
        fail(f"{label}.instruction is invalid")
    if item["execution_mode"] != "read_only_draft":
        fail(f"{label}.execution_mode must remain read_only_draft")
    check_bool(item["requires_commander_approval"], True, f"{label}.requires_commander_approval")
    check_bool(item["execution_allowed"], False, f"{label}.execution_allowed")


def validate_commander(value: dict[str, Any], label: str) -> None:
    require(value, "ok", "status", "mode", "authority", "delegated_instructions",
            "artifact", "handoff", "budget", "packet_sha256")
    check_bool(value["ok"], True, f"{label}.ok")
    if value["status"] != "awaiting_commander_approval":
        fail(f"{label}.status must await commander approval")
    if value["mode"] not in {
        "planner_critic_with_downstream_handoff",
        "commander_approved_specialist_draft",
    }:
        fail(f"{label}.mode is unknown")
    authority = value["authority"]
    if not isinstance(authority, dict):
        fail(f"{label}.authority must be an object")
    require(authority, "execution_allowed", "required_confirmation")
    check_bool(authority["execution_allowed"], False, f"{label}.authority.execution_allowed")
    orders = value["delegated_instructions"]
    if not isinstance(orders, list) or len(orders) > 6:
        fail(f"{label}.delegated_instructions is not bounded")
    for index, order in enumerate(orders):
        check_work_order(order, f"{label}.delegated_instructions[{index}]")
    artifact = value["artifact"]
    if not isinstance(artifact, dict):
        fail(f"{label}.artifact must be an object")
    require(artifact, "type", "title", "content", "status")
    if artifact["status"] != "draft_awaiting_commander_review":
        fail(f"{label}.artifact is not draft-only")
    handoff = value["handoff"]
    if not isinstance(handoff, dict):
        fail(f"{label}.handoff must be an object")
    check_bool(handoff.get("execution_allowed"), False, f"{label}.handoff.execution_allowed")
    packet_hash = value["packet_sha256"]
    if not isinstance(packet_hash, str) or not HEX64.fullmatch(packet_hash):
        fail(f"{label}.packet_sha256 must be a 64-character lowercase hex string")
    budget = value["budget"]
    if not isinstance(budget, dict):
        fail(f"{label}.budget must be an object")
    require(budget, "provider", "calls", "max_tokens_per_call", "timeout_seconds", "automatic_fallback")
    if budget["provider"] != "openrouter" or budget["calls"] not in (1, 2):
        fail(f"{label}.budget provider/calls are outside the contract")
    check_bool(budget["automatic_fallback"], False, f"{label}.budget.automatic_fallback")


def validate_agent(value: dict[str, Any], label: str) -> None:
    require(value, "ok", "status", "mode", "task", "artifact", "next_tasks",
            "authority", "budget", "packet_sha256")
    check_bool(value["ok"], True, f"{label}.ok")
    if value["status"] != "awaiting_commander_approval" or value["mode"] != "commander_approved_specialist_draft":
        fail(f"{label}: specialist packet is not approval-gated")
    task = value["task"]
    if not isinstance(task, dict):
        fail(f"{label}.task must be an object")
    require(task, "id", "role", "instruction")
    if task["role"] not in ROLES:
        fail(f"{label}.task.role is invalid")
    if not isinstance(task["instruction"], str) or not task["instruction"] or len(task["instruction"]) > 5000:
        fail(f"{label}.task.instruction is invalid")
    artifact = value["artifact"]
    if not isinstance(artifact, dict):
        fail(f"{label}.artifact must be an object")
    require(artifact, "type", "title", "content", "status")
    if artifact["status"] != "draft_awaiting_commander_review":
        fail(f"{label}.artifact is not draft-only")
    tasks = value["next_tasks"]
    if not isinstance(tasks, list) or len(tasks) > 4:
        fail(f"{label}.next_tasks is not bounded")
    for index, item in enumerate(tasks):
        check_work_order(item, f"{label}.next_tasks[{index}]", max_instruction=900)
    authority = value["authority"]
    if not isinstance(authority, dict):
        fail(f"{label}.authority must be an object")
    require(authority, "execution_allowed", "required_confirmation")
    check_bool(authority["execution_allowed"], False, f"{label}.authority.execution_allowed")
    if authority["required_confirmation"] != "COMMANDER_APPROVE":
        fail(f"{label}.authority.required_confirmation is invalid")
    packet_hash = value["packet_sha256"]
    if not isinstance(packet_hash, str) or not HEX64.fullmatch(packet_hash):
        fail(f"{label}.packet_sha256 is invalid")
    budget = value["budget"]
    if not isinstance(budget, dict):
        fail(f"{label}.budget must be an object")
    require(budget, "provider", "calls", "max_tokens", "timeout_seconds", "automatic_fallback")
    if budget["provider"] != "openrouter" or budget["calls"] != 1:
        fail(f"{label}.budget provider/calls are outside the contract")
    check_bool(budget["automatic_fallback"], False, f"{label}.budget.automatic_fallback")


def validate_media(value: dict[str, Any], label: str) -> None:
    require(value, "schema_version", "project_id", "rights", "format", "segments",
            "captions", "originality", "cost", "approval", "provenance")
    if value["schema_version"] != "1.0":
        fail(f"{label}.schema_version is unsupported")
    if not isinstance(value["project_id"], str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", value["project_id"]):
        fail(f"{label}.project_id is invalid")
    rights = value["rights"]
    if not isinstance(rights, dict):
        fail(f"{label}.rights must be an object")
    require(rights, "source_type", "permission_status", "attribution")
    if rights["permission_status"] not in {"verified", "pending", "blocked"}:
        fail(f"{label}.rights.permission_status is invalid")
    if rights["permission_status"] != "verified":
        fail(f"{label}: only verified rights may reach the render contract")
    fmt = value["format"]
    if not isinstance(fmt, dict):
        fail(f"{label}.format must be an object")
    require(fmt, "orientation", "width", "height", "fps", "target_duration_seconds")
    if (fmt["orientation"], fmt["width"], fmt["height"]) != ("9:16", 1080, 1920):
        fail(f"{label}.format must be the initial 9:16 profile")
    segments = value["segments"]
    if not isinstance(segments, list) or not 1 <= len(segments) <= 12:
        fail(f"{label}.segments is outside bounds")
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            fail(f"{label}.segments[{index}] must be an object")
        require(segment, "start_seconds", "end_seconds", "role", "text")
        if segment["end_seconds"] <= segment["start_seconds"]:
            fail(f"{label}.segments[{index}] has invalid timing")
    captions = value["captions"]
    if not isinstance(captions, dict):
        fail(f"{label}.captions must be an object")
    require(captions, "language", "max_lines", "max_chars_per_line", "safe_area_percent")
    if captions["language"] != "ja" or captions["max_lines"] != 2:
        fail(f"{label}.captions must use the Japanese two-line profile")
    originality = value["originality"]
    if not isinstance(originality, dict):
        fail(f"{label}.originality must be an object")
    require(originality, "source_hashes", "transformations", "similarity_check")
    if not originality["transformations"]:
        fail(f"{label}.originality.transformations cannot be empty")
    cost = value["cost"]
    if not isinstance(cost, dict):
        fail(f"{label}.cost must be an object")
    require(cost, "provider_tier", "estimated_cost_jpy", "max_budget_jpy",
            "paid_approved", "stop_if_estimate_exceeds")
    check_bool(cost["stop_if_estimate_exceeds"], True, f"{label}.cost.stop_if_estimate_exceeds")
    approval = value["approval"]
    if not isinstance(approval, dict):
        fail(f"{label}.approval must be an object")
    require(approval, "required", "approved", "render_allowed")
    check_bool(approval["required"], True, f"{label}.approval.required")
    if not approval["approved"] or not approval["render_allowed"]:
        fail(f"{label}: example must be an explicitly approved render plan")
    provenance = value["provenance"]
    if not isinstance(provenance, dict):
        fail(f"{label}.provenance must be an object")
    require(provenance, "created_by", "created_at", "toolchain")


def validate_schema(path: Path) -> None:
    value = load(path)
    require(value, "$schema", "title", "type", "properties")
    if value["type"] != "object":
        fail(f"{path}: root type must be object")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commander", action="append", default=[])
    parser.add_argument("--agent", action="append", default=[])
    parser.add_argument("--media", action="append", default=[])
    parser.add_argument("--schema", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        for path in args.schema:
            validate_schema(Path(path))
        for path in args.commander:
            value = load(Path(path))
            walk_for_secrets(value)
            validate_commander(value, path)
        for path in args.agent:
            value = load(Path(path))
            walk_for_secrets(value)
            validate_agent(value, path)
        for path in args.media:
            value = load(Path(path))
            walk_for_secrets(value)
            validate_media(value, path)
    except ContractError as exc:
        print(f"contract validation failed: {exc}", file=sys.stderr)
        return 1
    print("Agent and media packet contracts passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
