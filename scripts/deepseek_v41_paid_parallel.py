#!/usr/bin/env python3
"""DeepSeek V4.1 Flash compatibility entrypoint for the bounded paid parallel runner."""

from __future__ import annotations

import deepseek_paid_parallel as runner


# DeepSeek V4.1 Flash launched under the canonical API model ID `deepseek-flash`.
# Keep the shared bounded runner intact so the legacy V4 fixture remains useful,
# while this entrypoint adds V4.1-specific diagnostics without exposing provider
# response bodies or secret material.
runner.EXPECTED_MODEL = "deepseek-flash"
_original_normalize_error = runner._normalize_error


def _normalize_v41_error(exc: BaseException) -> tuple[str, int | None]:
    if isinstance(exc, ValueError):
        detail = str(exc).strip().lower()
        if "visible content missing" in detail:
            return "VISIBLE_CONTENT_MISSING", None
        if "choices missing" in detail:
            return "CHOICES_MISSING", None
        if "specialist output must be an object" in detail:
            return "INVALID_SPECIALIST_OBJECT", None
    return _original_normalize_error(exc)


runner._normalize_error = _normalize_v41_error


if __name__ == "__main__":
    raise SystemExit(runner.main())
