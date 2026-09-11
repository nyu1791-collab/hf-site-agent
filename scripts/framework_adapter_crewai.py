#!/usr/bin/env python3
"""CrewAI adapter for bounded, mission-scoped crews that are destroyed after execution."""
from __future__ import annotations

import time
from typing import Any, Mapping

from scripts.framework_adapter_base import FrameworkAdapter, build_report_envelope, framework_metadata, verify_free_route


class CrewAIFrameworkAdapter(FrameworkAdapter):
    adapter_id = "crewai"

    def execute(self, command: Mapping[str, Any], *, route_evidence: Mapping[str, Any] | None = None, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        route = verify_free_route(route_evidence)
        prepared = self.prepare(command, route_evidence=route)
        max_members = max(1, int(self.config.get("max_members") or 6))
        max_iterations = max(1, int(self.config.get("max_iterations") or 4))
        requested_members = framework_metadata(command).get("crew_members")
        requested_members = requested_members if isinstance(requested_members, list) else []
        if len(requested_members) > max_members:
            return build_report_envelope(
                command,
                status="blocked",
                summary="CrewAI crew exceeds bounded member limit",
                provider=str(route["provider_binding"]),
                model=str(route["model_binding"]),
                result={"requested_members": len(requested_members), "max_members": max_members, "crew_destroyed": True},
                errors=("CREW_MEMBER_LIMIT",),
                framework_id=self.adapter_id,
                framework_values={"ephemeral": True, "crew_destroyed": True},
            )
        started = time.monotonic()
        raw: Mapping[str, Any] = {}
        destroyed = False
        try:
            if not callable(self.runner):
                raise RuntimeError("CrewAI runner unavailable")
            runtime_context = {
                **dict(context or {}),
                "max_members": max_members,
                "max_iterations": max_iterations,
            }
            value = self.runner(prepared, runtime_context)
            raw = value if isinstance(value, Mapping) else {}
        except Exception as exc:
            raw = {"member_results": [], "fatal_error": type(exc).__name__}
        finally:
            destroyed = True
        members = raw.get("member_results") if isinstance(raw.get("member_results"), list) else []
        failures = [row for row in members if isinstance(row, Mapping) and str(row.get("status") or "").lower() in {"failed", "blocked"}]
        if raw.get("fatal_error"):
            status = "failed"
            summary = "CrewAI crew failed within bounded mission scope"
            errors = (str(raw["fatal_error"]),)
        elif failures:
            status = "completed_with_warnings"
            summary = "CrewAI crew completed with bounded partial member failure"
            errors = ()
        else:
            status = str(raw.get("status") or "completed")
            summary = str(raw.get("summary") or "CrewAI ephemeral crew completed")
            errors = ()
        return build_report_envelope(
            command,
            status=status,
            summary=summary,
            provider=str(route["provider_binding"]),
            model=str(route["model_binding"]),
            result={
                "member_results": members,
                "failed_member_count": len(failures),
                "crew_destroyed": destroyed,
                "actual_member_count": int(raw.get("actual_member_count") or 0),
                "actual_task_count": int(raw.get("actual_task_count") or 0),
            },
            warnings=((f"{len(failures)} crew member(s) failed",) if failures else ()),
            errors=errors,
            duration_ms=int((time.monotonic() - started) * 1000),
            requests_used=max(1, min(len(members) or 1, max_members * max_iterations)),
            framework_id=self.adapter_id,
            framework_values={
                "ephemeral": True,
                "crew_destroyed": destroyed,
                "max_members": max_members,
                "max_iterations": max_iterations,
            },
        )
