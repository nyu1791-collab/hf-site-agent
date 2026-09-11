#!/usr/bin/env python3
"""Quick team compiler for the framework-enabled AI Army.

This is a convenience facade, not a second orchestrator. It turns a small team
specification into native AgentTask objects that still run through AI Army V4,
FrameworkAdapterLayer, the parallel governor and the Single Writer join.

The toolkit never installs a framework, chooses a paid model, calls a provider,
grants repository write, mutates secrets, deploys or publishes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from scripts.parallel_framework_batch import ParallelBatchItem, build_parallel_batch_tasks

ALLOWED_RISK = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
PROFILE_FRAMEWORK = {
    "DURABLE": ("LANGGRAPH",),
    "ROLE_CREW": ("CREWAI",),
    "COUNCIL": ("AUTOGEN",),
    "CODING": ("GITHUB_COPILOT",),
    "NATIVE": ("NATIVE_V4",),
}
PROFILE_CAPABILITIES = {
    "DURABLE": ("graph", "checkpoint", "tool_use"),
    "ROLE_CREW": ("role_crew", "parallel", "tool_use"),
    "COUNCIL": ("debate", "delegation", "parallel_agents"),
    "CODING": ("coding", "repository_navigation", "agent_mode"),
    "NATIVE": ("general",),
}


class AIArmyToolkitError(ValueError):
    pass


@dataclass(frozen=True)
class TeamLane:
    lane_id: str
    objective: str
    slot: str
    profile: str = "NATIVE"
    risk_level: str = "MEDIUM"
    read_set: tuple[str, ...] = ()
    priority: str = "NORMAL"
    deterministic_validator_available: bool = True

    def validate(self) -> None:
        if not self.lane_id or len(self.lane_id) > 64:
            raise AIArmyToolkitError("lane_id must be 1-64 characters")
        if not self.objective or len(self.objective) > 5000:
            raise AIArmyToolkitError("lane objective must be 1-5000 characters")
        if self.risk_level not in ALLOWED_RISK:
            raise AIArmyToolkitError("unsupported lane risk")
        if self.profile not in PROFILE_FRAMEWORK:
            raise AIArmyToolkitError("unsupported lane profile")


def _to_batch_item(lane: TeamLane, *, copilot_explicitly_allowed: bool) -> ParallelBatchItem:
    lane.validate()
    if lane.profile == "CODING" and copilot_explicitly_allowed is not True:
        # Do not silently consume Copilot entitlement/credits. Native V4 can
        # still perform the coding task through verified free model routing.
        framework_preference = ("NATIVE_V4",)
        capabilities = ("general",)
        copilot_downgraded = True
    else:
        framework_preference = PROFILE_FRAMEWORK[lane.profile]
        capabilities = PROFILE_CAPABILITIES[lane.profile]
        copilot_downgraded = False
    return ParallelBatchItem(
        item_id=lane.lane_id,
        objective=lane.objective,
        slot=lane.slot,
        risk_level=lane.risk_level,
        framework_preference=framework_preference,
        framework_capabilities=capabilities,
        metadata={
            "framework_fallback_to_native": True,
            "priority": lane.priority,
            "read_set": list(lane.read_set),
            "deterministic_validator_available": lane.deterministic_validator_available,
            "toolkit_profile": lane.profile,
            "copilot_downgraded_to_native": copilot_downgraded,
        },
    )


def compile_team(
    *,
    mission_id: str,
    mission_objective: str,
    lanes: Sequence[TeamLane],
    framework_config: Mapping[str, Any],
    copilot_explicitly_allowed: bool = False,
) -> tuple[Any, ...]:
    if not mission_objective or len(mission_objective) > 5000:
        raise AIArmyToolkitError("mission_objective must be 1-5000 characters")
    if not lanes:
        raise AIArmyToolkitError("at least one team lane is required")
    items = tuple(_to_batch_item(lane, copilot_explicitly_allowed=copilot_explicitly_allowed) for lane in lanes)
    return build_parallel_batch_tasks(
        batch_id=mission_id,
        items=items,
        framework_config=framework_config,
        final_objective=(
            f"Integrate the bounded team outputs for mission: {mission_objective}. "
            "Resolve disagreement using evidence, preserve per-lane provenance, reject unverified authority expansion, "
            "and commit one Single Writer result. External frameworks may not merge, deploy, publish, mutate secrets or pay."
        ),
    )


def newsroom_team(topic: str) -> tuple[TeamLane, ...]:
    subject = str(topic or "").strip()
    if not subject:
        raise AIArmyToolkitError("topic is required")
    return (
        TeamLane(
            lane_id="research",
            slot="CONTEXT_LIBRARIAN",
            profile="DURABLE",
            risk_level="MEDIUM",
            objective=f"Research and structure source-backed facts for {subject}; separate facts, reports and interpretation.",
            priority="HIGH",
        ),
        TeamLane(
            lane_id="production",
            slot="OPERATIONS_LEAD",
            profile="ROLE_CREW",
            risk_level="LOW",
            objective=f"Turn verified material about {subject} into a concise script, captions and production/edit plan with consolidated ownership.",
        ),
        TeamLane(
            lane_id="independent_review",
            slot="QA_VALIDATOR",
            profile="COUNCIL",
            risk_level="MEDIUM",
            objective=f"Independently challenge the claims, framing, omissions and production risks for {subject}; return bounded corrections only.",
            priority="HIGH",
        ),
    )


def engineering_team(objective: str, *, include_copilot_lane: bool = False) -> tuple[TeamLane, ...]:
    goal = str(objective or "").strip()
    if not goal:
        raise AIArmyToolkitError("objective is required")
    lanes = [
        TeamLane(
            lane_id="plan",
            slot="OPERATIONS_LEAD",
            profile="DURABLE",
            objective=f"Build a bounded implementation plan for: {goal}",
        ),
        TeamLane(
            lane_id="review",
            slot="QA_VALIDATOR",
            profile="COUNCIL",
            objective=f"Adversarially review architecture, regressions and hidden assumptions for: {goal}",
        ),
    ]
    if include_copilot_lane:
        lanes.append(TeamLane(
            lane_id="coding_proposal",
            slot="ENGINEERING_AGENT",
            profile="CODING",
            objective=f"Produce a repository-scoped coding/test-repair proposal for: {goal}. Do not merge, deploy or publish.",
        ))
    return tuple(lanes)


__all__ = [
    "AIArmyToolkitError",
    "TeamLane",
    "compile_team",
    "engineering_team",
    "newsroom_team",
]
