#!/usr/bin/env python3
"""Read-only smoke test for OpenAI-compatible model providers.

The test sends exactly one short request, never falls back to another provider,
and never prints the API key or model response.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any

PROVIDER_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "deepseek": "https://api.deepseek.com",
    "huggingface": "https://router.huggingface.co/v1",
}
TRANSIENT_STATUSES = {429}
REQUEST_TEXT = "Reply with OK."


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def valid_model(value: str) -> bool:
    if not value or len(value) > 160:
        return False
    return not any(ord(char) < 32 or ord(char) == 127 for char in value)


def classify_status(status: int | None) -> str:
    if status == 402:
        return "paid_or_credit_required"
    if status in (401, 403):
        return "authentication_or_permission_failed"
    if status == 429:
        return "rate_limited"
    if status is not None and 500 <= status <= 599:
        return "provider_unavailable"
    return "provider_request_failed"


def main() -> int:
    provider = os.getenv("AI_PROVIDER", "").strip().lower()
    model = os.getenv("AI_MODEL", "").strip()
    api_key = os.getenv("AI_API_KEY", "")
    if provider not in PROVIDER_BASE_URLS:
        emit({"ok": False, "error": "unsupported_provider"})
        return 2
    if not valid_model(model):
        emit({"ok": False, "error": "invalid_model"})
        return 2
    if not api_key or any(ord(char) < 32 for char in api_key):
        emit({"ok": False, "error": "missing_api_key"})
        return 2

    timeout_ms = bounded_int("AI_TIMEOUT_MS", 15000, 3000, 30000)
    max_retries = bounded_int("AI_MAX_RETRIES", 1, 0, 1)
    try:
        from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
    except ModuleNotFoundError:
        emit({"ok": False, "error": "openai_sdk_not_installed"})
        return 2

    client = OpenAI(
        api_key=api_key,
        base_url=PROVIDER_BASE_URLS[provider],
        timeout=timeout_ms / 1000,
        max_retries=0,
    )

    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": REQUEST_TEXT}],
                temperature=0.1,
                max_tokens=1,
                stream=False,
            )
            choices = getattr(response, "choices", None) or []
            if not choices:
                emit({"ok": False, "provider": provider, "error": "empty_response"})
                return 1
            emit({
                "ok": True,
                "provider": provider,
                "base_url": PROVIDER_BASE_URLS[provider],
                "model_configured": True,
                "attempts": attempt + 1,
                "response_received": True,
            })
            return 0
        except APIStatusError as error:
            status = getattr(error, "status_code", None)
            category = classify_status(status)
            if status == 402:
                emit({"ok": False, "provider": provider, "status": 402, "error": category})
                return 2
            if status in TRANSIENT_STATUSES or (status is not None and 500 <= status <= 599):
                if attempt < max_retries:
                    time.sleep(min(2 ** attempt, 2))
                    continue
            emit({"ok": False, "provider": provider, "status": status, "error": category})
            return 1
        except (APITimeoutError, APIConnectionError):
            if attempt < max_retries:
                time.sleep(min(2 ** attempt, 2))
                continue
            emit({"ok": False, "provider": provider, "error": "timeout_or_connection_failed"})
            return 1
        except Exception:
            # Deliberately omit the provider's raw message; it may contain
            # request details, endpoint data, or credentials.
            emit({"ok": False, "provider": provider, "error": "unexpected_provider_error"})
            return 1

    emit({"ok": False, "provider": provider, "error": "retry_budget_exhausted"})
    return 1


if __name__ == "__main__":
    sys.exit(main())
