#!/usr/bin/env python3
"""DeepSeek V4.1 Flash compatibility entrypoint for the bounded paid parallel runner."""

from __future__ import annotations

import deepseek_paid_parallel as runner


# DeepSeek V4.1 Flash launched on 2026-09-10 under the canonical API model ID
# `deepseek-flash`. Keep the shared bounded runner unchanged so the legacy V4
# regression fixture remains valid while this entrypoint targets the new model.
runner.EXPECTED_MODEL = "deepseek-flash"


if __name__ == "__main__":
    raise SystemExit(runner.main())
