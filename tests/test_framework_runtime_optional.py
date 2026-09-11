from __future__ import annotations

import json

import pytest

from scripts.framework_runtime_autogen import build_autogen_runner
from scripts.framework_runtime_crewai import CrewAIRuntimeError, build_crewai_runner

pytest.importorskip("autogen_agentchat")
pytest.importorskip("crewai")


class FakeMessage:
    def __init__(self, source, content):
        self.source = source
        self.content = content


class FakeTaskResult:
    def __init__(self, messages):
        self.messages = messages
        self.stop_reason = "bounded fixture"


class FakeTeam:
    def __init__(self):
        self.reset_called = False

    async def run(self, *, task):
        assert task
        return FakeTaskResult([
            FakeMessage("architect", json.dumps({
                "claim": "use native control plane",
                "evidence": ["bounded envelope"],
                "confidence": 0.91,
                "objection": "external framework may fail",
            })),
            FakeMessage("reviewer", json.dumps({
                "claim": "checkpoint must be durable",
                "evidence": ["sqlite fixture"],
                "confidence": 0.88,
                "objection": "cross-runner durability is false for sqlite",
            })),
        ])

    async def reset(self):
        self.reset_called = True


def test_autogen_runtime_collects_structured_positions_and_resets_team():
    holder = {}

    def factory(command, context, max_rounds):
        holder["team"] = FakeTeam()
        assert max_rounds == 3
        return holder["team"]

    runner = build_autogen_runner(factory, timeout_seconds=3)
    report = runner({"command_id": "c", "objective": "review"}, {"max_rounds": 3, "api_key": "redact"})
    assert report["status"] == "completed"
    assert report["rounds"] == 2
    assert all(set(("claim", "evidence", "confidence", "objection")).issubset(row) for row in report["positions"])
    assert report["majority_vote_used"] is False
    assert report["final_authority"] == "TOP_COMMANDER"
    assert holder["team"].reset_called is True


class FakeAgent:
    def __init__(self, max_iter):
        self.max_iter = max_iter


class FakeTaskOutput:
    def __init__(self, agent, raw):
        self.agent = agent
        self.raw = raw
        self.json_dict = None
        self.pydantic = None


class FakeCrewOutput:
    def __init__(self):
        self.tasks_output = [FakeTaskOutput("Engineer", "done"), FakeTaskOutput("Tester", "checked")]


class FakeCrew:
    def __init__(self, max_iter=2):
        self.agents = [FakeAgent(max_iter), FakeAgent(max_iter)]
        self.tasks = [object(), object()]

    def kickoff(self, *, inputs):
        assert inputs["ai_army_objective"]
        return FakeCrewOutput()


def test_crewai_runtime_enforces_actual_bounds_and_returns_task_status():
    runner = build_crewai_runner(lambda command, context, max_members, max_iterations: FakeCrew(max_iter=max_iterations))
    report = runner(
        {"command_id": "c", "objective": "code and test"},
        {"max_members": 3, "max_iterations": 2, "provider_timeout_bounded": True},
    )
    assert report["status"] == "completed"
    assert report["actual_member_count"] == 2
    assert report["actual_task_count"] == 2
    assert all(row["status"] == "completed" for row in report["member_results"])
    assert report["crew_destroyed"] is True
    assert report["authority_expanded"] is False


def test_crewai_runtime_blocks_missing_timeout_evidence_and_excessive_agent_iterations():
    runner = build_crewai_runner(lambda command, context, max_members, max_iterations: FakeCrew(max_iter=max_iterations))
    with pytest.raises(CrewAIRuntimeError, match="timeout evidence"):
        runner({"command_id": "c"}, {"max_members": 2, "max_iterations": 2})

    excessive = build_crewai_runner(lambda command, context, max_members, max_iterations: FakeCrew(max_iter=max_iterations + 1))
    with pytest.raises(CrewAIRuntimeError, match="max_iter"):
        excessive(
            {"command_id": "c"},
            {"max_members": 2, "max_iterations": 2, "provider_timeout_bounded": True},
        )
