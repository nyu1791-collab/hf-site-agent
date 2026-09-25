#!/usr/bin/env python3
"""Add a measured DeepSeek V4.1 paid specialist at the free-council exception boundary.

The normal path remains OpenRouter exact-free specialists plus free work
stealing. Only SCHEDULER_DAG and FAILURE_RETRY that remain unresolved after the
free path are offered to DeepSeek. DeepSeek returns an advisory candidate; it
does not mark the lane machine-validated and never auto-applies a patch.

Paid execution is intentionally lightweight rather than globally enabled. The
CLI requires an explicit marker in the worker-expansion trigger file, while the
pure runner receives the approval as a boolean. This lets the current trial use
DeepSeek heavily enough to measure it without turning every future worker run
into an automatic paid route.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import deepseek_specialist_trial as ds_base
from scripts import deepseek_specialist_trial_v2 as ds_v2
from scripts import failure_aware_specialist_retry as free_council


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROUTING = ROOT / "config" / "deepseek_specialist_routing.json"
DEFAULT_APPROVAL_FILE = ROOT / ".github" / "worker-expansion-trigger.txt"
APPROVAL_MARKER = "allow_deepseek_paid_trial=true"
MAX_CONTEXT_FILES = 3
MAX_CONTEXT_CHARS_PER_FILE = 3000


def load_routing(path: Path = DEFAULT_ROUTING) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "deepseek-specialist-routing-v1":
        raise ValueError("invalid DeepSeek specialist routing config")
    if value.get("generic_paid_fallback") is not False or value.get("production_enabled") is not False:
        raise ValueError("DeepSeek specialist routing must remain explicit staging-only")
    return value


def paid_trial_approved(path: Path = DEFAULT_APPROVAL_FILE) -> bool:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(line.strip() == APPROVAL_MARKER for line in lines)


def unresolved_critical_assignments(report: Mapping[str, Any], routing: Mapping[str, Any]) -> list[dict[str, Any]]:
    automatic = routing.get("automatic_escalation") if isinstance(routing.get("automatic_escalation"), Mapping) else {}
    lane_policy = automatic.get("lanes") if isinstance(automatic.get("lanes"), Mapping) else {}
    assignments = {
        str(row.get("specialist_lane") or ""): dict(row)
        for row in report.get("selected_models", [])
        if isinstance(row, Mapping) and row.get("specialist_lane")
    }
    final_results = {
        str(row.get("specialist_lane") or ""): row
        for row in report.get("results", [])
        if isinstance(row, Mapping) and row.get("specialist_lane")
    }
    output: list[dict[str, Any]] = []
    for lane in lane_policy:
        result = final_results.get(str(lane), {})
        if result.get("status") == "COUNCIL_OK":
            continue
        assignment = assignments.get(str(lane))
        if assignment:
            output.append(assignment)
    return output


def _compact_lane_context(assignment: Mapping[str, Any]) -> str:
    context = assignment.get("specialist_context") if isinstance(assignment.get("specialist_context"), Mapping) else {}
    chunks = []
    for path, text in list(context.items())[:MAX_CONTEXT_FILES]:
        chunks.append(f"### {str(path)[:220]}\n{str(text)[:MAX_CONTEXT_CHARS_PER_FILE]}")
    return "\n\n".join(chunks)


def _grounding(result: Mapping[str, Any]) -> dict[str, Any]:
    patches = result.get("patch_candidates") if isinstance(result.get("patch_candidates"), list) else []
    checked = 0
    grounded = 0
    for patch in patches[:3]:
        if not isinstance(patch, Mapping):
            continue
        checked += 1
        relative = str(patch.get("path") or "")
        symbol = str(patch.get("symbol") or "")
        candidate = ROOT / relative
        path_ok = candidate.is_file() and ROOT in candidate.resolve().parents
        symbol_ok = not symbol
        if path_ok and symbol:
            symbol_ok = symbol in candidate.read_text(encoding="utf-8", errors="replace")
        if path_ok and symbol_ok:
            grounded += 1
    return {
        "checked_patch_count": checked,
        "grounded_patch_count": grounded,
        "grounded_patch_ratio": round(grounded / checked, 4) if checked else 1.0,
    }


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


def _costs(trial_config: Mapping[str, Any], usage: Mapping[str, Any]) -> tuple[float, float]:
    now = datetime.now(timezone.utc)
    current = ds_base.estimate_cost_usd(usage, ds_base._rate_table(trial_config, conservative=False, now=now))
    conservative = ds_base.estimate_cost_usd(usage, ds_base._rate_table(trial_config, conservative=True, now=now))
    return round(current, 8), round(conservative, 8)


def _preflight_cost(trial_config: Mapping[str, Any], prompt_text: str, max_tokens: int) -> float:
    """Return a conservative upper-bound exposure for one request.

    UTF-8 byte count is intentionally used as a token upper-bound estimate for
    this compact code context. It is more conservative than the old 4-char/token
    heuristic and keeps unknown/failed requests budget-accountable.
    """
    rates = ds_base._rate_table(trial_config, conservative=True, now=datetime.now(timezone.utc))
    prompt_token_upper = max(1, len(str(prompt_text).encode("utf-8")))
    return (
        prompt_token_upper * rates["prompt_cache_miss"]
        + max(0, int(max_tokens)) * rates["output"]
    ) / 1_000_000


def _build_request(
    assignment: Mapping[str, Any],
    routing: Mapping[str, Any],
) -> tuple[dict[str, Any], str, str, int, bool]:
    lane = str(assignment.get("specialist_lane") or "")
    policies = routing["automatic_escalation"]["lanes"]
    policy = policies[lane]
    capability = str(policy.get("capability") or "CODING_DEEP")
    max_tokens = max(1, int(policy.get("max_tokens") or 4096))
    thinking = bool(policy.get("thinking") is True)
    context = _compact_lane_context(assignment)
    objective = str(assignment.get("specialist_objective") or "")[:1200]
    system = ds_base._system_prompt(context)
    user = (
        f"CAPABILITY={capability}\nLANE={lane}\nTASK={objective}\n"
        "The free specialist and free work-stealing path did not resolve this critical lane. "
        "Return one compact evidence-grounded JSON candidate now."
    )
    payload: dict[str, Any] = {
        "model": str(routing["model"]),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "enabled" if thinking else "disabled"},
    }
    if thinking and policy.get("reasoning_effort"):
        payload["reasoning_effort"] = str(policy["reasoning_effort"])
    return payload, system + "\n" + user, capability, max_tokens, thinking


def _call_deepseek(
    *,
    api_key: str,
    assignment: Mapping[str, Any],
    routing: Mapping[str, Any],
    trial_config: Mapping[str, Any],
    reserved_conservative_cost_usd: float,
) -> dict[str, Any]:
    lane = str(assignment.get("specialist_lane") or "")
    payload, _, capability, max_tokens, thinking = _build_request(assignment, routing)
    requested_model = str(routing["model"])
    started = time.monotonic()
    try:
        response, provider_latency = ds_base._request_json(
            "https://api.deepseek.com/chat/completions",
            api_key=api_key,
            method="POST",
            payload=payload,
            timeout=90.0,
        )
    except Exception as exc:
        error_class, http_status = ds_base._normalized_error(exc)
        return {
            "lane": lane,
            "capability": capability,
            "status": "PAID_SPECIALIST_FAILED",
            "error_class": error_class,
            "http_status": http_status,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "usage": {},
            "estimated_current_cost_usd": 0.0,
            "conservative_cost_usd": 0.0,
            "cost_exposure_usd": round(float(reserved_conservative_cost_usd), 8),
            "cost_accounting_status": "RESERVED_UNKNOWN_AFTER_DISPATCH",
            "thinking": thinking,
            "max_tokens": max_tokens,
            "advisory_only": True,
            "machine_validated": False,
            "requested_model": requested_model,
        }

    usage = _usage(response)
    current_cost, conservative_cost = _costs(trial_config, usage)
    shape = ds_v2._response_shape(response)
    response_model = str(response.get("model") or "")
    if response_model != requested_model:
        return {
            "lane": lane,
            "capability": capability,
            "status": "PAID_SPECIALIST_FAILED",
            "error_class": "RESPONSE_MODEL_MISMATCH",
            "latency_ms": provider_latency,
            **shape,
            "usage": usage,
            "estimated_current_cost_usd": current_cost,
            "conservative_cost_usd": conservative_cost,
            "cost_exposure_usd": max(conservative_cost, round(float(reserved_conservative_cost_usd), 8)),
            "cost_accounting_status": "KNOWN_USAGE_MODEL_MISMATCH",
            "thinking": thinking,
            "max_tokens": max_tokens,
            "advisory_only": True,
            "machine_validated": False,
            "requested_model": requested_model,
            "response_model": response_model,
        }

    try:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ds_v2.SpecialistOutputError("CHOICES_MISSING")
        message = choices[0].get("message") if isinstance(choices[0].get("message"), Mapping) else {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ds_v2.SpecialistOutputError("VISIBLE_CONTENT_MISSING")
        parsed = ds_v2.parse_json_object(content)
    except ds_v2.SpecialistOutputError as exc:
        return {
            "lane": lane,
            "capability": capability,
            "status": "PAID_SPECIALIST_FAILED",
            "error_class": exc.code,
            "latency_ms": provider_latency,
            **shape,
            "usage": usage,
            "estimated_current_cost_usd": current_cost,
            "conservative_cost_usd": conservative_cost,
            "cost_exposure_usd": conservative_cost,
            "cost_accounting_status": "KNOWN_USAGE_INVALID_OUTPUT",
            "thinking": thinking,
            "max_tokens": max_tokens,
            "advisory_only": True,
            "machine_validated": False,
            "requested_model": requested_model,
            "response_model": response_model,
        }

    grounding = _grounding(parsed)
    quality = ds_base._quality_score(parsed, capability)
    confidence = parsed.get("confidence")
    confidence_ok = (
        isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and 0.55 <= float(confidence) <= 1.0
    )
    candidate_ready = quality >= 0.8 and float(grounding["grounded_patch_ratio"]) >= 0.66 and confidence_ok
    return {
        "lane": lane,
        "capability": capability,
        "status": "PAID_SPECIALIST_CANDIDATE_READY" if candidate_ready else "PAID_SPECIALIST_CANDIDATE_WEAK",
        "latency_ms": provider_latency,
        **shape,
        "usage": usage,
        "estimated_current_cost_usd": current_cost,
        "conservative_cost_usd": conservative_cost,
        "cost_exposure_usd": conservative_cost,
        "cost_accounting_status": "KNOWN_USAGE",
        "quality_score": quality,
        "grounding": grounding,
        "thinking": thinking,
        "max_tokens": max_tokens,
        "advisory_only": True,
        "machine_validated": False,
        "requested_model": requested_model,
        "response_model": response_model,
        "result": parsed,
    }


def run_with_paid_escalation(
    *,
    openrouter_api_key: str,
    deepseek_api_key: str,
    probe: Mapping[str, Any],
    benchmark: Mapping[str, Any],
    routing: Mapping[str, Any] | None = None,
    trial_config: Mapping[str, Any] | None = None,
    paid_approved: bool = False,
) -> dict[str, Any]:
    routing = dict(routing or load_routing())
    trial_config = dict(trial_config or ds_base._load_json(ds_base.DEFAULT_CONFIG))
    report = dict(free_council.run_failure_aware_council(
        api_key=openrouter_api_key,
        probe=probe,
        benchmark=benchmark,
    ))
    unresolved = unresolved_critical_assignments(report, routing)
    budget = routing.get("normal_mission_budget") if isinstance(routing.get("normal_mission_budget"), Mapping) else {}
    max_calls = min(2, max(0, int(budget.get("max_paid_calls") or 0)))
    max_parallel = min(2, max(1, int(budget.get("max_parallel_calls") or 1)))
    max_cost = max(0.0, float(budget.get("max_estimated_cost_usd") or 0.05))
    report["deepseek_specialist_policy"] = {
        "model": routing.get("model"),
        "scope": routing.get("scope"),
        "free_workers_remain_primary": True,
        "generic_paid_fallback": False,
        "advisory_candidate_only": True,
        "explicit_trial_approval": bool(paid_approved),
        "max_paid_calls": max_calls,
        "max_parallel_calls": max_parallel,
        "max_estimated_cost_usd": max_cost,
    }
    report["deepseek_paid_calls"] = 0
    report["deepseek_estimated_current_cost_usd"] = 0.0
    report["deepseek_conservative_cost_usd"] = 0.0
    report["deepseek_cost_exposure_usd"] = 0.0
    report["paid_specialist_escalations"] = []
    report["total_ai_calls"] = int(report.get("model_calls", 0) or 0)

    if not unresolved:
        report["deepseek_escalation_status"] = "NOT_NEEDED_FREE_PATH_COMPLETE"
        return report
    if not paid_approved:
        report["deepseek_escalation_status"] = "PAID_APPROVAL_MISSING"
        return report
    if not deepseek_api_key or not routing.get("enabled"):
        report["deepseek_escalation_status"] = "AVAILABLE_BUT_NOT_EXECUTED"
        return report

    selected = unresolved[:max_calls]
    if not selected:
        report["deepseek_escalation_status"] = "PAID_CALL_BUDGET_ZERO"
        return report

    reservations: list[float] = []
    for assignment in selected:
        payload, prompt_text, _, max_tokens, _ = _build_request(assignment, routing)
        del payload
        reservations.append(_preflight_cost(trial_config, prompt_text, max_tokens))
    estimated = sum(reservations)
    report["deepseek_conservative_preflight_cost_usd"] = round(estimated, 8)
    if estimated > max_cost:
        report["deepseek_escalation_status"] = "BUDGET_BLOCKED"
        report["deepseek_cost_exposure_usd"] = round(estimated, 8)
        return report

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(max_parallel, len(selected)), thread_name_prefix="deepseek-critical") as executor:
        futures = [
            executor.submit(
                _call_deepseek,
                api_key=deepseek_api_key,
                assignment=assignment,
                routing=routing,
                trial_config=trial_config,
                reserved_conservative_cost_usd=reservation,
            )
            for assignment, reservation in zip(selected, reservations)
        ]
        for future in as_completed(futures):
            rows.append(future.result())

    priority = {"SCHEDULER_DAG": 0, "FAILURE_RETRY": 1}
    rows.sort(key=lambda row: priority.get(str(row.get("lane") or ""), 99))
    current_cost = sum(float(row.get("estimated_current_cost_usd") or 0.0) for row in rows)
    conservative_cost = sum(float(row.get("conservative_cost_usd") or 0.0) for row in rows)
    cost_exposure = sum(float(row.get("cost_exposure_usd") or 0.0) for row in rows)
    report["paid_specialist_escalations"] = rows
    report["deepseek_paid_calls"] = len(rows)
    report["deepseek_estimated_current_cost_usd"] = round(current_cost, 8)
    report["deepseek_conservative_cost_usd"] = round(conservative_cost, 8)
    report["deepseek_cost_exposure_usd"] = round(cost_exposure, 8)
    report["total_ai_calls"] = int(report.get("model_calls", 0) or 0) + len(rows)
    ready = sum(row.get("status") == "PAID_SPECIALIST_CANDIDATE_READY" for row in rows)
    report["deepseek_escalation_status"] = "CANDIDATES_READY" if ready == len(rows) else "CANDIDATES_PARTIAL"
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", default="artifacts/openrouter_expansion_probe.json")
    parser.add_argument("--benchmark", default="artifacts/openrouter_expansion_benchmark.json")
    parser.add_argument("--output", default="artifacts/worker_council.json")
    parser.add_argument("--approval-file", default=".github/worker-expansion-trigger.txt")
    args = parser.parse_args()
    paths = [Path(args.probe), Path(args.benchmark), Path(args.output), Path(args.approval_file)]
    if any(path.is_absolute() or ".." in path.parts for path in paths):
        raise SystemExit("paths must stay inside workspace")
    probe = json.loads(paths[0].read_text(encoding="utf-8"))
    benchmark = json.loads(paths[1].read_text(encoding="utf-8"))
    approved = paid_trial_approved(ROOT / paths[3])
    report = run_with_paid_escalation(
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY") or "",
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY") or "",
        probe=probe,
        benchmark=benchmark,
        paid_approved=approved,
    )
    paths[2].parent.mkdir(parents=True, exist_ok=True)
    paths[2].write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "free_successful_lane_count": report.get("successful_lane_count", 0),
        "deepseek_escalation_status": report.get("deepseek_escalation_status"),
        "deepseek_paid_calls": report.get("deepseek_paid_calls", 0),
        "deepseek_estimated_current_cost_usd": report.get("deepseek_estimated_current_cost_usd", 0),
        "deepseek_conservative_cost_usd": report.get("deepseek_conservative_cost_usd", 0),
        "deepseek_cost_exposure_usd": report.get("deepseek_cost_exposure_usd", 0),
        "total_ai_calls": report.get("total_ai_calls", 0),
        "explicit_paid_trial_approval": approved,
        "generic_paid_fallback": False,
        "production_routing_changed": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
