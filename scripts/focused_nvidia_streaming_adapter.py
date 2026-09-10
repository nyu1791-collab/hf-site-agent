#!/usr/bin/env python3
"""Focused streaming transport for NVIDIA's hosted FREE_ENDPOINT.

The normal provider adapter intentionally stays conservative and non-streaming.
GitHub-hosted runners have repeatedly timed out waiting for a complete NVIDIA
response, so this staging-only adapter asks the same exact model/endpoint for
SSE streaming and normalizes the completed stream back into the existing
OpenAI-compatible AdapterResponse contract.

It does not select another model, endpoint, paid route, retry an inference, or
persist provider response bodies. A documented NVIDIA 202 response is polled
only through /v1/status/{requestId}, within the same bounded invocation.
"""

from __future__ import annotations

import json
import time
from typing import Any, Mapping, Sequence
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

from scripts.provider_adapters import (
    AdapterResponse,
    OpenAICompatibleAdapter,
    ProviderAdapterError,
)


FOCUSED_NVIDIA_TIMEOUT_SECONDS = 150.0
MAX_STREAM_BYTES = 1_000_000
MAX_STATUS_POLLS = 12
STATUS_POLL_SECONDS = 3.0


class FocusedNvidiaStreamingAdapter(OpenAICompatibleAdapter):
    """NVIDIA-only SSE transport that preserves the core adapter contract."""

    def __init__(self, registry: Mapping[str, Any], *, network_enabled: bool = False):
        super().__init__(
            registry,
            "nvidia",
            network_enabled=network_enabled,
            timeout_seconds=60.0,
        )
        # Deliberately instance-scoped. The ordinary adapter keeps its 60 s cap.
        self.timeout_seconds = FOCUSED_NVIDIA_TIMEOUT_SECONDS

    def _request_status_result(self, request_id: str, *, deadline: float) -> AdapterResponse:
        if not request_id or len(request_id) > 64:
            raise ProviderAdapterError("MODEL_OUTPUT_INVALID")
        status_path = f"status/{quote(request_id, safe='-_.~')}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key()}",
            "User-Agent": "hf-site-agent-focused-nvidia/1",
        }
        for _ in range(MAX_STATUS_POLLS):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("bounded NVIDIA status polling expired")
            response_started = time.monotonic()
            request = Request(urljoin(self._base_url, status_path), headers=headers, method="GET")
            with urlopen(request, timeout=min(self.timeout_seconds, max(1.0, remaining))) as response:  # nosec B310 - validated HTTPS base URL
                status = int(response.getcode() or 0)
                body = response.read(MAX_STREAM_BYTES + 1)
                if len(body) > MAX_STREAM_BYTES:
                    raise ProviderAdapterError("MODEL_OUTPUT_INVALID")
                payload = json.loads(body.decode("utf-8")) if body else {}
                response_headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
                if status == 200 and isinstance(payload, Mapping):
                    return AdapterResponse(payload, response_headers, int((time.monotonic() - response_started) * 1000))
                if status != 202:
                    raise ProviderAdapterError("PROVIDER_HTTP_ERROR", http_status=status)
            time.sleep(min(STATUS_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
        raise TimeoutError("bounded NVIDIA status polling exhausted")

    @staticmethod
    def _request_id(payload: Any) -> str:
        if not isinstance(payload, Mapping):
            return ""
        for key in ("requestId", "request_id", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _delta_text(delta: Mapping[str, Any]) -> str:
        for key in ("content", "reasoning_content", "reasoning"):
            value = delta.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    def _chat(
        self,
        model_id: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] = (),
        **options: Any,
    ) -> AdapterResponse:
        self._ensure_network()
        if not isinstance(model_id, str) or not model_id.strip():
            raise ProviderAdapterError("MODEL_ID_REQUIRED")
        if not messages or len(messages) > 64:
            raise ProviderAdapterError("INPUT_INVALID")

        max_tokens = int(options.get("max_tokens", 256))
        payload: dict[str, Any] = {
            "model": model_id.strip(),
            "messages": [dict(message) for message in messages],
            "max_tokens": max(1, min(max_tokens, 256)),
            "stream": True,
        }
        reasoning_effort = options.get("reasoning_effort")
        if reasoning_effort in {"none", "high", "max"}:
            payload["reasoning_effort"] = reasoning_effort
        if isinstance(options.get("chat_template_kwargs"), Mapping):
            payload["chat_template_kwargs"] = dict(options["chat_template_kwargs"])
        if tools:
            payload["tools"] = [dict(tool) for tool in tools[:32]]
            payload["tool_choice"] = options.get("tool_choice", "auto")
        if isinstance(options.get("response_format"), Mapping):
            payload["response_format"] = dict(options["response_format"])

        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
            "User-Agent": "hf-site-agent-focused-nvidia/1",
        }
        request = Request(
            urljoin(self._base_url, "chat/completions"),
            data=body,
            headers=headers,
            method="POST",
        )
        started = time.monotonic()
        deadline = started + self.timeout_seconds
        with urlopen(request, timeout=self.timeout_seconds) as response:  # nosec B310 - validated HTTPS base URL
            status = int(response.getcode() or 0)
            response_headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
            if status == 202:
                raw = response.read(MAX_STREAM_BYTES + 1)
                if len(raw) > MAX_STREAM_BYTES:
                    raise ProviderAdapterError("MODEL_OUTPUT_INVALID")
                pending = json.loads(raw.decode("utf-8")) if raw else {}
                result = self._request_status_result(self._request_id(pending), deadline=deadline)
                return AdapterResponse(result.payload, result.headers, int((time.monotonic() - started) * 1000))
            if status != 200:
                raise ProviderAdapterError("PROVIDER_HTTP_ERROR", http_status=status)

            chunks: list[str] = []
            resolved_model = ""
            usage: dict[str, Any] = {}
            consumed = 0
            for raw_line in response:
                consumed += len(raw_line)
                if consumed > MAX_STREAM_BYTES:
                    raise ProviderAdapterError("MODEL_OUTPUT_INVALID")
                line = raw_line.decode("utf-8", errors="strict").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                event = json.loads(data)
                if not isinstance(event, Mapping):
                    continue
                if isinstance(event.get("model"), str) and event.get("model"):
                    resolved_model = str(event["model"])
                if isinstance(event.get("usage"), Mapping):
                    usage = dict(event["usage"])
                choices = event.get("choices")
                if not isinstance(choices, list):
                    continue
                for choice in choices[:4]:
                    if not isinstance(choice, Mapping):
                        continue
                    delta = choice.get("delta")
                    if isinstance(delta, Mapping):
                        text = self._delta_text(delta)
                        if text:
                            chunks.append(text)
                    message = choice.get("message")
                    if isinstance(message, Mapping):
                        value = message.get("content")
                        if isinstance(value, str) and value:
                            chunks.append(value)

        text = "".join(chunks)
        if not text.strip():
            raise ProviderAdapterError("MODEL_OUTPUT_INVALID")
        normalized = {
            "model": resolved_model or model_id,
            "choices": [{"message": {"role": "assistant", "content": text[:20_000]}}],
            "usage": usage,
        }
        return AdapterResponse(normalized, response_headers, int((time.monotonic() - started) * 1000))


__all__ = ["FocusedNvidiaStreamingAdapter", "FOCUSED_NVIDIA_TIMEOUT_SECONDS"]
