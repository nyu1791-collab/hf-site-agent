#!/usr/bin/env python3
"""One-shot, exact-ID OpenRouter free endpoint probe.

The probe intentionally does not use the public model catalog as a substitute
for an endpoint call.  It sends exactly one request per requested :free ID,
with no models array, no online search, no paid fallback, and no retry.
Secrets and account balances are never written to the report.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

CATALOG_URL = "https://openrouter.ai/api/v1/models"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
CREDITS_URL = "https://openrouter.ai/api/v1/credits"
MODEL_IDS = (
    "z-ai/glm-5.3-flash:free",
    "minimax/minimax-m3:free",
    "deepseek/deepseek-v4-flash:free",
)
TIMEOUT_SECONDS = 20
MAX_TOKENS = 4


def _json_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | None, str]:
    data = None
    request_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(1024 * 1024).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = None
            return int(response.status), payload if isinstance(payload, dict) else None, ""
    except urllib.error.HTTPError as exc:
        # Do not persist provider response bodies; they can contain request
        # details or account metadata.
        return int(exc.code), None, "http_error"
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, None, "network_error"


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _credits(api_key: str) -> dict[str, Any]:
    status, payload, error = _json_request(
        CREDITS_URL,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        data = payload if isinstance(payload, dict) else {}
    total_credits = _decimal(data.get("total_credits"))
    total_usage = _decimal(data.get("total_usage"))
    if status != 200 or total_credits is None or total_usage is None:
        return {"checked": False, "status": status, "error": error or "credits_unavailable"}
    # Store only a comparison-safe digest and booleans, never balances.
    digest_payload = {
        "total_credits": str(total_credits),
        "total_usage": str(total_usage),
    }
    digest = hashlib.sha256(json.dumps(digest_payload, sort_keys=True).encode("utf-8")).hexdigest()
    return {"checked": True, "status": status, "digest": digest}


def _catalog() -> tuple[list[dict[str, Any]], str]:
    status, payload, error = _json_request(CATALOG_URL, headers={"Accept": "application/json"})
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return [], error or f"catalog_http_{status}"
    return [item for item in entries if isinstance(item, dict)], ""


def _catalog_state(entries: list[dict[str, Any]], model_id: str) -> dict[str, Any]:
    for entry in entries:
        if entry.get("id") == model_id:
            pricing = entry.get("pricing") if isinstance(entry.get("pricing"), dict) else {}
            prompt = str(pricing.get("prompt", ""))
            completion = str(pricing.get("completion", ""))
            return {
                "listed": True,
                "zero_priced": prompt in {"0", "0.0", "0.00"} and completion in {"0", "0.0", "0.00"},
                "prompt_price": prompt,
                "completion_price": completion,
            }
    return {"listed": False, "zero_priced": False, "prompt_price": None, "completion_price": None}


def _probe_one(model_id: str, api_key: str) -> dict[str, Any]:
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Return OK."}],
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        "stream": False,
        "provider": {"allow_fallbacks": False},
    }
    status, response, error = _json_request(
        CHAT_URL,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "HTTP-Referer": "https://github.com/nyu1791-collab/hf-site-agent",
            "X-Title": "hf-site-agent-free-probe",
        },
        body=payload,
    )
    result: dict[str, Any] = {
        "requested_model": model_id,
        "http_status": status,
        "response_model": None,
        "usage_cost": None,
        "fallback_used": False,
        "request_count": 1,
        "retry_count": 0,
        "status": "FREE_ENDPOINT_UNAVAILABLE",
        "error": error or None,
    }
    if status == 429:
        result["status"] = "FREE_RATE_LIMITED"
        return result
    if status == 404:
        result["status"] = "MODEL_NOT_FOUND"
        return result
    if status != 200 or not isinstance(response, dict):
        return result
    response_model = response.get("model")
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    cost = _decimal(usage.get("cost"))
    result["response_model"] = response_model if isinstance(response_model, str) else None
    result["usage_cost"] = str(cost) if cost is not None else None
    if response_model != model_id:
        result["fallback_used"] = True
        result["status"] = "MODEL_MISMATCH"
    elif cost is None:
        result["status"] = "USAGE_COST_UNAVAILABLE"
    elif cost != Decimal("0"):
        result["status"] = "FREE_COST_NONZERO"
    else:
        result["status"] = "PROBE_SUCCESS_PENDING_CREDITS"
    return result


def main() -> int:
    output_path = Path(os.environ.get("FREE_PROBE_OUTPUT", "artifacts/free_model_probe.json"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    api_key = os.environ.get("AI_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or ""
    entries, catalog_error = _catalog()
    catalog_states = {model_id: _catalog_state(entries, model_id) for model_id in MODEL_IDS}
    report: dict[str, Any] = {
        "schema_version": "free-model-probe-v1",
        "requested_models": list(MODEL_IDS),
        "model_calls": 0,
        "max_tokens": MAX_TOKENS,
        "retries": 0,
        "provider_allow_fallbacks": False,
        "models_parameter_sent": False,
        "web_search": False,
        "paid_fallback": False,
        "catalog_url": CATALOG_URL,
        "catalog_error": catalog_error or None,
        "catalog_inconsistency": any(not item["listed"] for item in catalog_states.values()),
        "catalog": catalog_states,
        "credits_before": None,
        "credits_after": None,
        "credits_checked": False,
        "credits_unchanged": None,
        "results": [],
    }
    if not api_key:
        report["status"] = "BLOCKED_MISSING_SECRET"
        report["reason"] = "OpenRouter API secret is not configured; no model call was sent."
    else:
        before = _credits(api_key)
        report["credits_before"] = {key: value for key, value in before.items() if key != "digest"}
        report["credits_checked"] = before.get("checked") is True
        for model_id in MODEL_IDS:
            report["model_calls"] += 1
            result = _probe_one(model_id, api_key)
            report["results"].append(result)
        after = _credits(api_key)
        report["credits_after"] = {key: value for key, value in after.items() if key != "digest"}
        report["credits_checked"] = before.get("checked") is True and after.get("checked") is True
        report["credits_unchanged"] = (
            report["credits_checked"] and before.get("digest") == after.get("digest")
        )
        for result in report["results"]:
            if result["status"] == "PROBE_SUCCESS_PENDING_CREDITS":
                if not report["credits_checked"]:
                    result["status"] = "FREE_CREDITS_UNVERIFIED"
                elif not report["credits_unchanged"]:
                    result["status"] = "FREE_CREDITS_CHANGED"
                else:
                    result["status"] = "FREE_ACTIVE"
        statuses = {result["status"] for result in report["results"]}
        report["status"] = "FREE_ACTIVE" if statuses == {"FREE_ACTIVE"} else "COMPLETED_WITH_BLOCKS"
    output_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    public = {
        "status": report["status"],
        "model_calls": report["model_calls"],
        "requested_models": list(MODEL_IDS),
        "results": [
            {
                "requested_model": item.get("requested_model"),
                "status": item.get("status"),
                "http_status": item.get("http_status"),
                "response_model": item.get("response_model"),
                "usage_cost": item.get("usage_cost"),
                "fallback_used": item.get("fallback_used"),
            }
            for item in report["results"]
        ],
        "catalog_inconsistency": report["catalog_inconsistency"],
        "credits_checked": report["credits_checked"],
        "credits_unchanged": report["credits_unchanged"],
        "paid_fallback": False,
    }
    print(json.dumps(public, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
