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
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

from .provider_registry import ProviderRegistryError, provider_config, validate_provider_registry
from .provider_controls import ProviderQuotaLedger, QuotaGuardError
from .execution_scope import ExecutionPolicy, ExecutionScopeError, authorize_execution


LIMITED_STAGING_MAX_OUTPUT_TOKENS = 8


MISSION_PROMPTS: dict[str, str] = {
    "decomposition": "Return a JSON object with a bounded list of independent steps for a read-only task.",
    "dependency_graph": "Return a JSON object containing bounded nodes and dependencies for a read-only task.",
    "constraint_planning": "Return a JSON object with a plan that explicitly preserves safety constraints.",
    "failure_recovery": "Return a JSON object describing a blocked result and a safe parent decision.",
    "child_command_generation": "Return a JSON object containing one bounded child command draft.",
    "result_aggregation": "Return a JSON object aggregating two bounded child reports.",
    "long_document_synthesis": "Return a JSON outline for synthesizing a long document without copying irrelevant text.",
    "research_planning": "Return a JSON research plan with sources, evidence checks, and bounded follow-up work.",
    "multimodal_plan": "Return a JSON plan for a read-only multimodal analysis with explicit missing-input handling.",
    "cross_source_synthesis": "Return a JSON synthesis plan that separates evidence, uncertainty, and conclusions.",
    "repository_diagnosis": "Return a JSON diagnosis plan for a repository bug using read-only inspection first.",
    "bug_localization": "Return a JSON bug-localization plan with bounded hypotheses and tests.",
    "implementation_plan": "Return a JSON implementation plan with small commits and rollback checkpoints.",
    "test_strategy": "Return a JSON test strategy covering unit, integration, regression, and failure injection.",
    "code_review": "Return a JSON code-review report with findings, evidence, and no direct mutation.",
    "technical_recovery": "Return a JSON recovery plan that preserves successful artifacts after a provider failure.",
    "bulk_classification": "Return a JSON classification result for a bounded batch with no external side effects.",
    "log_triage": "Return a JSON log triage result separating severity, evidence, and next safe action.",
    "fast_json_transform": "Return a JSON transformation result for a bounded input while preserving its schema.",
    "batch_summary": "Return a JSON summary of a bounded batch with counts and uncertainty markers.",
    "first_pass_code_review": "Return a JSON first-pass review limited to read-only observations and risk labels.",
}


def _parse_json_text(value: Any) -> Any:
    if not isinstance(value, str):
        raise ValueError("structured response missing")
    return json.loads(value)


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


def _authorize_generation(config: Mapping[str, Any], provider_id: str, model_id: str, options: dict[str, Any]) -> str:
    """Authorize one generation without conflating staging and production."""
    execution_policy = options.pop("execution_policy", None)
    legacy_default = execution_policy is None
    if execution_policy is None:
        # Existing callers retain the explicit production activation contract.
        # Staging callers must pass an ExecutionPolicy(scope="STAGING"); they
        # cannot inherit this production-shaped default.
        execution_policy = ExecutionPolicy(
            scope="PRODUCTION",
            provider_id=provider_id,
            model_id=model_id,
            production_approved=config.get("activation_approved") is True,
            production_active=config.get("enabled") is True,
            exact_model_verified=True,
            endpoint_verified=True,
            auth_verified=True,
            capability_verified=True,
            free_verified=config.get("free_mode") is True,
            cost_safe=config.get("free_mode") is True,
            quota_safe=config.get("health_status") == "HEALTHY",
            circuit_closed=config.get("circuit_state") == "CLOSED",
        )
    if not isinstance(execution_policy, ExecutionPolicy):
        raise ProviderAdapterError("EXECUTION_POLICY_REQUIRED")
    if execution_policy.provider_id != provider_id or execution_policy.model_id != model_id:
        raise ProviderAdapterError("EXECUTION_POLICY_MODEL_MISMATCH")
    if legacy_default and (
        config.get("enabled") is not True
        or config.get("activation_approved") is not True
    ):
        # Preserve the established adapter error contract for legacy callers.
        # Explicit STAGING callers never take this path.
        raise ProviderAdapterError("PROVIDER_NOT_ACTIVE")
    try:
        authorize_execution(config, execution_policy)
    except ExecutionScopeError as exc:
        if exc.reason == "PRODUCTION_REGISTRY_ACTIVATION_REQUIRED":
            raise ProviderAdapterError("PROVIDER_NOT_ACTIVE") from None
        raise ProviderAdapterError(exc.reason) from None
    return execution_policy.scope


class ProviderAdapter:
    """Provider-neutral contract used by commander and worker routes."""

    def discover_models(self) -> list[dict[str, Any]]:
        """Discover models through the provider's official configured API."""
        return self.list_models()

    def probe_auth(self) -> dict[str, Any]:
        """Perform the smallest authenticated read and return redacted evidence."""
        try:
            models = self.discover_models()
            # Keep the discovery result for the validation runner.  This
            # avoids a second authenticated catalog request while preserving
            # the provider-neutral interface.
            self._last_discovered_models = [dict(model) for model in models if isinstance(model, Mapping)]
        except Exception as exc:
            normalized = self.normalize_error(exc)
            return {
                "provider": self.provider_id,
                "status": normalized.error_class,
                "http_status": normalized.http_status,
                "retry_after_seconds": normalized.retry_after_seconds,
                "retryable": normalized.retryable,
                "model_count": 0,
            }
        return {"provider": self.provider_id, "status": "AUTH_OK", "model_count": len(models)}

    def probe_model(self, model_id: str) -> dict[str, Any]:
        """Probe one exact model after authentication/discovery."""
        return self.probe(model_id)

    def capability_probe(self, model_id: str, capability: str) -> dict[str, Any]:
        """Run one bounded, read-only capability check without activating a role."""
        raise NotImplementedError

    def mission_probe(self, model_id: str, mission_type: str) -> dict[str, Any]:
        """Run one bounded commander-contract task without side effects."""
        raise NotImplementedError

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

    def _request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: Mapping[str, Any] | None = None,
        include_auth: bool = True,
        auth_header: str | None = None,
    ) -> AdapterResponse:
        self._ensure_network()
        url = urljoin(self._base_url, path.lstrip("/"))
        headers = {"Accept": "application/json", "User-Agent": "hf-site-agent-provider-adapter/1"}
        if include_auth:
            header_name = str(auth_header or self.config.get("auth_header") or "Authorization")
            credential = self._api_key()
            headers[header_name] = f"Bearer {credential}" if header_name.lower() == "authorization" else credential
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
        response = self._request_json(str(self.config.get("models_path") or "/models"))
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
        if isinstance(options.get("response_format"), Mapping):
            payload["response_format"] = dict(options["response_format"])
        if self.provider_id == "openrouter":
            # Worker calls must not be silently routed to another model or
            # provider.  Probe and generation use the same strict setting.
            payload["provider"] = {"allow_fallbacks": False}
        response = self._request_json(str(self.config.get("generate_path") or "/chat/completions"), method="POST", payload=payload)
        self._validate_chat_payload(response.payload)
        return response

    @staticmethod
    def _validate_chat_payload(payload: Mapping[str, Any]) -> None:
        """Reject successful-looking but unusable OpenAI-compatible output."""
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderAdapterError("MODEL_OUTPUT_INVALID")
        first = choices[0] if isinstance(choices[0], Mapping) else {}
        message = first.get("message") if isinstance(first, Mapping) else None
        if not isinstance(message, Mapping):
            raise ProviderAdapterError("MODEL_OUTPUT_INVALID")
        content = message.get("content")
        tool_calls = message.get("tool_calls")
        has_content = (
            (isinstance(content, str) and bool(content.strip()))
            or (isinstance(content, list) and bool(content))
        )
        has_tool_calls = isinstance(tool_calls, list) and bool(tool_calls)
        if not has_content and not has_tool_calls:
            raise ProviderAdapterError("MODEL_OUTPUT_INVALID")

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
        execution_policy = options.get("execution_policy")
        execution_scope = _authorize_generation(self.config, self.provider_id, model_id, options)
        if (
            self.config.get("probe_status") != "PROBE_OK"
            or self.config.get("health_status") != "HEALTHY"
            or self.config.get("circuit_state") != "CLOSED"
        ) and execution_scope == "PRODUCTION":
            raise ProviderAdapterError("PROVIDER_NOT_ACTIVE")
        if (
            isinstance(execution_policy, ExecutionPolicy)
            and execution_policy.limited_staging is True
        ):
            # Enforce the NVIDIA one-shot bootstrap bound at the adapter too;
            # callers cannot accidentally widen it by omitting or overriding
            # ``max_tokens`` in the runtime callback.
            try:
                requested_max_tokens = int(options.get("max_tokens", LIMITED_STAGING_MAX_OUTPUT_TOKENS))
            except (TypeError, ValueError):
                requested_max_tokens = LIMITED_STAGING_MAX_OUTPUT_TOKENS
            options["max_tokens"] = min(max(1, requested_max_tokens), LIMITED_STAGING_MAX_OUTPUT_TOKENS)
        require_zero_cost = bool(options.pop("require_zero_cost", False))
        result = self._normalized_generation(self._chat(model_id, messages, **options), model_id)
        if self.provider_id == "openrouter" and result["model"] != model_id:
            raise ProviderAdapterError("MODEL_MISMATCH")
        if (
            self.provider_id == "nvidia"
            and isinstance(execution_policy, ExecutionPolicy)
            and execution_policy.limited_staging is True
            and result["model"]
            and result["model"] != model_id
        ):
            raise ProviderAdapterError("MODEL_MISMATCH")
        if require_zero_cost:
            usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
            cost = str(usage.get("cost", "")).strip()
            # Some providers do not return a per-response cost field.  In
            # that case the current, account-aware free-route evidence is the
            # authority; a missing field is not silently treated as free.
            evidence_allows_unreported_cost = (
                isinstance(execution_policy, ExecutionPolicy)
                and execution_policy.scope in {"PROBE", "STAGING"}
                and (
                    execution_policy.cost_safe is True
                    or (
                        execution_policy.limited_staging is True
                        and execution_policy.staging_free_route_allowed is True
                    )
                )
            )
            if not cost and not evidence_allows_unreported_cost:
                raise ProviderAdapterError("FREE_COST_UNVERIFIED")
            if cost and cost not in {"0", "0.0", "0.00"}:
                raise ProviderAdapterError("FREE_COST_NONZERO")
        return result

    def tool_call(self, model_id: str, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]], **options: Any) -> dict[str, Any]:
        if not tools:
            raise ProviderAdapterError("TOOLS_REQUIRED")
        return self.generate(model_id, messages, tools=tools, **options)

    def probe(self, model_id: str) -> dict[str, Any]:
        try:
            response = self._chat(model_id, [{"role": "user", "content": "Return JSON: {\"ok\":true}"}], max_tokens=8)
            self._validate_chat_payload(response.payload)
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
        if resolved and resolved != model_id:
            status = "MODEL_MISMATCH"
        elif self.config.get("free_access_type") == "FREE_MODEL_ENDPOINT" and cost is None:
            status = "FREE_COST_UNVERIFIED"
        elif self.config.get("free_access_type") == "FREE_MODEL_ENDPOINT" and str(cost) not in {"0", "0.0", "0.00"}:
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
            "usage_present": bool(usage),
            "usage_keys": sorted(str(key) for key in usage)[:24],
            "quota_headers": self._quota_headers(response.headers),
        }

    def capability_probe(self, model_id: str, capability: str) -> dict[str, Any]:
        try:
            if capability == "structured_output":
                response = self._chat(
                    model_id,
                    [{"role": "user", "content": "Return a JSON object with the boolean field ok set to true."}],
                    response_format={"type": "json_object"},
                    max_tokens=32,
                )
                choices = response.payload.get("choices") or []
                message = choices[0].get("message") if choices and isinstance(choices[0], Mapping) else {}
                content = message.get("content") if isinstance(message, Mapping) else None
                _parse_json_text(content)
            elif capability in {"tool_calling", "command_schema"}:
                command_schema = capability == "command_schema"
                response = self._chat(
                    model_id,
                    [{
                        "role": "user",
                        "content": (
                            "Return one JSON child-command draft with these keys: mission_id, command_id, "
                            "parent_agent_id, child_agent_id, owner_agent_id, role, objective, constraints, "
                            "expected_output, tool_scope, may_spawn_children, idempotency_key, side_effect_level."
                            if command_schema else "Call the provided function."
                        ),
                    }],
                    tools=() if command_schema else [{
                        "type": "function",
                        "function": {"name": "emit", "description": "Emit a validation marker", "parameters": {"type": "object", "properties": {}}},
                    }],
                    tool_choice=None if command_schema else {"type": "function", "function": {"name": "emit"}},
                    response_format={"type": "json_object"} if command_schema else None,
                    max_tokens=32,
                )
                choices = response.payload.get("choices") or []
                message = choices[0].get("message") if choices and isinstance(choices[0], Mapping) else {}
                if command_schema:
                    content = message.get("content") if isinstance(message, Mapping) else None
                    if not isinstance(content, str):
                        raise ValueError("command schema response missing")
                    parsed = _parse_json_text(content)
                    required = {
                        "mission_id", "command_id", "parent_agent_id", "child_agent_id",
                        "owner_agent_id", "role", "objective", "constraints", "expected_output",
                        "tool_scope", "may_spawn_children", "idempotency_key", "side_effect_level",
                    }
                    if not isinstance(parsed, Mapping) or not required <= set(parsed):
                        raise ValueError("command schema fields missing")
                else:
                    calls = message.get("tool_calls") if isinstance(message, Mapping) else []
                    if not isinstance(calls, list) or not calls:
                        raise ValueError("tool call missing")
            else:
                return {"status": "CAPABILITY_NOT_SUPPORTED", "capability": capability, "model": model_id}
        except Exception as exc:
            normalized = self.normalize_error(exc)
            return {"status": normalized.error_class, "capability": capability, "model": model_id, "http_status": normalized.http_status}
        return {
            "status": "CAPABILITY_OK",
            "capability": capability,
            "model": model_id,
            "latency_ms": response.latency_ms,
            "quota_headers": self._quota_headers(response.headers),
        }

    def mission_probe(self, model_id: str, mission_type: str) -> dict[str, Any]:
        prompt = MISSION_PROMPTS.get(mission_type)
        if prompt is None:
            return {"status": "MISSION_TYPE_INVALID", "mission_type": mission_type, "model": model_id}
        try:
            response = self._chat(model_id, [{"role": "user", "content": prompt}], response_format={"type": "json_object"}, max_tokens=96)
            choices = response.payload.get("choices") or []
            message = choices[0].get("message") if choices and isinstance(choices[0], Mapping) else {}
            content = message.get("content") if isinstance(message, Mapping) else None
            _parse_json_text(content)
        except Exception as exc:
            normalized = self.normalize_error(exc)
            return {"status": normalized.error_class, "mission_type": mission_type, "model": model_id, "http_status": normalized.http_status}
        return {"status": "MISSION_OK", "mission_type": mission_type, "model": model_id, "latency_ms": response.latency_ms}

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
            response = self._request_json(str(self.config.get("models_path") or "/models"))
        except Exception as exc:
            normalized = self.normalize_error(exc)
            return {"provider": self.provider_id, "status": normalized.error_class, "http_status": normalized.http_status}
        return {"provider": self.provider_id, "status": "HEALTHY", "http_status": 200, "latency_ms": response.latency_ms}


class GeminiNativeAdapter(OpenAICompatibleAdapter):
    """Adapter for Google's native Gemini REST contract.

    Gemini's API is not OpenAI-compatible: it uses ``contents`` and a
    ``models/*:generateContent`` path, and authenticates with the configured
    API-key header.  The adapter remains inert until ``network_enabled`` is
    explicitly set, and never puts the key in a URL or report.
    """

    @staticmethod
    def _parts(content: Any) -> list[dict[str, Any]]:
        if isinstance(content, str):
            return [{"text": content[:20_000]}]
        if isinstance(content, Mapping):
            parts = content.get("parts")
            if isinstance(parts, list):
                return [dict(part) for part in parts[:32] if isinstance(part, Mapping)]
        if isinstance(content, list):
            return [dict(part) for part in content[:32] if isinstance(part, Mapping)]
        return [{"text": str(content or "")[:20_000]}]

    @classmethod
    def _contents(cls, messages: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        contents: list[dict[str, Any]] = []
        system_parts: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "user").lower()
            parts = cls._parts(message.get("content", ""))
            if role in {"system", "developer"}:
                system_parts.extend(parts)
                continue
            contents.append({"role": "model" if role in {"assistant", "model"} else "user", "parts": parts})
        system = {"parts": system_parts} if system_parts else None
        return contents, system

    @staticmethod
    def _native_tools(tools: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        declarations: list[dict[str, Any]] = []
        for tool in tools[:32]:
            if not isinstance(tool, Mapping):
                continue
            function = tool.get("function") if isinstance(tool.get("function"), Mapping) else tool
            if isinstance(function, Mapping) and function.get("name"):
                declaration = {key: function[key] for key in ("name", "description", "parameters") if key in function}
                if "parameters" in declaration:
                    declaration["parametersJsonSchema"] = declaration.pop("parameters")
                declarations.append(declaration)
        return [{"functionDeclarations": declarations}] if declarations else []

    def list_models(self) -> list[dict[str, Any]]:
        response = self._request_json(str(self.config.get("models_path") or "/models"))
        entries = response.payload.get("models")
        if not isinstance(entries, list):
            raise ProviderAdapterError("MODEL_OUTPUT_INVALID")
        normalized: list[dict[str, Any]] = []
        for item in entries[:500]:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            model_id = name.removeprefix("models/") if name else ""
            if model_id:
                normalized.append({**dict(item), "id": model_id, "provider": "google"})
        return normalized

    def _gemini_chat(
        self,
        model_id: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] = (),
        **options: Any,
    ) -> AdapterResponse:
        if not isinstance(model_id, str) or not model_id.strip():
            raise ProviderAdapterError("MODEL_ID_REQUIRED")
        if not messages or len(messages) > 64:
            raise ProviderAdapterError("INPUT_INVALID")
        contents, system_instruction = self._contents(messages)
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": int(options.get("max_tokens", 256)),
                "temperature": float(options.get("temperature", 0)),
            },
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        response_format = options.get("response_format")
        if isinstance(response_format, Mapping):
            payload["generationConfig"]["responseMimeType"] = "application/json"
            schema = response_format.get("json_schema")
            if isinstance(schema, Mapping) and isinstance(schema.get("schema"), Mapping):
                payload["generationConfig"]["responseJsonSchema"] = dict(schema["schema"])
        native_tools = self._native_tools(tools)
        if native_tools:
            payload["tools"] = native_tools
        path = str(self.config.get("generate_path") or "/models/{model}:generateContent")
        path = path.replace("{model}", quote(model_id.strip(), safe="-_.~"))
        return self._request_json(path, method="POST", payload=payload)

    @staticmethod
    def _normalized_native(response: AdapterResponse, requested_model: str) -> dict[str, Any]:
        candidates = response.payload.get("candidates") or []
        first = candidates[0] if candidates and isinstance(candidates[0], Mapping) else {}
        content = first.get("content") if isinstance(first, Mapping) else {}
        parts = content.get("parts") if isinstance(content, Mapping) else []
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for part in parts if isinstance(parts, list) else []:
            if not isinstance(part, Mapping):
                continue
            if part.get("text") is not None:
                text_parts.append(str(part["text"])[:20_000])
            function_call = part.get("functionCall")
            if isinstance(function_call, Mapping) and function_call.get("name"):
                tool_calls.append({
                    "type": "function",
                    "function": {
                        "name": str(function_call["name"]),
                        "arguments": json.dumps(function_call.get("args") or {}, ensure_ascii=False, separators=(",", ":")),
                    },
                })
        return {
            "model": str(response.payload.get("modelVersion") or ""),
            "requested_model": requested_model,
            "text": "".join(text_parts),
            "tool_calls": tool_calls[:32],
            "usage": dict(response.payload.get("usageMetadata") or {}) if isinstance(response.payload.get("usageMetadata"), Mapping) else {},
            "latency_ms": response.latency_ms,
            "quota_headers": OpenAICompatibleAdapter._quota_headers(response.headers),
        }

    def generate(self, model_id: str, messages: Sequence[Mapping[str, Any]], **options: Any) -> dict[str, Any]:
        execution_policy = options.get("execution_policy")
        execution_scope = _authorize_generation(self.config, self.provider_id, model_id, options)
        if (
            self.config.get("probe_status") != "PROBE_OK"
            or self.config.get("health_status") != "HEALTHY"
            or self.config.get("circuit_state") != "CLOSED"
        ) and execution_scope == "PRODUCTION":
            raise ProviderAdapterError("PROVIDER_NOT_ACTIVE")
        require_zero_cost = bool(options.pop("require_zero_cost", False))
        result = self._normalized_native(self._gemini_chat(model_id, messages, **options), model_id)
        if require_zero_cost:
            usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
            cost = str(usage.get("cost", "")).strip()
            evidence_allows_unreported_cost = (
                isinstance(execution_policy, ExecutionPolicy)
                and execution_policy.scope in {"PROBE", "STAGING"}
                and execution_policy.cost_safe is True
            )
            if not cost and not evidence_allows_unreported_cost:
                raise ProviderAdapterError("FREE_COST_UNVERIFIED")
            if cost and cost not in {"0", "0.0", "0.00"}:
                raise ProviderAdapterError("FREE_COST_NONZERO")
        return result

    def tool_call(self, model_id: str, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]], **options: Any) -> dict[str, Any]:
        if not tools:
            raise ProviderAdapterError("TOOLS_REQUIRED")
        return self.generate(model_id, messages, tools=tools, **options)

    def probe(self, model_id: str) -> dict[str, Any]:
        try:
            response = self._gemini_chat(model_id, [{"role": "user", "content": "Return exactly OK."}], max_tokens=4)
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
        normalized = self._normalized_native(response, model_id)
        return {
            "provider": self.provider_id,
            "requested_model": model_id,
            "response_model": normalized["model"] or None,
            "status": "PROBE_OK" if response.payload.get("candidates") else "MODEL_OUTPUT_INVALID",
            "http_status": 200,
            "retry_after_seconds": None,
            "retryable": False,
            "latency_ms": response.latency_ms,
            "usage_cost": None,
            "usage_present": bool(normalized["usage"]),
            "usage_keys": sorted(str(key) for key in normalized["usage"])[:24],
            "quota_headers": normalized["quota_headers"],
        }

    def capability_probe(self, model_id: str, capability: str) -> dict[str, Any]:
        try:
            if capability == "structured_output":
                response = self._gemini_chat(
                    model_id,
                    [{"role": "user", "content": "Return a JSON object with the boolean field ok set to true."}],
                    response_format={"type": "json_object"},
                    max_tokens=32,
                )
                normalized = self._normalized_native(response, model_id)
                _parse_json_text(normalized["text"])
            elif capability in {"tool_calling", "command_schema"}:
                command_schema = capability == "command_schema"
                response = self._gemini_chat(
                    model_id,
                    [{"role": "user", "content": (
                        "Return one JSON child-command draft with these keys: mission_id, command_id, "
                        "parent_agent_id, child_agent_id, owner_agent_id, role, objective, constraints, "
                        "expected_output, tool_scope, may_spawn_children, idempotency_key, side_effect_level."
                        if command_schema else "Call the provided function."
                    )}],
                    tools=() if command_schema else [{
                        "type": "function",
                        "function": {"name": "emit", "description": "Emit a validation marker", "parameters": {"type": "object", "properties": {}}},
                    }],
                    response_format={"type": "json_object"} if command_schema else None,
                    max_tokens=32,
                )
                normalized = self._normalized_native(response, model_id)
                if command_schema:
                    parsed = _parse_json_text(normalized["text"]) if normalized["text"] else {}
                    required = {
                        "mission_id", "command_id", "parent_agent_id", "child_agent_id",
                        "owner_agent_id", "role", "objective", "constraints", "expected_output",
                        "tool_scope", "may_spawn_children", "idempotency_key", "side_effect_level",
                    }
                    if not isinstance(parsed, Mapping) or not required <= set(parsed):
                        raise ValueError("command schema fields missing")
                elif not normalized["tool_calls"]:
                    raise ValueError("tool call missing")
            else:
                return {"status": "CAPABILITY_NOT_SUPPORTED", "capability": capability, "model": model_id}
        except Exception as exc:
            normalized_error = self.normalize_error(exc)
            return {"status": normalized_error.error_class, "capability": capability, "model": model_id, "http_status": normalized_error.http_status}
        return {
            "status": "CAPABILITY_OK",
            "capability": capability,
            "model": model_id,
            "latency_ms": response.latency_ms,
            "quota_headers": OpenAICompatibleAdapter._quota_headers(response.headers),
        }

    def mission_probe(self, model_id: str, mission_type: str) -> dict[str, Any]:
        prompt = MISSION_PROMPTS.get(mission_type)
        if prompt is None:
            return {"status": "MISSION_TYPE_INVALID", "mission_type": mission_type, "model": model_id}
        try:
            response = self._gemini_chat(model_id, [{"role": "user", "content": prompt}], response_format={"type": "json_object"}, max_tokens=96)
            normalized = self._normalized_native(response, model_id)
            _parse_json_text(normalized["text"])
        except Exception as exc:
            normalized_error = self.normalize_error(exc)
            return {"status": normalized_error.error_class, "mission_type": mission_type, "model": model_id, "http_status": normalized_error.http_status}
        return {"status": "MISSION_OK", "mission_type": mission_type, "model": model_id, "latency_ms": response.latency_ms}

    def health_check(self) -> dict[str, Any]:
        try:
            models = self.list_models()
        except Exception as exc:
            normalized = self.normalize_error(exc)
            return {"provider": self.provider_id, "status": normalized.error_class, "http_status": normalized.http_status}
        return {"provider": self.provider_id, "status": "HEALTHY", "http_status": 200, "model_count": len(models)}


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
        self.config = getattr(adapter, "config", {})

    def list_models(self) -> list[dict[str, Any]]:
        return self.adapter.list_models()

    def discover_models(self) -> list[dict[str, Any]]:
        return self.adapter.discover_models()

    def probe_auth(self) -> dict[str, Any]:
        result = self.adapter.probe_auth()
        cached = getattr(self.adapter, "_last_discovered_models", None)
        if isinstance(cached, list):
            self._last_discovered_models = [dict(model) for model in cached if isinstance(model, Mapping)]
        return result

    def probe(self, model_id: str) -> dict[str, Any]:
        return self.adapter.probe(model_id)

    def probe_model(self, model_id: str) -> dict[str, Any]:
        return self.adapter.probe_model(model_id)

    def capability_probe(self, model_id: str, capability: str) -> dict[str, Any]:
        return self.adapter.capability_probe(model_id, capability)

    def mission_probe(self, model_id: str, mission_type: str) -> dict[str, Any]:
        return self.adapter.mission_probe(model_id, mission_type)

    def generate(self, model_id: str, messages: Sequence[Mapping[str, Any]], **options: Any) -> dict[str, Any]:
        request_id = str(options.pop("request_id", "")).strip()
        mission_id = str(options.pop("mission_id", "")).strip()
        agent_id = str(options.pop("agent_id", "")).strip()
        estimated_requests = int(options.pop("estimated_requests", 1))
        if not request_id or not mission_id or not agent_id:
            raise ProviderAdapterError("REQUEST_IDENTITY_REQUIRED")
        execution_policy = options.get("execution_policy")
        if isinstance(self.config, Mapping) and execution_policy is not None:
            if not isinstance(execution_policy, ExecutionPolicy):
                raise ProviderAdapterError("EXECUTION_POLICY_REQUIRED")
            if execution_policy.provider_id != self.provider_id or execution_policy.model_id != model_id:
                raise ProviderAdapterError("EXECUTION_POLICY_MODEL_MISMATCH")
            try:
                authorize_execution(self.config, execution_policy)
            except ExecutionScopeError as exc:
                raise ProviderAdapterError(exc.reason) from None
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


def create_provider_adapter(
    registry: Mapping[str, Any],
    provider_id: str,
    *,
    network_enabled: bool = False,
    timeout_seconds: float = 8.0,
) -> ProviderAdapter:
    """Create the provider-specific adapter without embedding provider logic in Runtime."""
    config = provider_config(registry, provider_id)
    adapter_class = GeminiNativeAdapter if config.get("api_style") == "gemini_native" else OpenAICompatibleAdapter
    return adapter_class(registry, provider_id, network_enabled=network_enabled, timeout_seconds=timeout_seconds)


__all__ = [
    "AdapterResponse", "GeminiNativeAdapter", "GuardedProviderAdapter", "NormalizedProviderError",
    "MISSION_PROMPTS", "OpenAICompatibleAdapter", "ProviderAdapter", "ProviderAdapterError",
    "create_provider_adapter", "normalize_error",
]
