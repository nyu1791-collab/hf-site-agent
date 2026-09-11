#!/usr/bin/env python3
"""GitHub Copilot adapter with repository write gates and immutable hard boundaries."""
from __future__ import annotations

import time
from typing import Any, Mapping

from scripts.framework_adapter_base import FrameworkAdapter, build_report_envelope, framework_metadata, verify_free_route

FORBIDDEN_ACTIONS = {"merge", "deploy", "publish", "secret", "secrets", "payment", "main_push", "push_main"}


class CopilotFrameworkAdapter(FrameworkAdapter):
    adapter_id = "copilot"

    def _boundary_failure(self, command: Mapping[str, Any]) -> str | None:
        fw = framework_metadata(command)
        action = str(fw.get("action") or "repository_read").lower()
        branch = str(fw.get("target_branch") or "")
        tool_scope = [str(v).lower() for v in command.get("tool_scope", ()) if str(v)]
        permissions = [str(v).lower() for v in command.get("permissions", ()) if str(v)]
        if action in FORBIDDEN_ACTIONS:
            return f"forbidden_action:{action}"
        if any(any(word in item for word in ("secret", "credential", "token", "payment")) for item in tool_scope + permissions):
            return "secret_or_payment_scope"
        if str(command.get("side_effect_level") or "read_only") == "mutation":
            forbidden = {str(x).lower() for x in self.config.get("forbidden_branches", ("main", "master"))}
            if not branch:
                return "staging_branch_required"
            if branch.lower() in forbidden:
                return "protected_branch_write"
            prefixes = tuple(str(x) for x in self.config.get("allowed_write_branch_prefixes", ()))
            if not prefixes or not branch.startswith(prefixes):
                return "outside_explicit_staging_scope"
        return None

    def execute(self, command: Mapping[str, Any], *, route_evidence: Mapping[str, Any] | None = None, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        route = verify_free_route(route_evidence)
        failure = self._boundary_failure(command)
        if failure:
            return build_report_envelope(
                command,
                status="blocked",
                summary="Copilot repository action blocked by AI Army boundary policy",
                provider=str(route["provider_binding"]),
                model=str(route["model_binding"]),
                result={"boundary_failure": failure},
                errors=(failure,),
                framework_id=self.adapter_id,
                framework_values={"repository_write_authorized": False},
            )
        prepared = self.prepare(command, route_evidence=route)
        started = time.monotonic()
        if not callable(self.runner):
            status, summary, result = "failed", "Copilot connector unavailable", {"connector_present": False}
        else:
            raw_value = self.runner(prepared, dict(context or {}))
            raw = raw_value if isinstance(raw_value, Mapping) else {"result": {"value": raw_value}}
            status = str(raw.get("status") or "completed")
            summary = str(raw.get("summary") or "Copilot repository executor completed within staging scope")
            result = raw.get("result") if isinstance(raw.get("result"), Mapping) else dict(raw)
        return build_report_envelope(
            command,
            status=status,
            summary=summary,
            provider=str(route["provider_binding"]),
            model=str(route["model_binding"]),
            result=result,
            duration_ms=int((time.monotonic() - started) * 1000),
            requests_used=1,
            framework_id=self.adapter_id,
            framework_values={"repository_read": True, "main_push": False, "merge": False, "deploy": False, "publish": False, "secret_operation": False},
        )
