#!/usr/bin/env python3
"""Backward-compatible entry point for the dynamic OpenRouter worker probe.

The former fixed-model probe name is retained so existing Actions links do not
break.  Its implementation now discovers current catalog workers and never
contains a hard-coded model ID.
"""

from __future__ import annotations

import sys

try:
    from scripts.probe_free_workers import main
except ModuleNotFoundError:  # pragma: no cover
    from probe_free_workers import main


if __name__ == "__main__":
    sys.exit(main())
