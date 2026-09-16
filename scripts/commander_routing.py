#!/usr/bin/env python3
"""Deterministic Mission routing from ChatGPT Work to provider commanders.

Routing is a policy decision, not model promotion.  One suitable provider is
selected for a Mission; an independent verifier may be added explicitly, but
the router never broadcasts the same Task to all providers.  This module only
builds and validates read-only command envelopes.  Provider activation and
model selection remain separate approval-gated steps.
"""

from __future__ import annotations

from typing import Any, Mapping

try:
    from scripts.agent_runtime import AgentRegistry, CommandEnvelope, ContractError, make_command, stable_id
    from scripts.provider_registry import provider_config, validate_provider_registry
except ModuleNotFoundError:  # pragma: no cover
    from agent_runtime import AgentRegistry, CommandEnvelope, ContractError, make_command, stable_id
    from provider_registry import provider_config, validate_provider_registry


ROUTING_RULES: dict[str, str] = {
    "research": "google",
    "planning": "google",
    "multimodal": "google",
    "long_context": "google",
    "long-document": "google",
    "synthesis": "google",
    "pdf": "google",
    "image": "google",
    "video": "google",
    "audio": "google",
    "coding": "nvidia",
    "repository": "nvidia",
    "debug": "nvidia",
    "testing": "nvidia",
    "infrastructure": "nvidia",
    "code_review": "nvidia",
    "bug_localization": "nvidia",
    "test_plan": "nvidia",
    "mass_processing": "groq",
    "bulk": "groq",
    "fast_analysis": "groq",
    "summary": "groq",
    "classification": "groq",
    "extraction": "groq",
    "json_transform": "groq",
    "log_triage": "groq",
    "json": "groq",
    "log": "groq",
    "first_pass": "groq",
    "light_worker": "openrouter",
    "simple": "python",
}

COMMANDER_BY_PROVIDER = {
    "google": "google-general-commander",
    "nvidia": "nvidia-engineering-commander",
    "groq": "groq-rapid-commander",
}


class RoutingError(ContractError):
    """Invalid or unsafe Mission routing request."""


def _mission_type(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        raise RoutingError("mission_type is required")
    if normalized not in ROUTING_RULES:
        raise RoutingError(f"mission_type is not routable: {normalized}")
    return normalized


def route_mission(
    mission_type: str,
    provider_registry: Mapping[str, Any],
    *,
    independent_verifier: str | None = None,
) -> dict[str, Any]:
    """Return a provider route without making an API call.

    A disabled/unprobed provider still yields a deterministic *blocked* route;
    it is never silently replaced by a paid model, another provider, or a
    generic OpenRouter router.
    """
    validate_provider_registry(provider_registry)
    normalized = _mission_type(mission_type)
    provider_id = ROUTING_RULES[normalized]
    if provider_id == "python":
        if independent_verifier:
            raise RoutingError("Python-only processing cannot fan out to a provider verifier")
        return {
            "status": "python_first",
            "mission_type": normalized,
            "provider": "python",
            "commander_agent_id": None,
            "independent_verifier": None,
            "broadcast": False,
            "paid_fallback": False,
        }
    if provider_id == "openrouter":
        # OpenRouter is a worker provider and is intentionally not returned as
        # a ChatGPT Work direct commander route.
        return {
            "status": "worker_route_required",
            "mission_type": normalized,
            "provider": "openrouter",
            "commander_agent_id": None,
            "independent_verifier": None,
            "broadcast": False,
            "paid_fallback": False,
        }
    if provider_id not in COMMANDER_BY_PROVIDER:
        raise RoutingError("provider is outside the commander boundary")
    provider = provider_config(provider_registry, provider_id)
    verifier = None
    if independent_verifier:
        verifier = str(independent_verifier).strip().lower()
        if verifier not in COMMANDER_BY_PROVIDER or verifier == provider_id:
            raise RoutingError("independent verifier must be a different commander provider")
    ready = (
        provider.get("enabled") is True
        and provider.get("health_status") == "HEALTHY"
        and provider.get("circuit_state") == "CLOSED"
    )
    return {
        "status": "ready" if ready else "blocked_provider_not_ready",
        "mission_type": normalized,
        "provider": provider_id,
        "commander_agent_id": COMMANDER_BY_PROVIDER[provider_id],
        "independent_verifier": verifier,
        "broadcast": bool(verifier),
        "max_provider_paths": 2 if verifier else 1,
        "reason": "provider_activation_and_probe_required" if not ready else "single_primary_route",
        "paid_fallback": False,
    }


def build_commander_command(
    mission_id: str,
    mission_type: str,
    objective: str,
    provider_registry: Mapping[str, Any],
    *,
    command_id: str | None = None,
    request_budget: int = 0,
    token_budget: int = 800,
    deadline: str | None = None,
) -> tuple[dict[str, Any], CommandEnvelope]:
    """Build one bounded direct Commander command for a routed Mission."""
    route = route_mission(mission_type, provider_registry)
    if route["provider"] not in COMMANDER_BY_PROVIDER:
        raise RoutingError("only a commander provider may receive a direct command")
    registry = AgentRegistry()
    child_agent_id = str(route["commander_agent_id"])
    child = registry.get(child_agent_id)
    safe_command_id = command_id or stable_id("COMMAND", {"mission_id": mission_id, "mission_type": mission_type, "objective": objective})
    command = make_command(
        registry,
        mission_id=mission_id,
        command_id=safe_command_id,
        parent_command_id=None,
        parent_agent_id="chatgpt-work",
        child_agent_id=child_agent_id,
        mission=objective,
        objective=objective,
        constraints=(
            "read_only_draft",
            "commander_approval_required",
            "no_secret_change",
            "no_publication_or_payment",
            "no_paid_fallback",
        ),
        input_refs=(),
        expected_output={"schema": "report-envelope-v1", "route": route["provider"]},
        provider_preference=route["provider"],
        token_budget=token_budget,
        time_budget_ms=child.time_budget_ms,
        request_budget=request_budget,
        estimated_free_requests=request_budget,
        tool_scope=child.allowed_tools,
        done_when=("structured commander report returned", "commander approval remains required"),
        depth=1,
        deadline=deadline,
        side_effect_level="read_only_draft",
    )
    return route, command


def validate_commander_command(command: CommandEnvelope, registry: AgentRegistry | None = None) -> None:
    """Re-check that a command is a direct provider-commander command."""
    registry = registry or AgentRegistry()
    command.validate(registry)
    if command.parent_agent_id != "chatgpt-work" or command.depth != 1:
        raise RoutingError("commander command must originate at the root")
    expected = registry.get(command.child_agent_id).provider_id
    if expected not in COMMANDER_BY_PROVIDER:
        raise RoutingError("command child is not a commander provider")
    if command.provider_preference != expected:
        raise RoutingError("provider_preference does not match commander provider")
    if command.side_effect_level not in {"read_only", "read_only_draft", "dry_run"}:
        raise RoutingError("commander route is not read-only")


__all__ = [
    "COMMANDER_BY_PROVIDER", "ROUTING_RULES", "RoutingError", "build_commander_command",
    "route_mission", "validate_commander_command",
]
