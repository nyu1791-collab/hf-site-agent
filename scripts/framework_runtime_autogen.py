#!/usr/bin/env python3
"""Bounded AutoGen AgentChat runtime bridge.

The Control Plane supplies the team factory/model clients. This module never
reads credentials, chooses providers, grants authority, or makes final decisions.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from typing import Any, Callable, Mapping

from scripts.framework_adapter_base import redact_for_framework

REQUIRED_POSITION_FIELDS = {"claim", "evidence", "confidence", "objection"}


class AutoGenRuntimeError(RuntimeError):
    pass


def _run_async(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="autogen-bounded") as pool:
        return pool.submit(asyncio.run, coro).result()


def _position_from_content(content: Any, source: str) -> dict[str, Any]:
    value: Any = content
    if isinstance(content, str):
        text = content.strip()
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1]).strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return {"source": source, "malformed": True}
    if not isinstance(value, Mapping) or not REQUIRED_POSITION_FIELDS.issubset(value.keys()):
        return {"source": source, "malformed": True}
    confidence = value.get("confidence")
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = -1.0
    if not 0.0 <= confidence_value <= 1.0:
        return {"source": source, "malformed": True}
    evidence = value.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return {"source": source, "malformed": True}
    return redact_for_framework({
        "source": source,
        "claim": str(value.get("claim") or "")[:4000],
        "evidence": [str(item)[:2000] for item in evidence[:16]],
        "confidence": confidence_value,
        "objection": str(value.get("objection") or "")[:4000],
    })


def build_autogen_runner(
    team_factory: Callable[[Mapping[str, Any], Mapping[str, Any], int], Any],
    *,
    timeout_seconds: int = 120,
) -> Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]:
    """Build an adapter-compatible AutoGen runner.

    The factory must return an AutoGen Team whose own termination/max-turn
    settings are bounded. Adapter max_rounds is passed to the factory.
    """
    if not callable(team_factory):
        raise AutoGenRuntimeError("team_factory must be callable")
    timeout = max(1, min(300, int(timeout_seconds)))

    def runner(prepared: Mapping[str, Any], context: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            import autogen_agentchat  # noqa: F401
        except ImportError as exc:
            raise AutoGenRuntimeError("autogen-agentchat is not installed") from exc

        bounded_context = redact_for_framework(dict(context or {}))
        max_rounds = max(1, min(12, int(bounded_context.get("max_rounds") or 6)))
        team = team_factory(redact_for_framework(dict(prepared)), bounded_context, max_rounds)
        if not hasattr(team, "run"):
            raise AutoGenRuntimeError("team_factory must return an AutoGen Team")
        objective = str(
            prepared.get("objective")
            or (prepared.get("metadata") or {}).get("framework", {}).get("objective")
            or "Return independent positions for this bounded AI Army council."
        )[:8000]

        async def execute() -> Any:
            try:
                return await asyncio.wait_for(team.run(task=objective), timeout=timeout)
            finally:
                reset = getattr(team, "reset", None)
                if callable(reset):
                    await reset()

        result = _run_async(execute())
        messages = getattr(result, "messages", None)
        if not isinstance(messages, list):
            raise AutoGenRuntimeError("AutoGen TaskResult.messages is unavailable")
        positions: list[dict[str, Any]] = []
        for message in messages:
            source = str(getattr(message, "source", "unknown"))[:160]
            content = getattr(message, "content", None)
            # Ignore non-agent event messages with no content.
            if content is None:
                continue
            positions.append(_position_from_content(content, source))
            if len(positions) >= max_rounds:
                break
        return {
            "status": "completed",
            "summary": "AutoGen bounded council completed; Top Commander decision still required",
            "rounds": len(positions),
            "positions": positions,
            "stop_reason": str(getattr(result, "stop_reason", ""))[:500],
            "majority_vote_used": False,
            "final_authority": "TOP_COMMANDER",
        }

    return runner


__all__ = ["AutoGenRuntimeError", "build_autogen_runner"]
