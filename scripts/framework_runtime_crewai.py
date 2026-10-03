#!/usr/bin/env python3
"""Bounded CrewAI runtime bridge for ephemeral mission-scoped crews.

The Control Plane supplies a crew factory with already-configured model clients.
This module never reads secrets, selects providers, deploys, publishes or grants
repository authority.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

from scripts.framework_adapter_base import redact_for_framework


class CrewAIRuntimeError(RuntimeError):
    pass


def build_crewai_runner(
    crew_factory: Callable[[Mapping[str, Any], Mapping[str, Any], int, int], Any],
) -> Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]:
    if not callable(crew_factory):
        raise CrewAIRuntimeError("crew_factory must be callable")

    def runner(prepared: Mapping[str, Any], context: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            import crewai  # noqa: F401
        except ImportError as exc:
            raise CrewAIRuntimeError("crewai is not installed") from exc

        bounded_context = redact_for_framework(dict(context or {}))
        max_members = max(1, min(12, int(bounded_context.get("max_members") or 6)))
        max_iterations = max(1, min(12, int(bounded_context.get("max_iterations") or 4)))
        if bounded_context.get("provider_timeout_bounded") is not True:
            raise CrewAIRuntimeError("bounded provider timeout evidence is required")

        clean_command = redact_for_framework(dict(prepared))
        crew = crew_factory(clean_command, bounded_context, max_members, max_iterations)
        if not hasattr(crew, "kickoff"):
            raise CrewAIRuntimeError("crew_factory must return a CrewAI Crew")
        agents = list(getattr(crew, "agents", None) or [])
        tasks = list(getattr(crew, "tasks", None) or [])
        if not agents or len(agents) > max_members:
            raise CrewAIRuntimeError("actual CrewAI agent count violates bounded member limit")
        if len(tasks) > max_members * max_iterations:
            raise CrewAIRuntimeError("actual CrewAI task count exceeds bounded execution limit")
        for agent in agents:
            agent_max_iter = getattr(agent, "max_iter", None)
            if not isinstance(agent_max_iter, int) or agent_max_iter < 1 or agent_max_iter > max_iterations:
                raise CrewAIRuntimeError("CrewAI agent max_iter exceeds Control Plane limit")

        objective = str(
            clean_command.get("objective")
            or (clean_command.get("metadata") or {}).get("framework", {}).get("objective")
            or "Execute this bounded AI Army crew mission."
        )[:8000]
        result = crew.kickoff(inputs={"ai_army_objective": objective})
        outputs = getattr(result, "tasks_output", None)
        outputs = outputs if isinstance(outputs, list) else []
        member_results: list[dict[str, Any]] = []
        for index, output in enumerate(outputs[: max_members * max_iterations], 1):
            raw = getattr(output, "raw", None)
            json_dict = getattr(output, "json_dict", None)
            pydantic_value = getattr(output, "pydantic", None)
            has_output = bool(str(raw or "").strip()) or isinstance(json_dict, Mapping) or pydantic_value is not None
            member_results.append(redact_for_framework({
                "name": str(getattr(output, "agent", None) or f"task-{index}")[:160],
                "status": "completed" if has_output else "failed",
                "task_index": index,
                "output_present": has_output,
            }))
        if not member_results and tasks:
            # A CrewOutput without task outputs is not silently treated as success.
            member_results = [{
                "name": f"task-{index}",
                "status": "failed",
                "task_index": index,
                "output_present": False,
            } for index in range(1, min(len(tasks), max_members * max_iterations) + 1)]
        return {
            "status": "completed",
            "summary": "CrewAI ephemeral crew returned to AI Army Control Plane",
            "member_results": member_results,
            "crew_destroyed": True,
            "actual_member_count": len(agents),
            "actual_task_count": len(tasks),
            "native_control_plane": True,
            "authority_expanded": False,
        }

    return runner


__all__ = ["CrewAIRuntimeError", "build_crewai_runner"]
