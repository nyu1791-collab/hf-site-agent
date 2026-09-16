#!/usr/bin/env python3
"""Resilient parser for advisory JSON emitted by the paid DeepSeek supervisor.

Strict JSON remains preferred. When the provider returns a valid JSON object with
non-JSON prefix/suffix or an accidental second object, select the most plausible
complete top-level advisory object and annotate the recovery. This module never
repairs malformed JSON by invention and never turns a non-object into success.
"""
from __future__ import annotations

import json
from typing import Any

_EXPECTED_KEYS = frozenset({
    "lane", "verdict", "evidence_assessment", "findings", "retain", "change",
    "remove_or_demote", "risks", "tests", "metrics", "confidence",
    "executive_summary", "highest_value_changes", "rejected_or_deferred",
    "contradictions", "experiment_plan", "final_recommendation",
})


def _content_from_response(obj: dict[str, Any]) -> str:
    choices = obj.get("choices") or []
    if not choices:
        raise RuntimeError("choices missing")
    content = (choices[0].get("message") or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("visible content missing")
    return content.strip()


def _strip_single_fence(text: str) -> str:
    value = text.strip()
    if not value.startswith("```"):
        return value
    first_newline = value.find("\n")
    if first_newline < 0:
        return value
    inner = value[first_newline + 1 :]
    if inner.rstrip().endswith("```"):
        inner = inner.rstrip()[:-3]
    return inner.strip()


def _candidate_score(value: dict[str, Any], start: int, end: int) -> tuple[int, int, int]:
    expected = len(_EXPECTED_KEYS.intersection(value.keys()))
    return (expected, len(value), -start + min(end - start, 1_000_000))


def parse_visible_json(obj: dict[str, Any]) -> dict[str, Any]:
    """Return one advisory object, using bounded salvage only after strict failure."""
    content = _content_from_response(obj)
    strict_error: json.JSONDecodeError | None = None
    try:
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise RuntimeError("visible result is not an object")
        return parsed
    except json.JSONDecodeError as exc:
        strict_error = exc

    text = _strip_single_fence(content)
    decoder = json.JSONDecoder()
    candidates: list[tuple[tuple[int, int, int], int, int, dict[str, Any]]] = []
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, consumed = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        end = start + consumed
        candidates.append((_candidate_score(value, start, end), start, end, value))

    if not candidates:
        assert strict_error is not None
        raise strict_error

    assert strict_error is not None
    _, start, end, parsed = max(candidates, key=lambda item: item[0])
    recovered = dict(parsed)
    recovered["_parse_recovery"] = {
        "mode": "BOUNDED_RAW_DECODE",
        "strict_error": str(strict_error)[:160],
        "ignored_prefix_chars": start,
        "ignored_suffix_chars": max(0, len(text) - end),
        "candidate_count": len(candidates),
    }
    return recovered


__all__ = ["parse_visible_json"]
