#!/usr/bin/env python3
"""Native adapter: wraps the existing AI Army runtime without replacing it."""
from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from scripts.framework_adapter_base import FrameworkAdapter, build_report_envelope, verify_free_route


class NativeFrameworkAdapter(FrameworkAdapter):
    adapter_id = "native"

    def probe(self) -> dict[str, Any]:
        base = super().probe()
        base.update({"installed": True, "version_pin_verified": True, "available": self.config.get("enabled") is True})
        return base

    def supports(self, task_profile: str, required_capabilities: Sequence[str] = ()) -> bool:
        return True

    def execute(self, command: Mapping[str, Any], *, route_evidence: Mapping[str, Any] | None = None, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        # Local deterministic native tasks may not need a model. If a model route is supplied, gate it strictly.
        route = None
        if route_evidence is not None:
            route = verify_free_route(route_evidence)
        prepared = self.prepare(command, route_evidence=route)
        started = time.monotonic()
        if not callable(self.runner):
            raw: Mapping[str, Any] = {"status": "completed", "summary": "native adapter prepared command", "result": {"prepared": True}}
        else:
            raw_value = self.runner(prepared, dict(context or {}))
            raw = raw_value if isinstance(raw_value, Mapping) else {"result": {"value": raw_value}}
        report = build_report_envelope(
            command,
            status=str(raw.get("status") or "completed"),
            summary=str(raw.get("summary") or "native runtime completed"),
            provider=str((route or {}).get("provider_binding") or raw.get("provider") or ""),
            model=str((route or {}).get("model_binding") or raw.get("model") or ""),
            result=raw.get("result") if isinstance(raw.get("result"), Mapping) else dict(raw),
            evidence=raw.get("evidence") if isinstance(raw.get("evidence"), (list, tuple)) else (),
            warnings=raw.get("warnings") if isinstance(raw.get("warnings"), (list, tuple)) else (),
            errors=raw.get("errors") if isinstance(raw.get("errors"), (list, tuple)) else (),
            duration_ms=int((time.monotonic() - started) * 1000),
            requests_used=int(raw.get("requests_used") or (1 if route else 0)),
            framework_id=self.adapter_id,
            framework_values={"control_plane_authoritative": True},
        )
        if not self.validate_result(report):
            raise ValueError("native result failed ReportEnvelope validation")
        return report
