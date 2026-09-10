#!/usr/bin/env python3
"""Bounded recovery wrapper for live staging provider calls.

A transport timeout or connection error may have reached the provider and is
therefore never replayed here. An explicit HTTP 5xx response is different: the
provider has conclusively rejected that request. For that narrow case only,
this module permits one fresh, separately identified recovery attempt.

The recovery request is deliberately smaller than the failed full-context
request. This distinguishes transient/capacity failures from workload-size
failures without starting a retry storm or changing model/provider/route.
"""

from __future__ import annotations

from dataclasses import replace
import json
from typing import Any, Mapping, Sequence

from scripts.agent_runtime import stable_hash
from scripts.mission_scheduler import ProviderInterrupted
from scripts.provider_adapters import ProviderAdapterError
import scripts.live_staging_runner as live_runner


MAX_CONCLUSIVE_5XX_RECOVERIES = 1
RECOVERY_MAX_OUTPUT_TOKENS = 4_096
RECOVERY_REPOSITORY_CONTEXT_CHARS = 20_000
RECOVERY_FILE_CHARS = 4_000
RECOVERY_USER_MESSAGE_CHARS = 32_000


def _is_conclusive_retryable_5xx(exc: ProviderAdapterError) -> bool:
    status = exc.http_status
    return (
        isinstance(status, int)
        and 500 <= status <= 599
        and exc.retryable is True
        and exc.error_class == "TEMPORARY_PROVIDER_ERROR"
    )


def _compact_repository_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve mission identity/objective while shrinking source evidence."""
    compact = dict(payload)
    repository = compact.get("repository_context")
    if not isinstance(repository, Mapping):
        return compact

    compact_repository = {
        key: repository.get(key)
        for key in ("source_head", "read_only", "fixed_allowlist")
        if key in repository
    }
    files = repository.get("files") if isinstance(repository.get("files"), Mapping) else {}
    compact_files: dict[str, str] = {}
    remaining = RECOVERY_REPOSITORY_CONTEXT_CHARS
    for path, raw_text in files.items():
        if remaining <= 0:
            break
        if not isinstance(path, str) or not isinstance(raw_text, str):
            continue
        allowance = min(RECOVERY_FILE_CHARS, remaining)
        text = raw_text[:allowance]
        compact_files[path[:240]] = text
        remaining -= len(text)
    compact_repository["files"] = compact_files
    compact_repository["recovery_compacted"] = True
    compact_repository["recovery_context_char_ceiling"] = RECOVERY_REPOSITORY_CONTEXT_CHARS
    compact["repository_context"] = compact_repository
    compact["transport_recovery"] = {
        "reason": "CONCLUSIVE_TEMPORARY_5XX",
        "same_model": True,
        "same_provider": True,
        "same_route": True,
        "reduced_context": True,
    }
    return compact


def _compact_recovery_messages(messages: Any) -> Any:
    """Deterministically reduce the user JSON context for the one recovery."""
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes, bytearray)):
        return messages
    output: list[Any] = []
    for item in messages:
        if not isinstance(item, Mapping):
            output.append(item)
            continue
        message = dict(item)
        if str(message.get("role") or "").lower() != "user" or not isinstance(message.get("content"), str):
            output.append(message)
            continue
        content = str(message["content"])
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError, json.JSONDecodeError):
            # Non-JSON user prompts are still bounded rather than rejected.
            message["content"] = content[:RECOVERY_USER_MESSAGE_CHARS]
        else:
            if isinstance(parsed, Mapping):
                compact = _compact_repository_payload(parsed)
                message["content"] = json.dumps(
                    compact,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )[:RECOVERY_USER_MESSAGE_CHARS]
            else:
                message["content"] = content[:RECOVERY_USER_MESSAGE_CHARS]
        output.append(message)
    return output


class _ConclusiveRecoveryAdapter:
    """Proxy one adapter and recover once only after an explicit HTTP 5xx."""

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
                call_options["request_id"] = self._retry_request_id(base_request_id, attempt)
                call_options["max_tokens"] = min(reserved_output_tokens, RECOVERY_MAX_OUTPUT_TOKENS)
                call_messages = _compact_recovery_messages(messages)
                self.recovery_compacted = True
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
                    if (
                        attempt < max_attempts
                        and self._metrics.may_call(
                            self.provider_id,
                            reserved_output_tokens=min(reserved_output_tokens, RECOVERY_MAX_OUTPUT_TOKENS),
                        )
                    ):
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
    """Call the normal live path with one safe, degraded 5xx recovery."""
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
    return result


__all__ = [
    "MAX_CONCLUSIVE_5XX_RECOVERIES",
    "RECOVERY_MAX_OUTPUT_TOKENS",
    "RECOVERY_REPOSITORY_CONTEXT_CHARS",
    "call_model_with_bounded_recovery",
]
