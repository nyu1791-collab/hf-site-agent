#!/usr/bin/env python3
"""Compatibility entrypoint adding bounded JSON recovery to the canonical runner."""
from __future__ import annotations

from scripts import deepseek_supervisor_research as canonical
from scripts.deepseek_json_guard import parse_visible_json


def main() -> int:
    canonical._visible_json = parse_visible_json
    return canonical.main()


if __name__ == "__main__":
    raise SystemExit(main())
