#!/usr/bin/env python3
"""Run one bounded paid DeepSeek architecture audit of the current AI Army.

The audit is intentionally advisory. It reads a fixed, existing organization
surface, asks DeepSeek V4.1 Flash for a structured review, validates proposed
paths against that exact surface, records usage/cost evidence, and never writes
repository code, deploys, merges, publishes, mutates secrets, or enables generic
paid fallback.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import deepseek_specialist_trial as ds_base
from scripts import deepseek_specialist_trial_v2 as ds_v2


ROOT = Path(__file__).resolve().parents[1]
TRIAL_CONFIG = ROOT / "config" / "deepseek_specialist_trial.json"
ROUTING_CONFIG = ROOT / "config" / "deepseek_specialist_routing.json"
CONFIRMATION_TOKEN = "DEEPSEEK_ORGANIZATION_AUDIT"
MAX_OUTPUT_TOKENS = 4096
MAX_CONTEXT_CHARS = 32_000
MAX_FILE_CHARS = 3_600
MAX_CONSERVATIVE_COST_USD = 0.06

AUDIT_CONTEXT: Mapping[str, Sequence[str]] = {
    "config/replaceable_agent_organization.json": (
        '"adaptive_controls"', '"communication"', '"slots"',
    ),
    "scripts/low_latency_agent_fabric.py": (
        "class LowLatencyAgentFabric", "class FailureSignalRegistry", "def publish",
    ),
    "scripts/replaceable_agent_scheduler_v2.py": (
        "class LowLatencyReplaceableAgentScheduler", "def enqueue_ready", "def resource_available",
    ),
    "scripts/independent_agent_runtime.py": (
        "class AgentSession", "class IndependentAgentRegistry", "def execution_context",
    ),
    "scripts/independent_agent_scheduler.py": (
        "class IndependentAgentScheduler", "def _execute_with_handoff",
    ),
    "scripts/independent_agent_real_canary.py": (
        "ROLE_INSTRUCTIONS", "def validate_role_bindings", "def _mission_tasks",
    ),
    "scripts/global_agent_role_optimizer.py": (
        "def optimize_role_assignments", "BEAM_WIDTH",
    ),
    "scripts/reconcile_agent_organization.py": (
        "def reconcile", "def merge_candidates",
    ),
    "scripts/organization_feedback.py": (
        "def build_feedback", "def _recommended_parallel_limit", "def _bottlenecks",
    ),
    "scripts/run_nvidia_worker_expansion_self_heal.py": (
        "def main", "INVALID_PATHS", "scope",
    ),
    "tests/test_independent_agent_scheduler.py": (
        "IndependentAgentRuntimeTests", "IndependentAgentSchedulerProbeTests",
    ),
    "tests/test_replaceable_agent_scheduler_v2.py": (
        "LowLatencyReplaceableAgentSchedulerTests",
    ),
}

REQUIRED_AUDIT_KEYS = (
    "status",
    "executive_summary",
    "findings",
    "patch_candidates",
    "tests",
    "priority_plan",
    "confidence",
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def _window(text: str, marker: str, budget: int) -> str:
    index = text.find(marker)
    if index < 0:
        return ""
    before = max(180, budget // 3)
    start = max(0, index - before)
    end = min(len(text), start + budget)
    if end - start < budget and start > 0:
        start = max(0, end - budget)
    return text[start:end]


def build_repository_context() -> tuple[str, list[str]]:
    remaining = MAX_CONTEXT_CHARS
    sections: list[str] = []
    included: list[str] = []
    for relative, markers in AUDIT_CONTEXT.items():
        if remaining <= 0:
            break
        path = ROOT / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        file_budget = min(MAX_FILE_CHARS, remaining)
        present = [marker for marker in markers if marker in text]
        pieces: list[str] = []
        if present:
            per_marker = max(600, file_budget // max(1, len(present)))
            for marker in present:
                candidate = _window(text, marker, per_marker)
                if candidate and candidate not in pieces:
                    pieces.append(candidate)
        else:
            pieces.append(text[:file_budget])
        compact = "\n...<symbol-window>...\n".join(pieces)[:file_budget]
        if not compact:
            continue
        section = f"\n### {relative}\n{compact}"
        sections.append(section)
        included.append(relative)
        remaining -= len(section)
    return "".join(sections)[:MAX_CONTEXT_CHARS], included


def conservative_exposure_usd(config: Mapping[str, Any], prompt_text: str) -> float:
    rates = ds_base._rate_table(config, conservative=True, now=datetime.now(timezone.utc))
    prompt_tokens_upper = max(1, len(prompt_text.encode("utf-8")))
    return round(
        (
            prompt_tokens_upper * float(rates.get("prompt_cache_miss") or 0.0)
            + MAX_OUTPUT_TOKENS * float(rates.get("output") or 0.0)
        )
        / 1_000_000.0,
        8,
    )


def _usage(payload: Mapping[str, Any]) -> dict[str, int]:
    raw = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    details = raw.get("completion_tokens_details") if isinstance(raw.get("completion_tokens_details"), Mapping) else {}
    return {
        "prompt_tokens": int(raw.get("prompt_tokens") or 0),
        "prompt_cache_hit_tokens": int(raw.get("prompt_cache_hit_tokens") or 0),
        "prompt_cache_miss_tokens": int(raw.get("prompt_cache_miss_tokens") or 0),
        "completion_tokens": int(raw.get("completion_tokens") or 0),
        "reasoning_tokens": int(details.get("reasoning_tokens") or 0),
        "total_tokens": int(raw.get("total_tokens") or 0),
    }


def _exact_model_listed(catalog: Mapping[str, Any], model: str) -> bool:
    data = catalog.get("data") if isinstance(catalog.get("data"), list) else []
    return any(isinstance(row, Mapping) and str(row.get("id") or "") == model for row in data)


def validate_audit(audit: Mapping[str, Any], allowed_files: Sequence[str]) -> dict[str, Any]:
    missing = [key for key in REQUIRED_AUDIT_KEYS if audit.get(key) in (None, "", [], {})]
    allowed = set(allowed_files)
    invalid_paths: list[str] = []
    patches = audit.get("patch_candidates") if isinstance(audit.get("patch_candidates"), list) else []
    for row in patches[:6]:
        if not isinstance(row, Mapping):
            invalid_paths.append("<non-object>")
            continue
        path = str(row.get("path") or "")
        if path not in allowed:
            invalid_paths.append(path or "<missing>")
    confidence = audit.get("confidence")
    confidence_ok = isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and 0 <= float(confidence) <= 1
    return {
        "required_keys_complete": not missing,
        "missing_keys": missing,
        "path_validation": not invalid_paths,
        "invalid_paths": invalid_paths,
        "confidence_valid": confidence_ok,
        "valid": not missing and not invalid_paths and confidence_ok,
    }


def _system_prompt(context: str, allowed_files: Sequence[str]) -> str:
    return (
        "You are DeepSeek V4.1 Flash acting as a senior multi-agent systems architect and adversarial engineering reviewer. "
        "Review the current AI Army as an operating organization, not as a generic codebase. Find concrete correctness bugs, "
        "coordination delays, duplicate work, stale-context risks, routing mistakes, starvation/backpressure risks, failure-propagation gaps, "
        "and places where an agent is not truly autonomous. Prefer fewer high-impact changes over broad rewrites. "
        "Use ONLY the files in allowed_files and the supplied repository context. Never invent a file or symbol. "
        "Do not recommend weakening secret, payment, stale-result, external-repository-write, merge, deploy, publish, or generic paid-fallback boundaries. "
        "Ordinary low/medium-risk agent work should remain autonomous and lightweight. "
        "Return exactly one compact JSON object with keys: status, executive_summary, findings, patch_candidates, tests, priority_plan, confidence. "
        "findings must be an array of objects with severity, area, evidence, impact, recommendation. "
        "patch_candidates must be an array of at most 5 objects with path, symbol, change, rationale. "
        "tests must be an array. priority_plan must be an array ordered highest impact first. confidence must be 0..1. "
        f"allowed_files={json.dumps(list(allowed_files), ensure_ascii=False)}\n\nREPOSITORY_CONTEXT:\n{context}"
    )


def run_audit(*, network: bool, confirm: str, api_key: str) -> dict[str, Any]:
    trial = _load_json(TRIAL_CONFIG)
    routing = _load_json(ROUTING_CONFIG)
    model = str(routing.get("model") or trial.get("model") or "")
    context, allowed_files = build_repository_context()
    prompt = _system_prompt(context, allowed_files)
    exposure = conservative_exposure_usd(trial, prompt)
    base_report = {
        "schema_version": "deepseek-organization-audit-v1",
        "requested_model": model,
        "model_family": routing.get("model_family") or trial.get("model_family"),
        "context_files": allowed_files,
        "context_chars": len(context),
        "network_enabled": bool(network),
        "explicit_paid_route": True,
        "generic_paid_fallback": False,
        "auto_top_up": False,
        "production_routing_changed": False,
        "repository_write": False,
        "max_calls": 1,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "conservative_preflight_cost_usd": exposure,
        "max_conservative_cost_usd": MAX_CONSERVATIVE_COST_USD,
    }
    if not network:
        return {**base_report, "status": "AUDIT_DRY_RUN", "model_calls": 0, "audit": {}}
    if confirm != CONFIRMATION_TOKEN:
        return {**base_report, "status": "AUDIT_BLOCKED", "stop_reason": "EXPLICIT_CONFIRMATION_REQUIRED", "model_calls": 0, "audit": {}}
    if not api_key:
        return {**base_report, "status": "AUDIT_BLOCKED", "stop_reason": "DEEPSEEK_API_KEY_MISSING", "model_calls": 0, "audit": {}}
    if exposure > MAX_CONSERVATIVE_COST_USD:
        return {**base_report, "status": "AUDIT_BLOCKED", "stop_reason": "CONSERVATIVE_COST_LIMIT", "model_calls": 0, "audit": {}}

    base_url = str(trial.get("base_url") or "https://api.deepseek.com").rstrip("/")
    try:
        catalog, catalog_latency = ds_base._request_json(base_url + "/models", api_key=api_key, timeout=45.0)
    except Exception as exc:
        error_class, http_status = ds_base._normalized_error(exc)
        return {**base_report, "status": "AUDIT_FAILED", "stop_reason": error_class, "http_status": http_status, "catalog_latency_ms": None, "model_calls": 0, "audit": {}}
    if not _exact_model_listed(catalog, model):
        return {**base_report, "status": "AUDIT_BLOCKED", "stop_reason": "EXACT_MODEL_NOT_LISTED", "catalog_latency_ms": catalog_latency, "model_calls": 0, "audit": {}}

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Audit the organization now. Return the final JSON only."},
        ],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "stream": False,
        "response_format": {"type": "json_object"},
        "reasoning_effort": "high",
        "thinking": {"type": "enabled"},
    }
    try:
        response, latency_ms = ds_base._request_json(
            base_url + "/chat/completions",
            api_key=api_key,
            method="POST",
            payload=payload,
            timeout=120.0,
        )
    except Exception as exc:
        error_class, http_status = ds_base._normalized_error(exc)
        return {**base_report, "status": "AUDIT_FAILED", "stop_reason": error_class, "http_status": http_status, "catalog_latency_ms": catalog_latency, "model_calls": 1, "audit": {}}

    response_model = str(response.get("model") or "")
    usage = _usage(response)
    current_cost = ds_base.estimate_cost_usd(usage, ds_base._rate_table(trial, conservative=False, now=datetime.now(timezone.utc)))
    conservative_cost = ds_base.estimate_cost_usd(usage, ds_base._rate_table(trial, conservative=True, now=datetime.now(timezone.utc)))
    choices = response.get("choices") if isinstance(response.get("choices"), list) else []
    first = choices[0] if choices and isinstance(choices[0], Mapping) else {}
    message = first.get("message") if isinstance(first.get("message"), Mapping) else {}
    content = message.get("content") if isinstance(message.get("content"), str) else ""
    if response_model != model:
        return {
            **base_report,
            "status": "AUDIT_FAILED",
            "stop_reason": "RESPONSE_MODEL_MISMATCH",
            "catalog_latency_ms": catalog_latency,
            "latency_ms": latency_ms,
            "model_calls": 1,
            "response_model": response_model,
            "usage": usage,
            "estimated_current_cost_usd": round(current_cost, 8),
            "conservative_cost_usd": round(conservative_cost, 8),
            "audit": {},
        }
    try:
        audit = ds_v2.parse_json_object(content)
    except ds_v2.SpecialistOutputError as exc:
        return {
            **base_report,
            "status": "AUDIT_FAILED",
            "stop_reason": exc.code,
            "catalog_latency_ms": catalog_latency,
            "latency_ms": latency_ms,
            "model_calls": 1,
            "response_model": response_model,
            "finish_reason": first.get("finish_reason"),
            "usage": usage,
            "estimated_current_cost_usd": round(current_cost, 8),
            "conservative_cost_usd": round(conservative_cost, 8),
            "audit": {},
        }
    validation = validate_audit(audit, allowed_files)
    status = "AUDIT_READY" if validation["valid"] else "AUDIT_PARTIAL"
    return {
        **base_report,
        "status": status,
        "catalog_latency_ms": catalog_latency,
        "latency_ms": latency_ms,
        "model_calls": 1,
        "response_model": response_model,
        "finish_reason": first.get("finish_reason"),
        "usage": usage,
        "estimated_current_cost_usd": round(current_cost, 8),
        "conservative_cost_usd": round(conservative_cost, 8),
        "validation": validation,
        "audit": audit,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--output", default="artifacts/deepseek_organization_audit.json")
    args = parser.parse_args()
    output = Path(args.output)
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output must stay inside workspace")
    config = _load_json(TRIAL_CONFIG)
    api_key = os.environ.get(str(config.get("api_key_env") or "DEEPSEEK_API_KEY"), "")
    report = run_audit(network=args.network, confirm=args.confirm, api_key=api_key)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "requested_model": report.get("requested_model"),
        "response_model": report.get("response_model"),
        "model_calls": report.get("model_calls", 0),
        "context_files": len(report.get("context_files", [])),
        "validation": report.get("validation", {}),
        "estimated_current_cost_usd": report.get("estimated_current_cost_usd", 0),
        "conservative_cost_usd": report.get("conservative_cost_usd", 0),
        "generic_paid_fallback": False,
        "production_routing_changed": False,
    }, sort_keys=True))
    return 0 if report.get("status") in {"AUDIT_DRY_RUN", "AUDIT_READY", "AUDIT_PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
