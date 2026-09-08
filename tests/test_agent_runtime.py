import threading
import unittest

from scripts.agent_runtime import (
    AgentRegistry,
    ArtifactStore,
    CheckpointStore,
    CommandRuntime,
    ContractError,
    HierarchicalCache,
    PermissionError,
    ReportEnvelope,
    TraceStore,
    compact_context,
    make_command,
    project_context,
    safe_text,
    stable_hash,
)


def command_for(registry, *, command_id, parent_agent_id, child_agent_id, depth=2, group=None):
    return make_command(
        registry,
        mission_id="MISSION-TEST",
        command_id=command_id,
        parent_command_id="MISSION-TEST-C01" if depth > 1 else None,
        parent_agent_id=parent_agent_id,
        child_agent_id=child_agent_id,
        mission="安全な階層ランタイムの検証",
        objective=f"検証用の {child_agent_id} 作業",
        constraints=("読み取りまたは下書きのみ",),
        input_refs=("artifact-test-input",),
        expected_output={"type": "report-envelope-v1"},
        token_budget=100 if child_agent_id.endswith("specialist") else 50,
        time_budget_ms=1_000,
        tool_scope=registry.get(child_agent_id).allowed_tools,
        done_when=("構造化Reportを返す",),
        depth=depth,
        parallel_group=group,
    )


def success_report(command, *, summary="completed", tokens=1):
    return ReportEnvelope(
        mission_id=command.mission_id,
        command_id=command.command_id,
        parent_command_id=command.parent_command_id,
        agent_id=command.child_agent_id,
        parent_agent_id=command.parent_agent_id,
        rank=command.rank,
        status="completed",
        summary=summary,
        result={"ok": True},
        evidence=("local-test",),
        duration_ms=1,
        tokens_used=tokens,
        tools_used=command.tool_scope,
    )


class HierarchicalRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.registry = AgentRegistry()

    def test_tree_and_downward_only_dispatch(self):
        tree = self.registry.tree()
        self.assertIn("chatgpt-work", tree)
        self.assertIn("qwen-planner", tree)
        self.assertIn("deepseek-critic", tree)
        command = command_for(
            self.registry,
            command_id="MISSION-TEST-C02",
            parent_agent_id="chatgpt-work",
            child_agent_id="deepseek-critic",
            depth=1,
        )
        self.assertEqual(command.role, "upper_commander")
        with self.assertRaises(PermissionError):
            command_for(
                self.registry,
                command_id="MISSION-TEST-BAD",
                parent_agent_id="content-specialist",
                child_agent_id="qwen-planner",
                depth=1,
            )

    def test_fan_out_fan_in_keeps_partial_success(self):
        runtime = CommandRuntime(self.registry)
        commands = [
            command_for(
                self.registry,
                command_id="MISSION-TEST-T01",
                parent_agent_id="deepseek-critic",
                child_agent_id="video-specialist",
                group="research-batch",
            ),
            command_for(
                self.registry,
                command_id="MISSION-TEST-T02",
                parent_agent_id="deepseek-critic",
                child_agent_id="code-specialist",
                group="research-batch",
            ),
            command_for(
                self.registry,
                command_id="MISSION-TEST-T03",
                parent_agent_id="deepseek-critic",
                child_agent_id="metrics-specialist",
                group="research-batch",
            ),
        ]
        started = []
        lock = threading.Lock()

        def handler(command):
            with lock:
                started.append(command.command_id)
            if command.command_id.endswith("T02"):
                raise RuntimeError("simulated bounded failure")
            return success_report(command, summary="partial success")

        reports = runtime.run_fan_out(
            commands,
            {command.command_id: handler for command in commands},
        )
        self.assertEqual(len(reports), 3)
        self.assertEqual(sum(report.status == "completed" for report in reports), 2)
        self.assertEqual(sum(report.status == "failed" for report in reports), 1)
        self.assertEqual(len(started), 3)
        parent = command_for(
            self.registry,
            command_id="MISSION-TEST-C01",
            parent_agent_id="chatgpt-work",
            child_agent_id="deepseek-critic",
            depth=1,
        )
        aggregate = runtime.fan_in(parent, reports)
        self.assertEqual(aggregate.status, "completed_with_warnings")
        self.assertEqual(aggregate.result["completed"], 2)
        self.assertEqual(aggregate.result["failed"], 1)
        self.assertEqual(runtime.metrics()["partial_failure_rate"], 1 / 4)

    def test_idempotency_and_cancellation(self):
        runtime = CommandRuntime(self.registry)
        command = command_for(
            self.registry,
            command_id="MISSION-TEST-IDEMPOTENT",
            parent_agent_id="qwen-planner",
            child_agent_id="product-specialist",
        )
        calls = 0

        def handler(value):
            nonlocal calls
            calls += 1
            return success_report(value)

        first = runtime.dispatch(command, handler)
        second = runtime.dispatch(command, handler)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(calls, 1)
        self.assertEqual(runtime.metrics()["duplicate_commands"], 1)

        cancelled = CommandRuntime(self.registry)
        cancelled.cancel_mission("MISSION-CANCEL")
        blocked = command_for(
            self.registry,
            command_id="MISSION-CANCEL-T01",
            parent_agent_id="deepseek-critic",
            child_agent_id="qa-specialist",
        )
        blocked = type(blocked)(**{**blocked.to_dict(), "mission_id": "MISSION-CANCEL"})
        report = cancelled.dispatch(blocked, lambda value: success_report(value))
        self.assertEqual(report.status, "cancelled")

    def test_artifact_cache_checkpoint_context_and_safe_trace(self):
        artifacts = ArtifactStore()
        artifact_id = artifacts.put({"kind": "brief", "value": "短い入力"}, mission_id="MISSION-TEST")
        self.assertEqual(artifacts.get(artifact_id)["value"], "短い入力")
        with self.assertRaises(ContractError):
            artifacts.put({"api_key": "secret"})

        cache = HierarchicalCache()
        key = cache.key("mission", stable_hash({"input": 1}), "content-specialist", "v1", "free-model")
        self.assertIsNone(cache.get(key))
        cache.set(key, {"artifact_id": artifact_id})
        self.assertEqual(cache.get(key)["artifact_id"], artifact_id)
        self.assertEqual(cache.stats()["hit_rate"], 0.5)

        checkpoints = CheckpointStore()
        checkpoints.save("MISSION-TEST", "stage-1", {"done": ["T01"]})
        self.assertEqual(checkpoints.load("MISSION-TEST", "stage-1")["state"]["done"], ["T01"])

        projected = project_context(
            "業種テンプレートを安全に設計する",
            ["YouTube投稿はしない", "秘密値を扱わない"],
            {"stage": "design"},
            [artifact_id],
            "report-envelope-v1",
            budget=2_000,
        )
        self.assertEqual(set(projected), {"MISSION", "CONSTRAINTS", "RELEVANT_STATE", "INPUT_REFERENCES", "OUTPUT_SCHEMA"})
        compacted = compact_context(
            [{"decisions": ["read-only"], "open_tasks": ["fan-in"]}, {"evidence": ["test-pass"]}],
            budget=1_000,
        )
        self.assertIn("decisions", compacted)
        self.assertIn("evidence", compacted)

        trace = TraceStore()
        trace.emit("started", mission_id="MISSION-TEST", command_id="C01", agent_id="content-specialist", rank=2, status="running")
        self.assertEqual(trace.events()[0]["event"], "started")
        with self.assertRaises(ContractError):
            safe_text("hf_abcdefghijklmnop")


if __name__ == "__main__":
    unittest.main()

