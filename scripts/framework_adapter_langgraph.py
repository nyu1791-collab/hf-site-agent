#!/usr/bin/env python3
"""LangGraph execution adapter for bounded mission/subgraph delegation."""
from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from scripts.framework_adapter_base import BoundaryViolation, FrameworkAdapter, build_report_envelope, framework_metadata, verify_free_route
from scripts.langgraph_checkpoint_backend import backend_status


class LangGraphFrameworkAdapter(FrameworkAdapter):
    adapter_id = "langgraph"

    def supports(self, task_profile: str, required_capabilities: Sequence[str] = ()) -> bool:
        return super().supports(task_profile, required_capabilities)

    def prepare(self, command: Mapping[str, Any], *, route_evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
        fw = framework_metadata(command)
        if str(fw.get("delegation_scope") or "mission").lower() == "control_plane":
            raise BoundaryViolation("LangGraph may own only a mission/subgraph, never the AI Army control plane")
        checkpoint = backend_status(backend=str(fw.get("checkpoint_backend") or "sqlite"))
        prepared = super().prepare(command, route_evidence=route_evidence)
        prepared["metadata"]["framework"].update({
            "delegation_scope": str(fw.get("delegation_scope") or "mission"),
            "checkpoint_enabled": True,
            "checkpoint_backend": checkpoint,
            "checkpoint_contract": "langgraph-checkpoint-backend-v1",
            "resume_enabled": True,
            "human_approval_boundary_preserved": True,
            "control_plane_authoritative": True,
        })
        return prepared

    def execute(self, command: Mapping[str, Any], *, route_evidence: Mapping[str, Any] | None = None, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        route = verify_free_route(route_evidence)
        prepared = self.prepare(command, route_evidence=route)
        command_id = str(command.get("command_id") or "")
        checkpoint_backend = prepared["metadata"]["framework"]["checkpoint_backend"]
        # This small controller-owned phase checkpoint is only a bounded safety
        # marker. Actual LangGraph state persistence uses langgraph_checkpoint_backend.
        self.checkpoint(command_id, {"phase": "prepared", "command": prepared})
        started = time.monotonic()
        try:
            if not callable(self.runner):
                raise RuntimeError("LangGraph runner unavailable")
            raw_value = self.runner(prepared, dict(context or {}))
            raw = raw_value if isinstance(raw_value, Mapping) else {"result": {"value": raw_value}}
            status = str(raw.get("status") or "completed")
            summary = str(raw.get("summary") or "LangGraph subgraph completed")
            result = raw.get("result") if isinstance(raw.get("result"), Mapping) else dict(raw)
            if status.lower() in {"completed", "completed_with_warnings"}:
                self.checkpoint(command_id, {"phase": "completed", "result": result})
            errors = raw.get("errors") if isinstance(raw.get("errors"), (list, tuple)) else ()
        except Exception as exc:
            checkpoint = self.resume(command_id)
            status = "failed"
            summary = "LangGraph execution failed; bounded recovery evidence preserved"
            result = {
                "adapter_phase_checkpoint_available": checkpoint is not None,
                "langgraph_checkpoint_backend": checkpoint_backend,
                "failure_class": type(exc).__name__,
            }
            errors = (type(exc).__name__,)
        report = build_report_envelope(
            command,
            status=status,
            summary=summary,
            provider=str(route["provider_binding"]),
            model=str(route["model_binding"]),
            result=result,
            errors=errors,
            duration_ms=int((time.monotonic() - started) * 1000),
            requests_used=1,
            framework_id=self.adapter_id,
            framework_values={
                "adapter_phase_checkpoint_available": self.resume(command_id) is not None,
                "checkpoint_backend": checkpoint_backend,
                "bounded_recovery": True,
            },
        )
        return report
