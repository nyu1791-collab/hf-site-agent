#!/usr/bin/env python3
"""Focused high-context Gemini adapter for the staging commander lane.

The generic adapter intentionally uses conservative 20k-character content
bounds. The focused AI-army lane has independently bounded 120k prompt and
144k response envelopes, so this adapter preserves that useful context while
keeping the native Gemini contract, exact-model checks, no fallback, and no
provider retries.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from scripts.provider_adapters import AdapterResponse, GeminiNativeAdapter, OpenAICompatibleAdapter, ProviderAdapterError

FOCUSED_GOOGLE_MAX_INPUT_CHARS = 120_000
FOCUSED_GOOGLE_MAX_RESPONSE_CHARS = 144_000


class FocusedGoogleNativeAdapter(GeminiNativeAdapter):
    """Gemini-native adapter with bounded large context for commander work."""

    # Explicit transport marker consumed by the bounded recovery layer. It
    # authorizes no call on its own; recovery also requires the verified
    # Google STAGING/free-route ExecutionPolicy. Keeping this marker on the
    # focused adapter prevents generic/provider-agnostic 429 replay behavior.
    focused_commander_transport = True

    @staticmethod
    def _parts(content: Any) -> list[dict[str, Any]]:
        if isinstance(content, str):
            return [{"text": content[:FOCUSED_GOOGLE_MAX_INPUT_CHARS]}]
        if isinstance(content, Mapping):
            parts = content.get("parts")
            if isinstance(parts, list):
                return FocusedGoogleNativeAdapter._bounded_parts(parts)
        if isinstance(content, list):
            return FocusedGoogleNativeAdapter._bounded_parts(content)
        return [{"text": str(content or "")[:FOCUSED_GOOGLE_MAX_INPUT_CHARS]}]

    @staticmethod
    def _bounded_parts(parts: Sequence[Any]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        remaining = FOCUSED_GOOGLE_MAX_INPUT_CHARS
        for item in parts[:32]:
            if not isinstance(item, Mapping) or remaining <= 0:
                continue
            part = dict(item)
            if isinstance(part.get("text"), str):
                text = part["text"][:remaining]
                part["text"] = text
                remaining -= len(text)
            output.append(part)
        return output

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
        generation_config: dict[str, Any] = {
            "maxOutputTokens": max(1, int(options.get("max_tokens", 256))),
        }
        # Gemini 3.x defaults are intentionally preserved unless a caller has
        # an explicit reason to override them. The focused wrapper normally
        # removes temperature entirely.
        if options.get("temperature") is not None:
            generation_config["temperature"] = float(options["temperature"])
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": generation_config,
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        response_format = options.get("response_format")
        if isinstance(response_format, Mapping):
            generation_config["responseMimeType"] = "application/json"
            schema = response_format.get("json_schema")
            if isinstance(schema, Mapping) and isinstance(schema.get("schema"), Mapping):
                generation_config["responseJsonSchema"] = dict(schema["schema"])
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
        text_chars = 0
        tool_calls: list[dict[str, Any]] = []
        for part in parts if isinstance(parts, list) else []:
            if not isinstance(part, Mapping):
                continue
            if part.get("text") is not None and text_chars < FOCUSED_GOOGLE_MAX_RESPONSE_CHARS:
                text = str(part["text"])
                room = FOCUSED_GOOGLE_MAX_RESPONSE_CHARS - text_chars
                text = text[:room]
                text_parts.append(text)
                text_chars += len(text)
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
        result = super().generate(model_id, messages, **options)
        resolved = str(result.get("model") or "").removeprefix("models/")
        if resolved and resolved != model_id:
            raise ProviderAdapterError("MODEL_MISMATCH")
        return result


__all__ = [
    "FocusedGoogleNativeAdapter",
    "FOCUSED_GOOGLE_MAX_INPUT_CHARS",
    "FOCUSED_GOOGLE_MAX_RESPONSE_CHARS",
]
