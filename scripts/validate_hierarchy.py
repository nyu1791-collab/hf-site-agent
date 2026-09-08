#!/usr/bin/env python3
"""Validate hierarchical command/report fixtures without making model calls."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:  # Works both from the repository root and from scripts/.
    from scripts.agent_runtime import AgentRegistry, CommandEnvelope, ReportEnvelope
except ModuleNotFoundError:  # pragma: no cover - CLI fallback
    from agent_runtime import AgentRegistry, CommandEnvelope, ReportEnvelope


def load(path: str) -> dict:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", required=True)
    parser.add_argument("--report")
    args = parser.parse_args()

    registry = AgentRegistry()
    command = CommandEnvelope.from_dict(load(args.command))
    command.validate(registry)
    print(f"command ok: {command.command_id} {command.parent_agent_id}->{command.child_agent_id}")

    if args.report:
        report = ReportEnvelope.from_dict(load(args.report))
        report.validate(command, registry)
        print(f"report ok: {report.command_id} status={report.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

