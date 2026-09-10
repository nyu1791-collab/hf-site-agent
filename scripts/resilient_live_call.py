#!/usr/bin/env python3
"""Bounded recovery wrapper for live staging provider calls.

A transport timeout or connection error may have reached the provider and is
therefore never replayed here.  An explicit HTTP 5xx response is different: the
provider has conclusively rejected that request.  For that narrow case only,
this module permits one fresh, separately identified recovery attempt while
keeping the provider adapter itself retry-free.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from scripts.agent_runtime import stable_hash
from scripts.mission_scheduler import ProviderInterrupted
from scripts.provider_adapters import ProviderAdapterError
import scripts.live_staging_runner as live_runner


MAX_CONCLUSIVE_5XX_RECOVERIES = 1


def _is_conclusive_retryable_5xx(exc: ProviderAdapterError) -> bool:
    status = exc.http_status
    return (
        isinstance(status, int)
        and 500 <= status <= 599
        and exc.retryable is True
        and exc.error_class == "TEMPORARY_PROVIDER_ERROR"
    )


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
            if attempt > 1:
                call_options["request_id"] = self._retry_request_id(base_request_id, attempt)
            self.attempts_started += 1
            try:
                return dict(self._adapter.generate(model_id, messages, **call_options))
            except ProviderAdapterError as exc:
                status = exc.http_status
                if isinstance(status, int):
                    # An explicit HTTP response proves this network attempt
                    # finished. Count it even though no model payload was
                    # adopted; usage tokens remain zero because none were
                    # reported by the failed response.
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
                            reserved_output_tokens=reserved_output_tokens,
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
    """Call the normal live path with one safe recovery after explicit 5xx.

    The original live parser/validator remains authoritative.  The proxy only
    changes transport recovery and accounting.  If every failed attempt ended
    with a concrete HTTP response, the scheduler can settle those requests
    instead of incorrectly retaining them as usage-unknown.  Any ambiguous
    transport failure keeps the existing unsettled/no-replay behavior.
    """
    # Adaptive callback unit tests and third-party callback adapters may supply
    # lightweight binding doubles. They must continue through the established
    # live-call seam; only a real LiveAgentBinding is eligible for transport
    # recovery because only it carries verified execution policy and adapter
    # identity.
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
            raise ProviderInterrupted(
                str(exc),
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
    return result


__all__ = [
    "MAX_CONCLUSIVE_5XX_RECOVERIES",
    "call_model_with_bounded_recovery",
]
