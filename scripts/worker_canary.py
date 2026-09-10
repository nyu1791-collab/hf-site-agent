#!/usr/bin/env python3
"""Run a tiny second-sample canary for ranked OpenRouter workers.

Canaries are a follow-up project, not another benchmark scorer. They verify that
a selected role/model still answers on a different task, resolves to the exact
model, stays on the no-fallback route, and preserves the prior FREE_ACTIVE
evidence. They never activate a worker or mutate the registry.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import json
import time
from typing import Any, Mapping
import urllib.error
import urllib.request

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_SECONDS = 25
MAX_OUTPUT_TOKENS = 128
MAX_CANARY_CALLS = 4

CANARY_TASKS: dict[str, tuple[str, str]] = {
    "GENERAL_WORKER": (
        'Return JSON only: {"ordered":[1,2,3],"sum":6}. No extra keys.',
        "general",
    ),
    "CODING_WORKER": (
        'Return JSON only. For Python `def twice(x): return x+x`, return {"complexity":"O(1)","result_for_4":8}.',
        "coding",
    ),
    "REVIEW_WORKER": (
        'Return JSON only. Review `if admin: allow(); else: allow()`. Return {"severity":"high","finding":"..."}.',
        "review",
    ),
    "FAST_WORKER": (
        'Return JSON only: {"answer":12} for 7+5.',
        "fast",
    ),
}


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _verified_models(probe_report: Mapping[str, Any]) -> set[str]:
    verified: set[str] = set()
    rows = probe_report.get("results")
    if not isinstance(rows, list):
        return verified
    for row in rows:
        if not isinstance(row, Mapping) or row.get("status") != "FREE_ACTIVE":
            continue
        requested = str(row.get("requested_model") or "").strip()
        resolved = str(row.get("response_model") or "").strip()
        if requested and requested == resolved and row.get("fallback_used") is not True:
            verified.add(requested)
    return verified


def _content(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        return ""
    value = message.get("content")
    return value.strip() if isinstance(value, str) else ""


def _passes(role: str, parsed: Mapping[str, Any]) -> bool:
    if role == "GENERAL_WORKER":
        return parsed.get("ordered") == [1, 2, 3] and parsed.get("sum") == 6
    if role == "CODING_WORKER":
        return str(parsed.get("complexity") or "").upper().replace(" ", "") == "O(1)" and parsed.get("result_for_4") == 8
    if role == "REVIEW_WORKER":
        finding = str(parsed.get("finding") or "").lower()
        return str(parsed.get("severity") or "").lower() == "high" and ("allow" in finding or "auth" in finding or "access" in finding)
    if role == "FAST_WORKER":
        return parsed.get("answer") == 12
    return False


def _request(model: str, api_key: str, prompt: str) -> tuple[int, dict[str, Any] | None, str, float]:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0,
        "stream": False,
        "response_format": {"type": "json_object"},
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
            "X-Title": "hf-site-agent-worker-canary",
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(512_000).decode("utf-8", errors="replace")
            elapsed = max(1.0, (time.perf_counter() - started) * 1000.0)
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = None
            return int(response.status), payload if isinstance(payload, dict) else None, "", elapsed
    except urllib.error.HTTPError as exc:
        return int(exc.code), None, "http_error", max(1.0, (time.perf_counter() - started) * 1000.0)
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, None, "network_error", max(1.0, (time.perf_counter() - started) * 1000.0)


def run_worker_canary(*, api_key: str, handoff: Mapping[str, Any], probe_report: Mapping[str, Any]) -> dict[str, Any]:
    selected = handoff.get("selected_workers")
    selected = selected if isinstance(selected, Mapping) else {}
    verified = _verified_models(probe_report)
    report: dict[str, Any] = {
        "schema_version": "worker-canary-v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "model_calls": 0,
        "max_calls": MAX_CANARY_CALLS,
        "max_output_tokens_per_call": MAX_OUTPUT_TOKENS,
        "provider_allow_fallbacks": False,
        "results": {},
        "automatic_activation": False,
        "paid_fallback": False,
        "production_active": False,
    }
    if not api_key:
        report.update(status="BLOCKED_MISSING_SECRET", reason="OpenRouter API secret is unavailable")
        return report
    if not selected:
        report.update(status="BLOCKED_NO_SELECTED_WORKERS", reason="No ranked worker handoff is available")
        return report

    for role, value in selected.items():
        if report["model_calls"] >= MAX_CANARY_CALLS:
            break
        if role not in CANARY_TASKS or not isinstance(value, Mapping):
            continue
        model = str(value.get("model") or "").strip()
        if not model or model not in verified:
            report["results"][role] = {
                "status": "CANARY_BLOCKED",
                "model": model,
                "reason": "MODEL_NOT_IN_CURRENT_EXACT_FREE_PROBE",
            }
            continue
        report["model_calls"] += 1
        status, payload, error, elapsed_ms = _request(model, api_key, CANARY_TASKS[role][0])
        result: dict[str, Any] = {
            "status": "CANARY_FAILED",
            "model": model,
            "http_status": status,
            "latency_ms": round(elapsed_ms, 3),
            "error": error or None,
            "exact_model": False,
            "structured_output": False,
            "quality_pass": False,
        }
        if status == 200 and isinstance(payload, Mapping):
            resolved = str(payload.get("model") or "").strip()
            usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
            cost = _decimal(usage.get("cost"))
            content = _content(payload)
            try:
                parsed_value = json.loads(content)
                parsed = parsed_value if isinstance(parsed_value, Mapping) else None
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = None
            exact = resolved == model
            structured = parsed is not None
            quality = bool(parsed is not None and _passes(role, parsed))
            cost_ok = cost in {None, Decimal("0")}
            result.update(
                exact_model=exact,
                structured_output=structured,
                quality_pass=quality,
                usage_cost=str(cost) if cost is not None else None,
                cost_evidence="THIS_RESPONSE_ZERO" if cost == Decimal("0") else "PRIOR_EXACT_FREE_PROBE",
            )
            if exact and structured and quality and cost_ok:
                result["status"] = "CANARY_OK"
                result["error"] = None
            elif not exact:
                result["error"] = "response_model_mismatch"
            elif not cost_ok:
                result["error"] = "nonzero_cost"
            elif not structured:
                result["error"] = "invalid_structured_output"
            else:
                result["error"] = "quality_assertion_failed"
        report["results"][role] = result

    required_roles = [role for role in selected if role in CANARY_TASKS]
    passed_roles = [role for role in required_roles if isinstance(report["results"].get(role), Mapping) and report["results"][role].get("status") == "CANARY_OK"]
    report["required_role_count"] = len(required_roles)
    report["passed_role_count"] = len(passed_roles)
    report["status"] = "CANARY_READY" if required_roles and len(passed_roles) == len(required_roles) else "COMPLETED_WITH_BLOCKS"
    return report


__all__ = ["MAX_CANARY_CALLS", "run_worker_canary"]
