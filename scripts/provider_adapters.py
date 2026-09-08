#!/usr/bin/env python3
"""Common, guarded adapter interface for configured AI providers.

Adapters are inert by default: ``network_enabled`` must be explicitly set by a
probe caller, and normal generation additionally requires the provider to be
enabled in the registry.  Errors are normalized without retaining provider
response bodies or secret values.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from .provider_registry import ProviderRegistryError, provider_config, validate_provider_registry
from .provider_controls import ProviderQuotaLedger, QuotaGuardError


class ProviderAdapterError(RuntimeError):
    """Safe provider error with a normalized class and no raw response body."""

    def __init__(self, error_class: str, message: str = "provider operation blocked", *, http_status: int | None = None, retry_after_seconds: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.error_class = error_class
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds
        self.retryable = retryable


@dataclass(frozen=True)
class NormalizedProviderError:
    error_class: str
    http_status: int | None
    retry_after_seconds: int | None
    retryable: bool


@dataclass(frozen=True)
class AdapterResponse:
    payload: Mapping[str, Any]
    headers: Mapping[str, str]
    latency_ms: int


def normalize_error(error: BaseException, *, provider_id: str = "") -> NormalizedProviderError:
    status = getattr(error, "code", None)
    if isinstance(error, HTTPError):
        try:
            retry_after_value = error.headers.get("Retry-After")
        except AttributeError:
            retry_after_value = None
        try:
            retry_after = max(0, int(retry_after_value)) if retry_after_value else None
        except (TypeError, ValueError):
            retry_after = None
        if status in {401}:
            return NormalizedProviderError("AUTH_ERROR", status, retry_after, False)
        if status in {403}:
            return NormalizedProviderError("PERMISSION_ERROR", status, retry_after, False)
        if status in {404}:
            return NormalizedProviderError("MODEL_UNAVAILABLE", status, retry_after, False)
        if status in {402}:
            return NormalizedProviderError("CREDIT_EXHAUSTED", status, retry_after, False)
        if status in {429}:
            return NormalizedProviderError("RATE_LIMITED", status, retry_after, False)
        if isinstance(status, int) and 500 <= status <= 599:
            return NormalizedProviderError("TEMPORARY_PROVIDER_ERROR", status, retry_after, True)
        return NormalizedProviderError("PROVIDER_HTTP_ERROR", status, retry_after, False)
    if isinstance(error, TimeoutError):
        return NormalizedProviderError("NETWORK_TIMEOUT", status, None, True)
    if isinstance(error, URLError):
        return NormalizedProviderError("NETWORK_ERROR", status, None, True)
    if isinstance(error, json.JSONDecodeError):
        return NormalizedProviderError("MODEL_OUTPUT_INVALID", status, None, False)
    if isinstance(error, ProviderAdapterError):
        return NormalizedProviderError(error.error_class, error.http_status, error.retry_after_seconds, error.retryable)
    return NormalizedProviderError("PROVIDER_ERROR", status, None, False)


class ProviderAdapter:
    """Provider-neutral contract used by commander and worker routes."""

    def list_models(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def probe(self, model_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def generate(self, model_id: str, messages: Sequence[Mapping[str, Any]], **options: Any) -> dict[str, Any]:
        raise NotImplementedError

    def tool_call(self, model_id: str, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]], **options: Any) -> dict[str, Any]:
        raise NotImplementedError

    def get_usage(self) -> dict[str, Any]:
        raise NotImplementedError

    def get_quota(self) -> dict[str, Any]:
        raise NotImplementedError

    def normalize_error(self, error: BaseException) -> NormalizedProviderError:
        return normalize_error(error, provider_id=self.provider_id)

    def health_check(self) -> dict[str, Any]:
        raise NotImplementedError


class OpenAICompatibleAdapter(ProviderAdapter):
    """Small standard-library adapter for an explicitly configured endpoint."""

    def __init__(self, registry: Mapping[str, Any], provider_id: str, *, network_enabled: bool = False, timeout_seconds: float = 8.0):
        try:
            validate_provider_registry(registry)
            self.config = provider_config(registry, provider_id)
        except ProviderRegistryError as exc:
            raise ProviderAdapterError("PROVIDER_CONFIGURATION_INVALID") from exc
        self.provider_id = provider_id
        self.network_enabled = network_enabled
        self.timeout_seconds = max(0.1, min(float(timeout_seconds), 60.0))

    @property
    def _base_url(self) -> str:
        configured = self.config.get("base_url")
        from_env = os.environ.get(str(self.config.get("base_url_env", "")), "")
        value = str(configured or from_env).strip()
        if not value:
            raise ProviderAdapterError("ENDPOINT_NOT_CONFIGURED")
        if not value.startswith("https://") or any(char in value for char in "\r\n"):
            raise ProviderAdapterError("ENDPOINT_INVALID")
        return value.rstrip("/") + "/"

    def _api_key(self) -> str:
        names = [str(self.config.get("api_key_env", "")), *(self.config.get("legacy_api_key_envs") or [])]
        for name in names:
            value = os.environ.get(name, "")
            if value and all(ord(char) >= 32 for char in value):
                return value
        raise ProviderAdapterError("AUTH_NOT_CONFIGURED")

    def _ensure_network(self) -> None:
        if not self.network_enabled:
            raise ProviderAdapterError("NETWORK_DISABLED")

    def _request_json(self, path: str, *, method: str = "GET", payload: Mapping[str, Any] | None = None, include_auth: bool = True) -> AdapterResponse:
        self._ensure_network()
        url = urljoin(self._base_url, path.lstrip("/"))
        headers = {"Accept": "application/json", "User-Agent": "hf-site-agent-provider-adapter/1"}
        if include_auth:
            headers["Authorization"] = f"Bearer {self._api_key()}"
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=data, headers=headers, method=method)
        started = time.monotonic()
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read(1_000_000).decode("utf-8")
                parsed = json.loads(body)
                if not isinstance(parsed, Mapping):
                    raise ProviderAdapterError("MODEL_OUTPUT_INVALID")
                response_headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
                return AdapterResponse(parsed, response_headers, int((time.monotonic() - started) * 1000))
        except (ProviderAdapterError, HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            raise
        except OSError as exc:
            raise ProviderAdapterError("NETWORK_ERROR") from exc

    def list_models(self) -> list[dict[str, Any]]:
        response = self._request_json("/models")
        entries = response.payload.get("data")
        if not isinstance(entries, list):
            raise ProviderAdapterError("MODEL_OUTPUT_INVALID")
        return [dict(item) for item in entries[:500] if isinstance(item, Mapping)]

    def _chat(self, model_id: str, messages: Sequence[Mapping[str, Any]], *, tools: Sequence[Mapping[str, Any]] = (), **options: Any) -> AdapterResponse:
        if not isinstance(model_id, str) or not model_id.strip():
            raise ProviderAdapterError("MODEL_ID_REQUIRED")
        if not messages or len(messages) > 64:
            raise ProviderAdapterError("INPUT_INVALID")
        payload: dict[str, Any] = {
            "model": model_id.strip(),
            "messages": [dict(message) for message in messages],
            "max_tokens": int(options.get("max_tokens", 256)),
            "stream": False,
        }
        if tools:
            payload["tools"] = [dict(tool) for tool in tools[:32]]
            payload["tool_choice"] = options.get("tool_choice", "auto")
        if self.provider_id == "openrouter":
            # Worker calls must not be silently routed to another model or
            # provider.  Probe and generation use the same strict setting.
            payload["provider"] = {"allow_fallbacks": False}
        response = self._request_json("/chat/completions", method="POST", payload=payload)
        if not isinstance(response.payload.get("choices"), list):
            raise ProviderAdapterError("MODEL_OUTPUT_INVALID")
        return response

    @staticmethod
    def _quota_headers(headers: Mapping[str, str]) -> dict[str, str]:
        """Keep only provider quota headers; never persist arbitrary headers."""
        allowed_fragments = (
            "ratelimit-limit",
            "ratelimit-remaining",
            "ratelimit-reset",
            "retry-after",
        )
        return {
            str(key).lower(): str(value)[:120]
            for key, value in headers.items()
            if any(fragment in str(key).lower() for fragment in allowed_fragments)
        }

    @staticmethod
    def _normalized_generation(response: AdapterResponse, requested_model: str) -> dict[str, Any]:
        resolved_model = response.payload.get("model")
        choices = response.payload.get("choices") or []
        first = choices[0] if choices and isinstance(choices[0], Mapping) else {}
        message = first.get("message") if isinstance(first, Mapping) else {}
        return {
            "model": str(resolved_model or ""),
            "requested_model": requested_model,
            "text": str(message.get("content") or "")[:20_000] if isinstance(message, Mapping) else "",
            "tool_calls": list(message.get("tool_calls") or [])[:32] if isinstance(message, Mapping) else [],
            "usage": dict(response.payload.get("usage") or {}) if isinstance(response.payload.get("usage"), Mapping) else {},
            "latency_ms": response.latency_ms,
            "quota_headers": OpenAICompatibleAdapter._quota_headers(response.headers),
        }

    def generate(self, model_id: str, messages: Sequence[Mapping[str, Any]], **options: Any) -> dict[str, Any]:
        if (
            self.config.get("enabled") is not True
            or self.config.get("activation_approved") is not True
            or self.config.get("probe_status") != "PROBE_OK"
            or self.config.get("health_status") != "HEALTHY"
            or self.config.get("circuit_state") != "CLOSED"
        ):
            raise ProviderAdapterError("PROVIDER_NOT_ACTIVE")
        require_zero_cost = bool(options.pop("require_zero_cost", False))
        result = self._normalized_generation(self._chat(model_id, messages, **options), model_id)
        if self.provider_id == "openrouter" and result["model"] != model_id:
            raise ProviderAdapterError("MODEL_MISMATCH")
        if require_zero_cost:
            usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
            cost = str(usage.get("cost", "")).strip()
            if not cost:
                raise ProviderAdapterError("FREE_COST_UNVERIFIED")
            if cost not in {"0", "0.0", "0.00"}:
                raise ProviderAdapterError("FREE_COST_NONZERO")
        return result

    def tool_call(self, model_id: str, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]], **options: Any) -> dict[str, Any]:
        if not tools:
            raise ProviderAdapterError("TOOLS_REQUIRED")
        return self.generate(model_id, messages, tools=tools, **options)

    def probe(self, model_id: str) -> dict[str, Any]:
        try:
            response = self._chat(model_id, [{"role": "user", "content": "Return JSON: {\"ok\":true}"}], max_tokens=8)
        except Exception as exc:
            normalized = self.normalize_error(exc)
            return {
                "provider": self.provider_id,
                "requested_model": model_id,
                "status": normalized.error_class,
                "http_status": normalized.http_status,
                "retry_after_seconds": normalized.retry_after_seconds,
                "retryable": normalized.retryable,
                "latency_ms": None,
                "usage_cost": None,
                "response_model": None,
            }
        resolved = str(response.payload.get("model") or "")
        usage = response.payload.get("usage") if isinstance(response.payload.get("usage"), Mapping) else {}
        cost = usage.get("cost")
        status = "PROBE_OK"
        if resolved != model_id:
            status = "MODEL_MISMATCH"
        elif cost is None:
            status = "FREE_COST_UNVERIFIED"
        elif str(cost) not in {"0", "0.0", "0.00"}:
            status = "FREE_COST_NONZERO"
        return {
            "provider": self.provider_id,
            "requested_model": model_id,
            "response_model": resolved,
            "status": status,
            "http_status": 200,
            "retry_after_seconds": None,
            "retryable": False,
            "latency_ms": response.latency_ms,
            "usage_cost": cost,
            "quota_headers": self._quota_headers(response.headers),
        }

    def get_usage(self) -> dict[str, Any]:
        return {"provider": self.provider_id, "status": "UNAVAILABLE_UNTIL_PROVIDER_ENDPOINT_CONFIGURED"}

    def get_quota(self) -> dict[str, Any]:
        quota_path = self.config.get("quota_path")
        if not quota_path:
            return {"provider": self.provider_id, "status": "QUOTA_UNAVAILABLE"}
        response = self._request_json(str(quota_path))
        return {"provider": self.provider_id, "status": "QUOTA_REPORTED", "data": dict(response.payload)}

    def health_check(self) -> dict[str, Any]:
        try:
            response = self._request_json("/models")
        except Exception as exc:
            normalized = self.normalize_error(exc)
            return {"provider": self.provider_id, "status": normalized.error_class, "http_status": normalized.http_status}
        return {"provider": self.provider_id, "status": "HEALTHY", "http_status": 200, "latency_ms": response.latency_ms}


class GuardedProviderAdapter(ProviderAdapter):
    """Bind one adapter to a provider-scoped quota ledger.

    The ledger reservation happens before the underlying request.  A retry
    must use a new, explicitly budgeted request ID; a duplicate ID is blocked
    after completion or while the first request is still in progress.
    """

    def __init__(self, adapter: ProviderAdapter, ledger: ProviderQuotaLedger):
        if adapter.provider_id != ledger.provider_id:
            raise ValueError("adapter and quota ledger providers must match")
        self.adapter = adapter
        self.ledger = ledger
        self.provider_id = adapter.provider_id

    def list_models(self) -> list[dict[str, Any]]:
        return self.adapter.list_models()

    def probe(self, model_id: str) -> dict[str, Any]:
        return self.adapter.probe(model_id)

    def generate(self, model_id: str, messages: Sequence[Mapping[str, Any]], **options: Any) -> dict[str, Any]:
        request_id = str(options.pop("request_id", "")).strip()
        mission_id = str(options.pop("mission_id", "")).strip()
        agent_id = str(options.pop("agent_id", "")).strip()
        estimated_requests = int(options.pop("estimated_requests", 1))
        if not request_id or not mission_id or not agent_id:
            raise ProviderAdapterError("REQUEST_IDENTITY_REQUIRED")
        self.ledger.reserve(
            request_id=request_id,
            mission_id=mission_id,
            agent_id=agent_id,
            model=model_id,
            estimated_requests=estimated_requests,
        )
        try:
            result = self.adapter.generate(model_id, messages, **options)
        except Exception as exc:
            normalized = self.adapter.normalize_error(exc)
            self.ledger.record_result(
                request_id,
                success=False,
                http_status=normalized.http_status,
                error_class=normalized.error_class,
                retry_after_seconds=normalized.retry_after_seconds,
            )
            raise ProviderAdapterError(
                normalized.error_class,
                http_status=normalized.http_status,
                retry_after_seconds=normalized.retry_after_seconds,
                retryable=normalized.retryable,
            ) from None
        self.ledger.record_result(request_id, success=True, http_status=200)
        summary = self.ledger.summary()
        return {
            **dict(result),
            "provider": self.provider_id,
            "quota_state": summary["zone"],
            "quota_remaining_known": summary["daily_limit"] is not None,
            "paid_fallback": False,
        }

    def tool_call(self, model_id: str, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]], **options: Any) -> dict[str, Any]:
        if not tools:
            raise ProviderAdapterError("TOOLS_REQUIRED")
        return self.generate(model_id, messages, tools=tools, **options)

    def get_usage(self) -> dict[str, Any]:
        return self.adapter.get_usage()

    def get_quota(self) -> dict[str, Any]:
        return self.adapter.get_quota()

    def normalize_error(self, error: BaseException) -> NormalizedProviderError:
        return self.adapter.normalize_error(error)

    def health_check(self) -> dict[str, Any]:
        return self.adapter.health_check()


__all__ = [
    "AdapterResponse", "GuardedProviderAdapter", "NormalizedProviderError", "OpenAICompatibleAdapter", "ProviderAdapter",
    "ProviderAdapterError", "normalize_error",
]
