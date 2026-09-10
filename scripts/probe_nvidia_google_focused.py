#!/usr/bin/env python3
"""Focused NVIDIA + Google staging probe with a bounded NVIDIA cold-start window.

This wrapper does not weaken the existing free-evidence gate. It only gives the
already-approved single NVIDIA FREE_ENDPOINT probe more time to return from a
cold hosted endpoint. Google still goes through the normal zero-cost preflight
and is never called when its current project billing route is unverified.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover - script invocation path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.probe_providers import run_probe
from scripts.provider_adapters import create_provider_adapter
from scripts.provider_registry import load_provider_registry


NVIDIA_FOCUSED_TIMEOUT_SECONDS = 150.0


def _read_json(path_value: str) -> Mapping[str, Any]:
    path = Path(path_value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("evidence file must stay inside the workspace")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("evidence file must contain an object")
    return value


def run_focused_probe(
    evidence: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run exactly the existing NVIDIA+Google gates, extending NVIDIA timeout only."""
    env = os.environ if environ is None else environ
    registry = load_provider_registry()
    nvidia = create_provider_adapter(
        registry,
        "nvidia",
        network_enabled=True,
        timeout_seconds=60.0,
    )
    # The core adapter caps constructor values at 60 seconds for ordinary use.
    # This focused staging carrier deliberately overrides the instance only;
    # request count, model ID, route, token cap and paid-fallback rules remain
    # enforced by run_probe() and the adapter itself.
    nvidia.timeout_seconds = NVIDIA_FOCUSED_TIMEOUT_SECONDS
    return run_probe(
        registry,
        ("nvidia", "google"),
        network_enabled=True,
        adapters={"nvidia": nvidia},
        environ=env,
        free_evidence=evidence,
        explicit_approval=True,
        allow_limited_staging_probe=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-file", required=True)
    parser.add_argument("--output", default="artifacts/provider_probe.json")
    args = parser.parse_args()
    try:
        report = run_focused_probe(_read_json(args.evidence_file))
    except Exception:
        report = {
            "schema_version": "provider-probe-v1",
            "network_enabled": True,
            "model_calls": 0,
            "retry_count": 0,
            "paid_fallback": False,
            "registry_changed": False,
            "explicit_probe_approval": True,
            "limited_staging_probe_approval": True,
            "status": "FOCUSED_PROBE_INVALID",
            "providers": [],
        }
    output = Path(args.output)
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output must stay inside the workspace")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
