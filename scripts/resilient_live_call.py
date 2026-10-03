#!/usr/bin/env python3
"""Bounded recovery wrapper for live staging provider calls.

Ambiguous transport failures may have reached the provider and are never
replayed here. Explicit HTTP failures are different because the attempt has a
confirmed terminal response. Retryable 5xx failures get bounded, progressively
smaller recovery attempts.

The focused Google commander route has one additional availability rule: an
initial HTTP 429 may be retried at most twice after long cooldowns, but only
when the exact focused Gemini transport is bound to a verified STAGING free
route with paid fallback disabled. Generic providers and non-focused Google
routes keep the original first-429 fail-closed behavior. A 429 following
confirmed 5xx recovery pressure uses its own bounded cooldown path.

Every recovery uses the exact same provider/model/free route, a fresh request
identity, progressively smaller context/output, and a bounded cooldown. An
ambiguous timeout/network failure is never replayed.
"""

from __future__ import annotations

from dataclasses import replace
import json
import time
from typing import Any, Mapping, Sequence

from scripts.agent_runtime import stable_hash
from scripts.execution_scope import ExecutionPolicy
from scripts.mission_scheduler import ProviderInterrupted
from scripts.provider_adapters import ProviderAdapterError
import scripts.live_staging_runner as live_runner


MAX_CONCLUSIVE_5XX_RECOVERIES = 2
MAX_PRIMARY_RATE_LIMIT_RECOVERIES = 2
MAX_POST_5XX_RATE_LIMIT_RECOVERIES = 1
MAX_TRAILING_RATE_LIMIT_RECOVERIES = 1
RECOVERY_MAX_OUTPUT_TOKENS = 4_096
RECOVERY_REPOSITORY_CONTEXT_CHARS = 20_000
RECOVERY_FILE_CHARS = 4_000
RECOVERY_USER_MESSAGE_CHARS = 32_000
FINAL_RECOVERY_MAX_OUTPUT_TOKENS = 2_048
FINAL_RECOVERY_REPOSITORY_CONTEXT_CHARS = 8_000
FINAL_RECOVERY_FILE_CHARS = 2_000
FINAL_RECOVERY_USER_MESSAGE_CHARS = 16_000
# Short retries amplified shared free-tier pressure. Use long, bounded waits
# for explicit 429s while preserving the no-replay rule for ambiguous calls.
RECOVERY_BACKOFF_SECONDS = (15.0, 30.0)
PRIMARY_RATE_LIMIT_BACKOFF_SECONDS = (90.0, 120.0)
POST_5XX_RATE_LIMIT_BACKOFF_SECONDS = 65.0
TRAILING_RATE_LIMIT_BACKOFF_SECONDS = 120.0
MAX_PROVIDER_RETRY_AFTER_SECONDS = 120.0


def _is_conclusive_retryable_5xx(exc: ProviderAdapterError) -> bool:
    status = exc.http_status
    return (
        isinstance(status, int)
        and 500 <= status <= 599
        and exc.retryable is True
        and exc.error_class == "TEMPORARY_PROVIDER_ERROR"
    )


def _is_rate_limit(exc: ProviderAdapterError) -> bool:
    return exc.http_status == 429 and exc.error_class == "RATE_LIMITED"


def _is_post_5xx_rate_limit(exc: ProviderAdapterError, *, prior_5xx_failures: int) -> bool:
    return prior_5xx_failures > 0 and _is_rate_limit(exc)


def _focused_google_primary_rate_limit_recovery_allowed(binding: Any) -> bool:
    """Authorize first-429 recovery only on the real focused free-tier lane."""
    if not isinstance(binding, live_runner.LiveAgentBinding):
        return False
    policy = binding.execution_policy
    return (
        binding.provider_id == "google"
        and isinstance(policy, ExecutionPolicy)
        and policy.scope == "STAGING"
        and policy.staging_free_route_allowed is True
        and policy.paid_fallback is False
        and policy.exact_model_verified is True
        and policy.endpoint_verified is True
        and policy.auth_verified is True
        and getattr(binding.adapter, "focused_commander_transport", False) is True
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


def _primary_rate_limit_delay_seconds(recovery_number: int, retry_after_seconds: int | None) -> float:
    index = max(0, min(recovery_number - 1, len(PRIMARY_RATE_LIMIT_BACKOFF_SECONDS) - 1))
    base = float(PRIMARY_RATE_LIMIT_BACKOFF_SECONDS[index])
    if isinstance(retry_after_seconds, int) and retry_after_seconds >= 0:
        return min(MAX_PROVIDER_RETRY_AFTER_SECONDS, max(base, float(retry_after_seconds)))
    return base


def _rate_limit_delay_seconds(retry_after_seconds: int | None, *, trailing: bool = False) -> float:
    base = TRAILING_RATE_LIMIT_BACKOFF_SECONDS if trailing else POST_5XX_RATE_LIMIT_BACKOFF_SECONDS
    if isinstance(retry_after_seconds, int) and retry_after_seconds >= 0:
        return min(
            MAX_PROVIDER_RETRY_AFTER_SECONDS,
            max(base, float(retry_after_seconds)),
        )
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
        "reason": "CONCLUSIVE_TEMPORARY_HTTP_FAILURE",
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
    """Proxy one adapter and recover only after confirmed terminal HTTP failures."""

    def __init__(
        self,
        adapter: Any,
        *,
        metrics: live_runner.LiveCallMetrics,
        allow_primary_rate_limit_recovery: bool = False,
    ) -> None:
        self._adapter = adapter
        self._metrics = metrics
        self.provider_id = str(getattr(adapter, "provider_id", ""))
        self.config = getattr(adapter, "config", {})
        self.allow_primary_rate_limit_recovery = bool(allow_primary_rate_limit_recovery)
        self.attempts_started = 0
        self.confirmed_http_failures = 0
        self.conclusive_5xx_failures = 0
        self.primary_rate_limit_failures = 0
        self.post_5xx_rate_limit_failures = 0
        self.trailing_rate_limit_failures = 0
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
            "reason": "CONCLUSIVE_HTTP_RECOVERY",
        })[:32]

    def _may_recover(self, *, reserved_output_tokens: int, recovery_number: int) -> bool:
        profile = _recovery_profile(recovery_number)
        return self._metrics.may_call(
            self.provider_id,
            reserved_output_tokens=min(reserved_output_tokens, profile["max_output_tokens"]),
        )

    def generate(self, model_id: str, messages: Any, **options: Any) -> dict[str, Any]:
        base_request_id = str(options.get("request_id") or "")[:64]
        try:
            reserved_output_tokens = max(1, int(options.get("max_tokens", 256)))
        except (TypeError, ValueError):
            reserved_output_tokens = 256

        max_attempts = (
            1
            + MAX_CONCLUSIVE_5XX_RECOVERIES
            + MAX_POST_5XX_RATE_LIMIT_RECOVERIES
            + MAX_TRAILING_RATE_LIMIT_RECOVERIES
            + (MAX_PRIMARY_RATE_LIMIT_RECOVERIES if self.allow_primary_rate_limit_recovery else 0)
        )
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
                    recovery_number = self.conclusive_5xx_failures
                    if (
                        self.conclusive_5xx_failures <= MAX_CONCLUSIVE_5XX_RECOVERIES
                        and attempt < max_attempts
                        and self._may_recover(
                            reserved_output_tokens=reserved_output_tokens,
                            recovery_number=recovery_number,
                        )
                    ):
                        delay = _recovery_delay_seconds(recovery_number, exc.retry_after_seconds)
                        if delay > 0:
                            time.sleep(delay)
                            self.total_backoff_seconds += delay
                        continue

                if _is_post_5xx_rate_limit(exc, prior_5xx_failures=self.conclusive_5xx_failures):
                    if (
                        self.post_5xx_rate_limit_failures < MAX_POST_5XX_RATE_LIMIT_RECOVERIES
                        and attempt < max_attempts
                        and self._may_recover(
                            reserved_output_tokens=reserved_output_tokens,
                            recovery_number=max(2, attempt),
                        )
                    ):
                        self.post_5xx_rate_limit_failures += 1
                        delay = _rate_limit_delay_seconds(exc.retry_after_seconds)
                        if delay > 0:
                            time.sleep(delay)
                            self.total_backoff_seconds += delay
                        continue
                    if (
                        self.post_5xx_rate_limit_failures >= MAX_POST_5XX_RATE_LIMIT_RECOVERIES
                        and self.trailing_rate_limit_failures < MAX_TRAILING_RATE_LIMIT_RECOVERIES
                        and attempt < max_attempts
                        and self._may_recover(
                            reserved_output_tokens=reserved_output_tokens,
                            recovery_number=max(2, attempt),
                        )
                    ):
                        self.trailing_rate_limit_failures += 1
                        delay = _rate_limit_delay_seconds(exc.retry_after_seconds, trailing=True)
                        if delay > 0:
                            time.sleep(delay)
                            self.total_backoff_seconds += delay
                        continue

                if (
                    _is_rate_limit(exc)
                    and self.conclusive_5xx_failures == 0
                    and self.allow_primary_rate_limit_recovery
                    and self.primary_rate_limit_failures < MAX_PRIMARY_RATE_LIMIT_RECOVERIES
                    and attempt < max_attempts
                    and self._may_recover(
                        reserved_output_tokens=reserved_output_tokens,
                        recovery_number=max(1, attempt),
                    )
                ):
                    # HTTP 429 is a terminal refusal, so there is no unknown
                    # inference result to duplicate. Only the focused Google
                    # free-tier commander lane is allowed this recovery.
                    self.primary_rate_limit_failures += 1
                    delay = _primary_rate_limit_delay_seconds(
                        self.primary_rate_limit_failures,
                        exc.retry_after_seconds,
                    )
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
    """Call the normal live path with bounded progressive HTTP recovery."""
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

    proxy = _ConclusiveRecoveryAdapter(
        binding.adapter,
        metrics=metrics,
        allow_primary_rate_limit_recovery=_focused_google_primary_rate_limit_recovery_allowed(binding),
    )
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
        result["primary_rate_limit_recovery_count"] = min(
            proxy.primary_rate_limit_failures,
            MAX_PRIMARY_RATE_LIMIT_RECOVERIES,
        )
        result["post_5xx_rate_limit_recovery_count"] = min(
            proxy.post_5xx_rate_limit_failures,
            MAX_POST_5XX_RATE_LIMIT_RECOVERIES,
        )
        result["trailing_rate_limit_recovery_count"] = min(
            proxy.trailing_rate_limit_failures,
            MAX_TRAILING_RATE_LIMIT_RECOVERIES,
        )
        result["recovery_context_compacted"] = proxy.recovery_compacted
        result["recovery_level_reached"] = proxy.recovery_level_reached
        result["recovery_backoff_seconds"] = proxy.total_backoff_seconds
    return result


__all__ = [
    "MAX_CONCLUSIVE_5XX_RECOVERIES",
    "MAX_PRIMARY_RATE_LIMIT_RECOVERIES",
    "MAX_POST_5XX_RATE_LIMIT_RECOVERIES",
    "MAX_TRAILING_RATE_LIMIT_RECOVERIES",
    "RECOVERY_MAX_OUTPUT_TOKENS",
    "RECOVERY_REPOSITORY_CONTEXT_CHARS",
    "FINAL_RECOVERY_MAX_OUTPUT_TOKENS",
    "FINAL_RECOVERY_REPOSITORY_CONTEXT_CHARS",
    "RECOVERY_BACKOFF_SECONDS",
    "PRIMARY_RATE_LIMIT_BACKOFF_SECONDS",
    "POST_5XX_RATE_LIMIT_BACKOFF_SECONDS",
    "TRAILING_RATE_LIMIT_BACKOFF_SECONDS",
    "call_model_with_bounded_recovery",
]
