#!/usr/bin/env python3
"""Bounded recovery wrapper for live staging provider calls.

Ambiguous transport failures may have reached the provider and are never
replayed here. An explicit retryable HTTP 5xx is different: the provider has
conclusively rejected that request. For that narrow case only, this module
permits two fresh, separately identified recovery attempts.

Recovery is progressive rather than a retry storm. The first recovery keeps a
moderate repository slice; the second keeps only the mission-critical slice and
uses a smaller output budget. Both stay on the exact same provider/model/free
route, and short bounded backoff gives a temporarily overloaded service time to
recover.
"""

from __future__ import annotations

from dataclasses import replace
import json
import time
from typing import Any, Mapping, Sequence

from scripts.agent_runtime import stable_hash
from scripts.mission_scheduler import ProviderInterrupted
from scripts.provider_adapters import ProviderAdapterError
import scripts.live_staging_runner as live_runner


MAX_CONCLUSIVE_5XX_RECOVERIES = 2
RECOVERY_MAX_OUTPUT_TOKENS = 4_096
RECOVERY_REPOSITORY_CONTEXT_CHARS = 20_000
RECOVERY_FILE_CHARS = 4_000
RECOVERY_USER_MESSAGE_CHARS = 32_000
FINAL_RECOVERY_MAX_OUTPUT_TOKENS = 2_048
FINAL_RECOVERY_REPOSITORY_CONTEXT_CHARS = 8_000
FINAL_RECOVERY_FILE_CHARS = 2_000
FINAL_RECOVERY_USER_MESSAGE_CHARS = 16_000
RECOVERY_BACKOFF_SECONDS = (2.0, 5.0)
MAX_PROVIDER_RETRY_AFTER_SECONDS = 10.0


def _is_conclusive_retryable_5xx(exc: ProviderAdapterError) -> bool:
    status = exc.http_status
    return (
        isinstance(status, int)
        and 500 <= status <= 599
        and exc.retryable is True
        and exc.error_class == "TEMPORARY_PROVIDER_ERROR"
    )


def _recovery_profile(recovery_number: int) -> dict[str, int]:
    if recovery_number <= 1:
        return {
            "max_output_tokens": RECOVERY_MAX_OUTPUT_TOKENS,
            "repository_chars": RECOVERY_REPOSITORY_CONTEXT_CHARS,
            "file_chars": RECOVERY_FILE_CHARS,
            "user_chars": RECOVERY_USER_MESSAGE_CHARS,
        }
    return {
        "max_output_tokens": FINAL_RECOVERY_MAX_OUTPUT_TOKENS,
        "repository_chars": FINAL_RECOVERY_REPOSITORY_CONTEXT_CHARS,
        "file_chars": FINAL_RECOVERY_FILE_CHARS,
        "user_chars": FINAL_RECOVERY_USER_MESSAGE_CHARS,
    }


def _recovery_delay_seconds(recovery_number: int, retry_after_seconds: int | None) -> float:
    index = max(0, min(recovery_number - 1, len(RECOVERY_BACKOFF_SECONDS) - 1))
    base = float(RECOVERY_BACKOFF_SECONDS[index])
    if isinstance(retry_after_seconds, int) and retry_after_seconds >= 0:
        return min(MAX_PROVIDER_RETRY_AFTER_SECONDS, max(base, float(retry_after_seconds)))
    return base


def _compact_repository_payload(payload: Mapping[str, Any], *, recovery_number: int = 1) -> dict[str, Any]:
    """Preserve mission identity/objective while progressively shrinking source evidence."""
    compact = dict(payload)
    repository = compact.get("repository_context")
    if not isinstance(repository, Mapping):
        return compact

    profile = _recovery_profile(recovery_number)
    repository_limit = profile["repository_chars"]
    file_limit = profile["file_chars"]
    compact_repository = {
        key: repository.get(key)
        for key in ("source_head", "read_only", "fixed_allowlist")
        if key in repository
    }
    files = repository.get("files") if isinstance(repository.get("files"), Mapping) else {}
    compact_files: dict[str, str] = {}
    remaining = repository_limit
    for path, raw_text in files.items():
        if remaining <= 0:
            break
        if not isinstance(path, str) or not isinstance(raw_text, str):
            continue
        allowance = min(file_limit, remaining)
        text = raw_text[:allowance]
        compact_files[path[:240]] = text
        remaining -= len(text)
    compact_repository["files"] = compact_files
    compact_repository["recovery_compacted"] = True
    compact_repository["recovery_number"] = max(1, int(recovery_number))
    compact_repository["recovery_context_char_ceiling"] = repository_limit
    compact["repository_context"] = compact_repository
    compact["transport_recovery"] = {
        "reason": "CONCLUSIVE_TEMPORARY_5XX",
        "same_model": True,
        "same_provider": True,
        "same_route": True,
        "reduced_context": True,
        "recovery_number": max(1, int(recovery_number)),
    }
    return compact


def _compact_recovery_messages(messages: Any, *, recovery_number: int = 1) -> Any:
    """Deterministically reduce the user JSON context for one recovery level."""
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes, bytearray)):
        return messages
    profile = _recovery_profile(recovery_number)
    user_limit = profile["user_chars"]
    output: list[Any] = []
    for item in messages:
        if not isinstance(item, Mapping):
            output.append(item)
            continue
        message = dict(item)
        role = str(message.get("role") or "").lower()
        if not isinstance(message.get("content"), str):
            output.append(message)
            continue
        content = str(message["content"])
        if role in {"system", "developer"}:
            # Keep the contract/instruction but prevent an oversized static
            # instruction from defeating the aggressive final recovery.
            system_limit = 2_000 if recovery_number <= 1 else 1_200
            message["content"] = content[:system_limit]
            output.append(message)
            continue
        if role != "user":
            output.append(message)
            continue
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError, json.JSONDecodeError):
            message["content"] = content[:user_limit]
        else:
            if isinstance(parsed, Mapping):
                compact = _compact_repository_payload(parsed, recovery_number=recovery_number)
                message["content"] = json.dumps(
                    compact,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )[:user_limit]
            else:
                message["content"] = content[:user_limit]
        output.append(message)
    return output


class _ConclusiveRecoveryAdapter:
    """Proxy one adapter and recover only after explicit retryable HTTP 5xx."""

    def __init__(self, adapter: Any, *, metrics: live_runner.LiveCallMetrics) -> None:
        self._adapter = adapter
        self._metrics = metrics
        self.provider_id = str(getattr(adapter, "provider_id", ""))
        self.config = getattr(adapter, "config", {})
        self.attempts_started = 0
        self.confirmed_http_failures = 0
        self.conclusive_5xx_failures = 0
        self.ambiguous_failures = 0
        self.last_http_status: int | None = None
        self.recovery_compacted = False
        self.recovery_level_reached = 0
        self.total_backoff_seconds = 0.0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)

    @staticmethod
    def _retry_request_id(base_request_id: str, attempt: int) -> str:
        return stable_hash({
            "base_request_id": base_request_id,
            "recovery_attempt": attempt,
            "reason": "CONCLUSIVE_TEMPORARY_5XX",
        })[:32]

    def generate(self, model_id: str, messages: Any, **options: Any) -> dict[str, Any]:
        base_request_id = str(options.get("request_id") or "")[:64]
        try:
            reserved_output_tokens = max(1, int(options.get("max_tokens", 256)))
        except (TypeError, ValueError):
            reserved_output_tokens = 256

        max_attempts = 1 + MAX_CONCLUSIVE_5XX_RECOVERIES
        for attempt in range(1, max_attempts + 1):
            call_options = dict(options)
            call_messages = messages
            if attempt > 1:
                recovery_number = attempt - 1
                profile = _recovery_profile(recovery_number)
                call_options["request_id"] = self._retry_request_id(base_request_id, attempt)
                call_options["max_tokens"] = min(reserved_output_tokens, profile["max_output_tokens"])
                call_messages = _compact_recovery_messages(messages, recovery_number=recovery_number)
                self.recovery_compacted = True
                self.recovery_level_reached = max(self.recovery_level_reached, recovery_number)
            self.attempts_started += 1
            try:
                return dict(self._adapter.generate(model_id, call_messages, **call_options))
            except ProviderAdapterError as exc:
                status = exc.http_status
                self.last_http_status = status if isinstance(status, int) else None
                if isinstance(status, int):
                    # A concrete HTTP response proves this attempt ended. Count
                    # it even though no model payload was adopted; provider
                    # tokens remain zero because none were reported.
                    self.confirmed_http_failures += 1
                    self._metrics.record(self.provider_id, model_id, {})
                else:
                    self.ambiguous_failures += 1

                if _is_conclusive_retryable_5xx(exc):
                    self.conclusive_5xx_failures += 1
                    recovery_number = attempt
                    if attempt < max_attempts:
                        profile = _recovery_profile(recovery_number)
                        if self._metrics.may_call(
                            self.provider_id,
                            reserved_output_tokens=min(reserved_output_tokens, profile["max_output_tokens"]),
                        ):
                            delay = _recovery_delay_seconds(recovery_number, exc.retry_after_seconds)
                            if delay > 0:
                                time.sleep(delay)
                                self.total_backoff_seconds += delay
                            continue
                raise
            except Exception:
                self.ambiguous_failures += 1
                raise

        raise ProviderAdapterError("RECOVERY_LOOP_EXHAUSTED")  # pragma: no cover


def call_model_with_bounded_recovery(
    binding: Any,
    task: Any,
    context: Mapping[str, Any],
    *,
    metrics: live_runner.LiveCallMetrics,
    instruction: str,
) -> dict[str, Any]:
    """Call the normal live path with bounded progressive 5xx recovery."""
    # Lightweight callback doubles must continue through the established seam.
    # Only a real binding carries enough verified policy/identity for transport
    # recovery to be authorized.
    if not isinstance(binding, live_runner.LiveAgentBinding):
        return live_runner._call_model(
            binding,
            task,
            context,
            metrics=metrics,
            instruction=instruction,
        )

    proxy = _ConclusiveRecoveryAdapter(binding.adapter, metrics=metrics)
    proxy_binding = replace(binding, adapter=proxy)
    try:
        result = live_runner._call_model(
            proxy_binding,
            task,
            context,
            metrics=metrics,
            instruction=instruction,
        )
    except ProviderInterrupted as exc:
        if (
            proxy.attempts_started > 0
            and proxy.ambiguous_failures == 0
            and proxy.confirmed_http_failures == proxy.attempts_started
        ):
            suffix = f":HTTP_{proxy.last_http_status}" if proxy.last_http_status is not None else ""
            raise ProviderInterrupted(
                f"{exc}{suffix}",
                actual_requests=proxy.attempts_started,
                actual_tokens=0,
            ) from None
        raise

    if proxy.confirmed_http_failures:
        result = dict(result)
        result["requests_used"] = int(result.get("requests_used", 1) or 1) + proxy.confirmed_http_failures
        result["provider_attempts"] = proxy.attempts_started
        result["conclusive_5xx_recovery_count"] = min(
            proxy.conclusive_5xx_failures,
            MAX_CONCLUSIVE_5XX_RECOVERIES,
        )
        result["recovery_context_compacted"] = proxy.recovery_compacted
        result["recovery_level_reached"] = proxy.recovery_level_reached
        result["recovery_backoff_seconds"] = proxy.total_backoff_seconds
    return result


__all__ = [
    "MAX_CONCLUSIVE_5XX_RECOVERIES",
    "RECOVERY_MAX_OUTPUT_TOKENS",
    "RECOVERY_REPOSITORY_CONTEXT_CHARS",
    "FINAL_RECOVERY_MAX_OUTPUT_TOKENS",
    "FINAL_RECOVERY_REPOSITORY_CONTEXT_CHARS",
    "RECOVERY_BACKOFF_SECONDS",
    "call_model_with_bounded_recovery",
]
