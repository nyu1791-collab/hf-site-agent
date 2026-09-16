#!/usr/bin/env python3
"""Validate a redacted secure-account evidence artifact without printing it."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover - script invocation path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.secure_account_evidence import PROVIDER_ENDPOINTS, validate_redacted_bundle


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_report(report: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    secrets = [
        str(env.get(name, "") or "")
        for config in PROVIDER_ENDPOINTS.values()
        for name in (config.get("secret"), config.get("legacy_secret"))
        if name
    ]
    validate_redacted_bundle(report, secret_values=secrets)
    if _parse_time(report.get("expires_at")) <= datetime.now(timezone.utc):
        raise ValueError("EVIDENCE_STALE")
    providers = report.get("providers")
    if not isinstance(providers, Mapping):
        raise ValueError("PROVIDERS_REQUIRED")
    return {
        "schema_version": report.get("schema_version"),
        "provider_count": len(providers),
        "secret_values_in_bundle": report.get("secret_values_in_bundle"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    path = Path(args.input)
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit("input must stay inside the workspace")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        result = validate_report(payload)
    except Exception:
        print("SECURE_EVIDENCE_VALIDATION=FAIL")
        return 1
    print(json.dumps({"SECURE_EVIDENCE_VALIDATION": "PASS", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
