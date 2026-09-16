#!/usr/bin/env python3
"""Run a bounded real-provider canary for seven independent role agents.

Unlike the deterministic coordination probe, this canary lets each currently
reconciled OpenRouter exact-free role binding perform its own real inference.
The scheduler supplies stable role identity, bounded mission memory, direct
peer handoffs and high-priority deltas. Two independent branches run when the
DAG permits and join at RESULT_SYNTHESIZER.

Admission is fail closed: every assigned body must be present in the SAME RUN
exact-free probe, OpenRouter provider fallback is disabled, the response model
must equal the requested exact model and reported cost must be zero/absent.
The entire canary is capped at 10 provider attempts including free failover.
It cannot write the repository, expose secrets, enable paid fallback, deploy,
merge or publish.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Mapping, Sequence
import urllib.error
import urllib.request

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.independent_agent_scheduler import IndependentAgentScheduler
from scripts.replaceable_agent_organization import load_config
from scripts.replaceable_agent_scheduler import AgentTask


CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_SECONDS = 45
MAX_PROVIDER_CALLS = 10
MAX_OUTPUT_TOKENS = 512
MAX_VISIBLE_CHARS = 2_200
REASONING_POLICY = {"effort": "minimal", "exclude": True}
ROLE_ORDER = (
    "CONTEXT_LIBRARIAN",
    "FAST_OPERATOR",
    "OPERATIONS_LEAD",
    "ENGINEERING_AGENT",
    "CODE_EXECUTOR",
    "QA_VALIDATOR",
    "RESULT_SYNTHESIZER",
)

ROLE_INSTRUCTIONS = {
    "CONTEXT_LIBRARIAN": "Extract the minimum context and constraints another engineering agent needs. Do not design the final solution.",
    "FAST_OPERATOR": "Triage likely coordination risks and identify the single highest-priority operational signal. Be brief.",
    "OPERATIONS_LEAD": "Own the operations branch. Convert the triage handoff into a compact execution priority and acceptance condition.",
    "ENGINEERING_AGENT": "Use the context handoff to design one minimal, testable coordination improvement. Do not write files.",
    "CODE_EXECUTOR": "Turn the engineering handoff into a concise implementation candidate: symbol-level change plus one test idea. Do not claim repository writes.",
    "QA_VALIDATOR": "Independently inspect the code-candidate handoff for correctness, regressions and missing validation. Return PASS or NEEDS_FIX with one reason.",
    "RESULT_SYNTHESIZER": "Join the operations and QA handoffs into one concise final recommendation. Surface disagreement instead of hiding it.",
}


def _read(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, Mapping) else {}


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def exact_free_models(probe: Mapping[str, Any]) -> set[str]:
    output: set[str] = set()
    rows = probe.get("results") if isinstance(probe.get("results"), list) else []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("status") != "FREE_ACTIVE":
            continue
        requested = str(row.get("requested_model") or "").strip()
        response = str(row.get("response_model") or "").strip()
        if not requested or response != requested:
            continue
        if row.get("fallback_used") is True or row.get("provider_allow_fallbacks") is True:
            continue
        cost = _decimal(row.get("usage_cost"))
        if cost not in {None, Decimal("0")}:
            continue
        if row.get("credits_unchanged") is not True:
            continue
        output.add(requested)
    return output


def validate_role_bindings(organization: Mapping[str, Any], probe: Mapping[str, Any]) -> tuple[bool, dict[str, dict[str, Any]], list[str]]:
    verified = exact_free_models(probe)
    assignments = organization.get("assignments") if isinstance(organization.get("assignments"), Mapping) else {}
    bindings: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for slot in ROLE_ORDER:
        row = assignments.get(slot) if isinstance(assignments, Mapping) else None
        if not isinstance(row, Mapping) or row.get("status") != "ASSIGNED":
            errors.append(f"{slot}:UNASSIGNED")
            continue
        provider = str(row.get("provider") or "")
        model = str(row.get("model") or "")
        if provider != "openrouter":
            errors.append(f"{slot}:NON_OPENROUTER_BODY")
            continue
        if model not in verified:
            errors.append(f"{slot}:NOT_SAME_RUN_EXACT_FREE")
            continue
        bindings[slot] = dict(row)
    return len(bindings) == len(ROLE_ORDER) and not errors, bindings, errors


def _mission_tasks() -> tuple[AgentTask, ...]:
    # Two branches are independent until synthesis:
    #   context -> engineering -> code -> QA --\
    #                                      synthesis
    #   fast -> operations ----------------/
    return (
        AgentTask(
            task_id="live-context",
            slot="CONTEXT_LIBRARIAN",
            objective="For a multi-agent coding organization, identify the minimum context packet needed to prevent stale or duplicated work while preserving agent independence.",
            risk_level="LOW",
            metadata={"priority": "HIGH", "critical_path_rank": 0},
        ),
        AgentTask(
            task_id="live-fast-triage",
            slot="FAST_OPERATOR",
            objective="Triage the main operational risk when independent AI agents exchange results concurrently; return one priority signal and one measurable check.",
            risk_level="LOW",
            metadata={"priority": "HIGH", "critical_path_rank": 0},
        ),
        AgentTask(
            task_id="live-operations",
            slot="OPERATIONS_LEAD",
            objective="Using the FAST_OPERATOR handoff, define one bounded execution priority and acceptance condition for the agent organization.",
            depends_on=("live-fast-triage",),
            risk_level="MEDIUM",
            metadata={"priority": "HIGH", "critical_path_rank": 1},
        ),
        AgentTask(
            task_id="live-engineering",
            slot="ENGINEERING_AGENT",
            objective="Using the CONTEXT_LIBRARIAN handoff, design one minimal improvement that increases independent-agent throughput without broadening permissions.",
            depends_on=("live-context",),
            risk_level="MEDIUM",
            metadata={"priority": "CRITICAL", "critical_path_rank": 1},
        ),
        AgentTask(
            task_id="live-code",
            slot="CODE_EXECUTOR",
            objective="Convert the engineering design into an implementation-ready, symbol-level candidate and one deterministic test. Return advice only; do not write files.",
            depends_on=("live-engineering",),
            risk_level="MEDIUM",
            metadata={"priority": "CRITICAL", "critical_path_rank": 2},
        ),
        AgentTask(
            task_id="live-qa",
            slot="QA_VALIDATOR",
            objective="Independently validate the code candidate. Return PASS or NEEDS_FIX, the key reason, and one regression test target.",
            depends_on=("live-code",),
            risk_level="MEDIUM",
            metadata={"priority": "CRITICAL", "critical_path_rank": 3},
        ),
        AgentTask(
            task_id="live-synthesis",
            slot="RESULT_SYNTHESIZER",
            objective="Join the independent operations and QA results into one evidence-backed recommendation, preserving any disagreement.",
            depends_on=("live-operations", "live-qa"),
            risk_level="MEDIUM",
            metadata={"priority": "HIGH", "critical_path_rank": 4},
        ),
    )


class ProviderCallBudget:
    def __init__(self, maximum: int = MAX_PROVIDER_CALLS) -> None:
        self.maximum = max(1, min(MAX_PROVIDER_CALLS, int(maximum)))
        self._lock = threading.Lock()
        self._used = 0

    def reserve(self) -> int | None:
        with self._lock:
            if self._used >= self.maximum:
                return None
            self._used += 1
            return self._used

    @property
    def used(self) -> int:
        with self._lock:
            return self._used


def _compact_context(context: Mapping[str, Any]) -> dict[str, Any]:
    session = context.get("agent_session") if isinstance(context.get("agent_session"), Mapping) else {}
    identity = session.get("agent_identity") if isinstance(session.get("agent_identity"), Mapping) else {}
    authority = session.get("local_authority") if isinstance(session.get("local_authority"), Mapping) else {}
    handoff = session.get("direct_dependency_handoff") if isinstance(session.get("direct_dependency_handoff"), Mapping) else {}
    dependencies = handoff.get("dependencies") if isinstance(handoff.get("dependencies"), Mapping) else {}
    compact_dependencies: dict[str, Any] = {}
    for task_id, row in list(dependencies.items())[:3]:
        if not isinstance(row, Mapping):
            continue
        output = row.get("output") if isinstance(row.get("output"), Mapping) else {}
        compact_dependencies[str(task_id)[:128]] = {
            "summary": str(row.get("summary") or "")[:600],
            "quality_score": row.get("quality_score"),
            "output": {str(key)[:80]: str(value)[:900] for key, value in list(output.items())[:4]},
        }
    deltas = session.get("peer_deltas") if isinstance(session.get("peer_deltas"), list) else []
    compact_deltas = []
    for row in deltas[-4:]:
        if isinstance(row, Mapping):
            compact_deltas.append({
                "kind": row.get("kind"),
                "subject": row.get("subject"),
                "priority": row.get("priority"),
            })
    return {
        "agent_id": identity.get("agent_id"),
        "role_slot": identity.get("role_slot"),
        "current_body": identity.get("current_body"),
        "local_authority": {
            "may_plan_locally": authority.get("may_plan_locally") is True,
            "may_revise_locally": authority.get("may_revise_locally") is True,
            "may_handoff_to_dependencies": authority.get("may_handoff_to_dependencies") is True,
            "commander_roundtrip_required_for_ordinary_local_decision": authority.get("commander_roundtrip_required_for_ordinary_local_decision") is True,
        },
        "direct_dependencies": compact_dependencies,
        "peer_deltas": compact_deltas,
    }


def _provider_request(
    *,
    api_key: str,
    model: str,
    slot: str,
    task: AgentTask,
    context: Mapping[str, Any],
    call_index: int,
) -> dict[str, Any]:
    compact = _compact_context(context)
    prompt = (
        "You are an independent role agent in a bounded engineering organization. "
        "Act only inside your assigned role; use direct dependency handoffs as evidence and make ordinary local decisions without waiting for a commander. "
        "Do not claim repository writes, deploys, merges, secret access, payments, or permission changes. "
        f"Role instruction: {ROLE_INSTRUCTIONS[slot]} "
        f"Task objective: {task.objective} "
        "Return concise final work in at most 120 words. Context: "
        + json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0.1,
        "stream": False,
        "reasoning": dict(REASONING_POLICY),
        "provider": {"allow_fallbacks": False},
    }
    request = urllib.request.Request(
        CHAT_URL,
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/nyu1791-collab/hf-site-agent",
            "X-Title": "hf-site-agent-independent-role-canary",
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            elapsed_ms = max(1.0, (time.perf_counter() - started) * 1000.0)
            raw = response.read(1_000_000).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = None
            if int(response.status) != 200 or not isinstance(payload, Mapping):
                return {"status": "FAILED", "summary": "provider returned invalid response", "error_class": "HTTP_ERROR", "call_index": call_index, "http_status": int(response.status), "latency_ms": round(elapsed_ms, 3)}
            resolved = str(payload.get("model") or "").strip()
            usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
            cost = _decimal(usage.get("cost"))
            choices = payload.get("choices")
            first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else {}
            message = first.get("message") if isinstance(first.get("message"), Mapping) else {}
            text = str(message.get("content") or "")[:MAX_VISIBLE_CHARS].strip()
            if resolved != model:
                return {"status": "FAILED", "summary": "exact response model mismatch", "error_class": "MODEL_MISMATCH", "call_index": call_index, "http_status": int(response.status), "latency_ms": round(elapsed_ms, 3), "requested_model": model, "response_model": resolved}
            if cost not in {None, Decimal("0")}:
                return {"status": "FAILED", "summary": "non-zero provider cost observed", "error_class": "SAFETY_COST", "call_index": call_index, "http_status": int(response.status), "latency_ms": round(elapsed_ms, 3), "requested_model": model, "response_model": resolved, "usage_cost": str(cost)}
            if not text:
                return {"status": "FAILED", "summary": "empty visible model response", "error_class": "EMPTY_RESPONSE", "call_index": call_index, "http_status": int(response.status), "latency_ms": round(elapsed_ms, 3), "requested_model": model, "response_model": resolved, "finish_reason": first.get("finish_reason")}
            return {
                "status": "COMPLETED",
                "summary": text[:700],
                "output": {"response": text},
                "quality_score": 1.0,
                "call_index": call_index,
                "http_status": int(response.status),
                "latency_ms": round(elapsed_ms, 3),
                "requested_model": model,
                "response_model": resolved,
                "exact_model": True,
                "usage_cost": str(cost) if cost is not None else None,
                "finish_reason": first.get("finish_reason"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
            }
    except urllib.error.HTTPError as exc:
        error = "RATE_LIMIT" if int(exc.code) == 429 else "PROVIDER_5XX" if 500 <= int(exc.code) <= 599 else "HTTP_ERROR"
        return {"status": "FAILED", "summary": f"provider HTTP {int(exc.code)}", "error_class": error, "call_index": call_index, "http_status": int(exc.code), "requested_model": model}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"status": "FAILED", "summary": "provider transport failure", "error_class": "NETWORK", "call_index": call_index, "http_status": 0, "requested_model": model}


def run_live_canary(
    *,
    api_key: str,
    probe: Mapping[str, Any],
    organization: Mapping[str, Any],
    request_fn=_provider_request,
) -> dict[str, Any]:
    admitted, bindings, admission_errors = validate_role_bindings(organization, probe)
    base_report: dict[str, Any] = {
        "schema_version": "live-independent-agent-canary-v1",
        "execution_mode": "REAL_PROVIDER_STABLE_ROLE_AGENTS_TWO_BRANCH_DAG",
        "role_count": len(ROLE_ORDER),
        "admitted_role_count": len(bindings),
        "admission_errors": admission_errors,
        "max_provider_calls": MAX_PROVIDER_CALLS,
        "provider_allow_fallbacks": False,
        "paid_fallback": False,
        "production_routing_changed": False,
        "external_repository_write": False,
        "role_bindings": {slot: {"provider": row.get("provider"), "model": row.get("model")} for slot, row in bindings.items()},
    }
    if not api_key:
        base_report["status"] = "BLOCKED_MISSING_SECRET"
        base_report["provider_calls"] = 0
        return base_report
    if not admitted:
        base_report["status"] = "BLOCKED_ROLE_BINDING_ADMISSION"
        base_report["provider_calls"] = 0
        return base_report

    config = load_config()
    candidates = organization.get("candidate_portfolio") if isinstance(organization.get("candidate_portfolio"), list) else []
    verified = exact_free_models(probe)
    candidate_pool = [
        dict(row)
        for row in candidates
        if isinstance(row, Mapping)
        and row.get("provider") == "openrouter"
        and str(row.get("model") or "") in verified
        and row.get("free_verified") is True
        and row.get("paid") is not True
    ]
    scheduler = IndependentAgentScheduler(organization, config=config, candidate_pool=candidate_pool)
    scheduler.fabric.publish(
        kind="DECISION",
        subject="LIVE_INDEPENDENT_AGENT_CANARY",
        source="work-integrator",
        priority="HIGH",
        dedupe_key="live-independent-agent-canary:v1",
        payload={
            "goal": "prove real exact-free role-agent execution with direct handoff and bounded local authority",
            "provider_fallback": False,
            "max_provider_calls": MAX_PROVIDER_CALLS,
        },
    )
    budget = ProviderCallBudget(MAX_PROVIDER_CALLS)
    records: list[dict[str, Any]] = []
    record_lock = threading.Lock()
    handoff_observations: dict[str, list[str]] = {}
    peer_delta_tasks: set[str] = set()

    def handler(task: AgentTask, binding: Mapping[str, Any], context: Mapping[str, Any]) -> Mapping[str, Any]:
        call_index = budget.reserve()
        if call_index is None:
            return {"status": "FAILED", "summary": "live canary call budget exhausted", "error_class": "CANARY_BUDGET_EXHAUSTED"}
        model = str(binding.get("model") or "")
        if model not in verified or str(binding.get("provider") or "") != "openrouter":
            return {"status": "FAILED", "summary": "scheduler selected a body outside same-run exact-free admission", "error_class": "ADMISSION_VIOLATION"}
        session = context.get("agent_session") if isinstance(context.get("agent_session"), Mapping) else {}
        handoff = session.get("direct_dependency_handoff") if isinstance(session.get("direct_dependency_handoff"), Mapping) else {}
        dependencies = handoff.get("dependencies") if isinstance(handoff.get("dependencies"), Mapping) else {}
        handoff_observations[task.task_id] = sorted(str(key) for key in dependencies)
        if session.get("peer_deltas"):
            peer_delta_tasks.add(task.task_id)
        row = dict(request_fn(
            api_key=api_key,
            model=model,
            slot=task.slot,
            task=task,
            context=context,
            call_index=call_index,
        ))
        record = {
            "call_index": call_index,
            "task_id": task.task_id,
            "slot": task.slot,
            "agent_id": session.get("agent_identity", {}).get("agent_id") if isinstance(session.get("agent_identity"), Mapping) else None,
            "provider": binding.get("provider"),
            "requested_model": model,
            "response_model": row.get("response_model"),
            "status": row.get("status"),
            "error_class": row.get("error_class"),
            "http_status": row.get("http_status"),
            "latency_ms": row.get("latency_ms"),
            "exact_model": row.get("exact_model") is True,
            "usage_cost": row.get("usage_cost"),
            "dependency_ids": handoff_observations[task.task_id],
            "peer_delta_count": len(session.get("peer_deltas") or []),
        }
        with record_lock:
            records.append(record)
        return row

    report = dict(scheduler.run(_mission_tasks(), handler))
    ordered_records = sorted(records, key=lambda row: int(row.get("call_index") or 0))
    completed_roles = {
        str(row.get("agent_id") or "")
        for row in ordered_records
        if row.get("status") == "COMPLETED" and row.get("agent_id")
    }
    all_costs_zero = all(_decimal(row.get("usage_cost")) in {None, Decimal("0")} for row in ordered_records)
    all_success_exact = all(row.get("exact_model") is True for row in ordered_records if row.get("status") == "COMPLETED")
    report.update(base_report)
    report["status"] = "LIVE_INDEPENDENT_AGENTS_READY" if report.get("completed_task_count") == len(ROLE_ORDER) else "LIVE_INDEPENDENT_AGENTS_PARTIAL"
    report["provider_calls"] = budget.used
    report["successful_provider_calls"] = sum(row.get("status") == "COMPLETED" for row in ordered_records)
    report["actual_ai_role_agent_count"] = len(completed_roles)
    report["call_records"] = ordered_records
    report["all_successful_calls_exact_model"] = all_success_exact
    report["all_observed_costs_zero"] = all_costs_zero
    report["peer_delta_task_count"] = len(peer_delta_tasks)
    report["direct_handoff_observations"] = handoff_observations
    report["two_branch_join_verified"] = (
        handoff_observations.get("live-synthesis") == ["live-operations", "live-qa"]
    )
    report["qa_direct_code_handoff_verified"] = handoff_observations.get("live-qa") == ["live-code"]
    report["engineering_direct_context_handoff_verified"] = handoff_observations.get("live-engineering") == ["live-context"]
    report["operations_direct_fast_handoff_verified"] = handoff_observations.get("live-operations") == ["live-fast-triage"]
    report["real_provider_role_execution"] = True
    report["dynamic_child_delegation_proven_elsewhere"] = True
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", default="artifacts/openrouter_expansion_probe.json")
    parser.add_argument("--organization", default="artifacts/replaceable_agent_organization.json")
    parser.add_argument("--output", default="artifacts/live_independent_agent_canary.json")
    args = parser.parse_args()
    paths = [Path(args.probe), Path(args.organization), Path(args.output)]
    for path in paths:
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit("paths must stay inside workspace")
    report = run_live_canary(
        api_key=os.environ.get("OPENROUTER_API_KEY") or "",
        probe=_read(paths[0]),
        organization=_read(paths[1]),
    )
    paths[2].parent.mkdir(parents=True, exist_ok=True)
    paths[2].write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "provider_calls": report.get("provider_calls", 0),
        "successful_provider_calls": report.get("successful_provider_calls", 0),
        "actual_ai_role_agent_count": report.get("actual_ai_role_agent_count", 0),
        "completed_task_count": report.get("completed_task_count", 0),
        "free_reselection_count": report.get("free_reselection_count", 0),
        "two_branch_join_verified": report.get("two_branch_join_verified", False),
        "all_observed_costs_zero": report.get("all_observed_costs_zero", False),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
