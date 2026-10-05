#!/usr/bin/env python3
"""Deprecated compatibility entry point for the guarded provider probe.

The old smoke script contained a second endpoint/model policy.  Keep the
filename for existing callers, but route all behavior through
``probe_providers`` and require an explicit opt-in before any request.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

if __package__ in {None, ""}:  # pragma: no cover - script invocation path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.probe_providers import PROBE_MODEL_ENVS, run_probe
from scripts.provider_registry import load_provider_registry


def main() -> int:
    provider = os.environ.get("AI_PROVIDER", "").strip().lower()
    model = os.environ.get("AI_MODEL", "").strip()
    output = Path(os.environ.get("PROVIDER_SMOKE_OUTPUT", "artifacts/provider_probe.json"))
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output must stay inside the workspace")
    if provider not in PROBE_MODEL_ENVS or not model or model == "openrouter/free":
        report = {"status": "BLOCKED_INVALID_PROVIDER_OR_MODEL", "model_calls": 0, "retry_count": 0, "paid_fallback": False}
    elif os.environ.get("PROVIDER_SMOKE_ENABLE") != "1":
        report = {"status": "DRY_RUN_NO_REQUEST", "provider": provider, "model": model, "model_calls": 0, "retry_count": 0, "paid_fallback": False}
    else:
        os.environ[PROBE_MODEL_ENVS[provider]] = model
        if provider == "openrouter" and os.environ.get("OPENROUTER_API_KEY", "") == "":
            os.environ["OPENROUTER_API_KEY"] = os.environ.get("AI_API_KEY", "")
        report = run_probe(
            load_provider_registry(),
            [provider],
            network_enabled=True,
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "model_calls": report.get("model_calls", 0),
        "retry_count": report.get("retry_count", 0),
        "paid_fallback": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
