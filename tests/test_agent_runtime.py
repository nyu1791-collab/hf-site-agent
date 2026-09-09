import threading
import tempfile
import unittest

from scripts.agent_runtime import (
    AgentRegistry,
    ArtifactStore,
    BudgetError,
    CheckpointStore,
    CommandRuntime,
    ContractError,
    HierarchicalCache,
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyStore,
    MissionBudget,
    PermissionError,
    ReportEnvelope,
    ResponseFreshnessGuard,
    TraceStore,
    compact_context,
    make_command,
    project_context,
    safe_text,
    stable_hash,
)


def command_for(
    registry,
    *,
    command_id,
    parent_agent_id,
    child_agent_id,
    depth=2,
    group=None,
    mission_id="MISSION-TEST",
    parent_command_id=None,
    provider_preference=None,
    request_budget=0,
):
    if parent_command_id is None and depth > 1:
        parent_command_id = f"{mission_id}-C01"
    return make_command(
        registry,
        mission_id=mission_id,
        command_id=command_id,
        parent_command_id=parent_command_id,
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
        provider_preference=provider_preference,
        request_budget=request_budget,
        estimated_free_requests=request_budget,
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
        self.assertIn("google-general-commander", tree)
        self.assertIn("nvidia-engineering-commander", tree)
        self.assertIn("groq-rapid-commander", tree)
        roots = [spec for spec in self.registry.all() if spec.parent_agent_id == "chatgpt-work"]
        self.assertEqual({spec.agent_id for spec in roots}, {
            "google-general-commander",
            "nvidia-engineering-commander",
            "groq-rapid-commander",
        })
        self.assertTrue(all(spec.provider_id in {"google", "nvidia", "groq"} for spec in roots))
        self.assertTrue(all(spec.active is False and spec.requires_explicit_approval for spec in roots))
        command = command_for(
            self.registry,
            command_id="MISSION-TEST-C02",
            parent_agent_id="chatgpt-work",
            child_agent_id="nvidia-engineering-commander",
            depth=1,
        )
        self.assertEqual(command.role, "engineering_commander")
        with self.assertRaises(PermissionError):
            command_for(
                self.registry,
                command_id="MISSION-TEST-BAD",
                parent_agent_id="content-specialist",
                child_agent_id="google-general-commander",
                depth=1,
            )

    def test_provider_request_budget_is_reserved_before_dispatch(self):
        runtime = CommandRuntime(
            self.registry,
            MissionBudget(max_nvidia_requests=1, max_parallel=1),
        )
        first = command_for(
            self.registry,
            command_id="MISSION-BUDGET-C01",
            parent_agent_id="chatgpt-work",
            child_agent_id="nvidia-engineering-commander",
            depth=1,
            provider_preference="nvidia",
            request_budget=1,
        )
        runtime.dispatch(first, lambda value: success_report(value))
        second = command_for(
            self.registry,
            command_id="MISSION-BUDGET-C02",
            parent_agent_id="chatgpt-work",
            child_agent_id="nvidia-engineering-commander",
            depth=1,
            provider_preference="nvidia",
            request_budget=1,
        )
        with self.assertRaises(BudgetError):
            runtime.dispatch(second, lambda value: success_report(value))
        self.assertEqual(runtime.metrics()["budget"]["provider_requests_used"]["nvidia"], 1)

    def test_fan_out_fan_in_keeps_partial_success(self):
        runtime = CommandRuntime(self.registry)
        commands = [
            command_for(
                self.registry,
                command_id="MISSION-TEST-T01",
                parent_agent_id="nvidia-engineering-commander",
                child_agent_id="video-specialist",
                group="research-batch",
            ),
            command_for(
                self.registry,
                command_id="MISSION-TEST-T02",
                parent_agent_id="nvidia-engineering-commander",
                child_agent_id="code-specialist",
                group="research-batch",
            ),
            command_for(
                self.registry,
                command_id="MISSION-TEST-T03",
                parent_agent_id="nvidia-engineering-commander",
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
            child_agent_id="nvidia-engineering-commander",
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
            parent_agent_id="google-general-commander",
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
            parent_agent_id="nvidia-engineering-commander",
            child_agent_id="qa-specialist",
        )
        blocked = type(blocked)(**{**blocked.to_dict(), "mission_id": "MISSION-CANCEL"})
        report = cancelled.dispatch(blocked, lambda value: success_report(value))
        self.assertEqual(report.status, "cancelled")

    def test_cancel_mission_does_not_affect_other_mission(self):
        runtime = CommandRuntime(self.registry)
        mission_a = command_for(
            self.registry,
            command_id="MISSION-A-T01",
            mission_id="MISSION-A",
            parent_agent_id="nvidia-engineering-commander",
            child_agent_id="qa-specialist",
        )
        mission_b = command_for(
            self.registry,
            command_id="MISSION-B-T01",
            mission_id="MISSION-B",
            parent_agent_id="nvidia-engineering-commander",
            child_agent_id="metrics-specialist",
        )
        runtime.cancel_mission("MISSION-A")
        self.assertEqual(runtime.dispatch(mission_a, success_report).status, "cancelled")
        self.assertEqual(runtime.dispatch(mission_b, success_report).status, "completed")
        self.assertEqual(runtime.status(mission_b.command_id), "completed")

    def test_cancel_parent_propagates_to_child(self):
        runtime = CommandRuntime(self.registry)
        parent = command_for(
            self.registry,
            command_id="MISSION-CANCEL-PARENT",
            mission_id="MISSION-CANCEL-PARENT-M",
            parent_agent_id="chatgpt-work",
            child_agent_id="google-general-commander",
            depth=1,
        )
        child = command_for(
            self.registry,
            command_id="MISSION-CANCEL-CHILD",
            mission_id="MISSION-CANCEL-PARENT-M",
            parent_agent_id="google-general-commander",
            child_agent_id="content-specialist",
            parent_command_id=parent.command_id,
        )
        started = threading.Event()
        release = threading.Event()
        result = []

        def parent_handler(command):
            started.set()
            release.wait(1)
            return success_report(command)

        thread = threading.Thread(target=lambda: result.append(runtime.dispatch(parent, parent_handler)))
        thread.start()
        self.assertTrue(started.wait(1))
        runtime.cancel_command(parent.command_id)
        self.assertEqual(runtime.dispatch(child, success_report).status, "cancelled")
        release.set()
        thread.join(1)
        self.assertEqual(result[0].status, "cancelled")

    def test_cancelled_sibling_isolated(self):
        runtime = CommandRuntime(self.registry)
        first = command_for(
            self.registry,
            command_id="MISSION-SIBLING-T01",
            parent_agent_id="google-general-commander",
            child_agent_id="content-specialist",
        )
        sibling = command_for(
            self.registry,
            command_id="MISSION-SIBLING-T02",
            parent_agent_id="google-general-commander",
            child_agent_id="product-specialist",
        )
        # A command cancellation must stop only the selected branch.
        isolated = CommandRuntime(self.registry)
        isolated.dispatch(first, success_report)
        isolated.cancel_command(first.command_id)
        self.assertEqual(isolated.dispatch(sibling, success_report).status, "completed")

    def test_completed_result_is_preserved_after_cancel(self):
        runtime = CommandRuntime(self.registry)
        command = command_for(
            self.registry,
            command_id="MISSION-CANCEL-COMPLETED",
            parent_agent_id="google-general-commander",
            child_agent_id="product-specialist",
        )
        report = runtime.dispatch(command, success_report)
        runtime.cancel_mission(command.mission_id)
        self.assertEqual(runtime.dispatch(command, success_report).to_dict(), report.to_dict())
        self.assertEqual(report.status, "completed")

    def test_double_cancel_is_idempotent(self):
        runtime = CommandRuntime(self.registry)
        runtime.cancel_mission("MISSION-DOUBLE-CANCEL")
        runtime.cancel_mission("MISSION-DOUBLE-CANCEL")
        command = command_for(
            self.registry,
            command_id="MISSION-DOUBLE-CANCEL-T01",
            mission_id="MISSION-DOUBLE-CANCEL",
            parent_agent_id="nvidia-engineering-commander",
            child_agent_id="qa-specialist",
        )
        report = runtime.dispatch(command, success_report)
        self.assertEqual(report.status, "cancelled")
        self.assertEqual(sum(event["event"] == "cancelled" for event in runtime.trace.events()), 1)

    def test_background_cancel_stops_only_relevant_mission(self):
        runtime = CommandRuntime(self.registry)
        commands = [
            command_for(
                self.registry,
                command_id="MISSION-BG-A-T01",
                mission_id="MISSION-BG-A",
                parent_agent_id="nvidia-engineering-commander",
                child_agent_id="qa-specialist",
                group="background-batch",
            ),
            command_for(
                self.registry,
                command_id="MISSION-BG-B-T01",
                mission_id="MISSION-BG-B",
                parent_agent_id="nvidia-engineering-commander",
                child_agent_id="metrics-specialist",
                group="background-batch",
            ),
        ]
        started = threading.Event()

        def handler(command):
            if command.mission_id == "MISSION-BG-A":
                started.set()
                while not runtime.is_cancelled(command.mission_id, command.command_id, command.parent_command_id):
                    threading.Event().wait(0.005)
            return success_report(command)

        result = []
        thread = threading.Thread(target=lambda: result.extend(runtime.run_fan_out(commands, {item.command_id: handler for item in commands})))
        thread.start()
        self.assertTrue(started.wait(1))
        runtime.cancel_mission("MISSION-BG-A")
        thread.join(2)
        statuses = {report.mission_id: report.status for report in result}
        self.assertEqual(statuses["MISSION-BG-A"], "cancelled")
        self.assertEqual(statuses["MISSION-BG-B"], "completed")

    def test_new_mission_can_start_after_cancellation(self):
        runtime = CommandRuntime(self.registry)
        runtime.cancel_mission("MISSION-OLD")
        old = command_for(
            self.registry,
            command_id="MISSION-OLD-T01",
            mission_id="MISSION-OLD",
            parent_agent_id="nvidia-engineering-commander",
            child_agent_id="qa-specialist",
        )
        new = command_for(
            self.registry,
            command_id="MISSION-NEW-T01",
            mission_id="MISSION-NEW",
            parent_agent_id="nvidia-engineering-commander",
            child_agent_id="qa-specialist",
        )
        self.assertEqual(runtime.dispatch(old, success_report).status, "cancelled")
        self.assertEqual(runtime.dispatch(new, success_report).status, "completed")

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

    def test_checkpoint_survives_runtime_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            first = CheckpointStore(directory)
            first.save("MISSION-RESTART", "stage-1", {"completed": ["task-1"], "keep": True})
            second = CheckpointStore(directory)
            self.assertEqual(
                second.load("MISSION-RESTART", "stage-1")["state"],
                {"completed": ["task-1"], "keep": True},
            )


class IdempotencyTests(unittest.TestCase):
    def test_null_idempotency_key_rejected(self):
        store = IdempotencyStore()
        with self.assertRaises(ContractError):
            store.execute(
                mission_id="MISSION-IDEM-01", command_id="COMMAND-IDEM-01",
                idempotency_key=None, operation_type="FILE_WRITE", payload={}, operation=lambda: {},
            )

    def test_empty_idempotency_key_rejected(self):
        store = IdempotencyStore()
        with self.assertRaises(ContractError):
            store.execute(
                mission_id="MISSION-IDEM-02", command_id="COMMAND-IDEM-02",
                idempotency_key="", operation_type="FILE_WRITE", payload={}, operation=lambda: {},
            )

    def test_whitespace_idempotency_key_rejected(self):
        store = IdempotencyStore()
        with self.assertRaises(ContractError):
            store.execute(
                mission_id="MISSION-IDEM-03", command_id="COMMAND-IDEM-03",
                idempotency_key="   ", operation_type="FILE_WRITE", payload={}, operation=lambda: {},
            )

    def test_duplicate_mutation_executes_once(self):
        store = IdempotencyStore()
        calls = 0

        def mutation():
            nonlocal calls
            calls += 1
            return {"written": True, "sequence": calls}

        kwargs = {
            "mission_id": "MISSION-IDEM-04",
            "command_id": "COMMAND-IDEM-04",
            "idempotency_key": "IDEMP-IDEM-04",
            "operation_type": "FILE_WRITE",
            "payload": {"path": "draft.json", "value": "bounded"},
        }
        first = store.execute(**kwargs, operation=mutation)
        second = store.execute(**kwargs, operation=mutation)
        self.assertEqual(first, second)
        self.assertEqual(calls, 1)

    def test_same_key_same_payload_replays_same_result(self):
        store = IdempotencyStore()
        calls = []
        kwargs = {
            "mission_id": "MISSION-IDEM-05",
            "command_id": "COMMAND-IDEM-05",
            "idempotency_key": "IDEMP-IDEM-05",
            "operation_type": "GITHUB_WRITE",
            "payload": {"branch": "draft", "change": "safe"},
        }
        first = store.execute(**kwargs, operation=lambda: {"commit": "local-draft"})
        calls.append(first)
        second = store.execute(**kwargs, operation=lambda: {"commit": "should-not-run"})
        calls.append(second)
        self.assertEqual(calls[0], {"commit": "local-draft"})
        self.assertEqual(calls[1], calls[0])

    def test_same_key_different_payload_conflicts(self):
        store = IdempotencyStore()
        kwargs = {
            "mission_id": "MISSION-IDEM-06",
            "command_id": "COMMAND-IDEM-06",
            "idempotency_key": "IDEMP-IDEM-06",
            "operation_type": "UPLOAD",
            "payload": {"artifact": "artifact-a"},
        }
        store.execute(**kwargs, operation=lambda: {"uploaded": True})
        with self.assertRaises(IdempotencyConflict) as caught:
            store.execute(**{**kwargs, "payload": {"artifact": "artifact-b"}}, operation=lambda: {"uploaded": True})
        self.assertIn("IDEMPOTENCY_CONFLICT", str(caught.exception))

    def test_timeout_retry_cannot_double_write(self):
        store = IdempotencyStore()
        calls = 0

        def ambiguous_write():
            nonlocal calls
            calls += 1
            raise TimeoutError("provider response was ambiguous")

        kwargs = {
            "mission_id": "MISSION-IDEM-07",
            "command_id": "COMMAND-IDEM-07",
            "idempotency_key": "IDEMP-IDEM-07",
            "operation_type": "POST",
            "payload": {"action": "publish-draft"},
        }
        with self.assertRaises(TimeoutError):
            store.execute(**kwargs, operation=ambiguous_write)
        with self.assertRaises(IdempotencyInProgress):
            store.execute(**kwargs, operation=ambiguous_write)
        self.assertEqual(calls, 1)
        self.assertEqual(store.get(kwargs["idempotency_key"])["status"], "unknown")

    def test_restart_preserves_command_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/idempotency.json"
            registry = AgentRegistry()
            command = command_for(
                registry,
                command_id="MISSION-IDEM-08-T01",
                parent_agent_id="google-general-commander",
                child_agent_id="product-specialist",
            )
            calls = 0

            def handler(value):
                nonlocal calls
                calls += 1
                return success_report(value, summary="persisted result")

            first_runtime = CommandRuntime(registry, idempotency=IdempotencyStore(path))
            first = first_runtime.dispatch(command, handler)
            second_runtime = CommandRuntime(registry, idempotency=IdempotencyStore(path))
            second = second_runtime.dispatch(command, handler)
            self.assertEqual(first.to_dict(), second.to_dict())
            self.assertEqual(calls, 1)


class ResponseFreshnessTests(unittest.TestCase):
    def test_out_of_order_and_duplicate_responses_are_rejected(self):
        guard = ResponseFreshnessGuard()
        self.assertTrue(guard.accept(mission_id="MISSION-FRESH-01", command_id="COMMAND-FRESH-01", response_version=2))
        self.assertFalse(guard.accept(mission_id="MISSION-FRESH-01", command_id="COMMAND-FRESH-01", response_version=1))
        self.assertFalse(guard.accept(mission_id="MISSION-FRESH-01", command_id="COMMAND-FRESH-01", response_version=2))
        self.assertTrue(guard.accept(mission_id="MISSION-FRESH-01", command_id="COMMAND-FRESH-01", response_version=3))
        self.assertEqual(guard.latest(mission_id="MISSION-FRESH-01", command_id="COMMAND-FRESH-01"), 3)

    def test_freshness_isolated_by_mission(self):
        guard = ResponseFreshnessGuard()
        self.assertTrue(guard.accept(mission_id="MISSION-FRESH-A", command_id="COMMAND-FRESH", response_version=4))
        self.assertTrue(guard.accept(mission_id="MISSION-FRESH-B", command_id="COMMAND-FRESH", response_version=0))

    def test_invalid_response_version_is_rejected(self):
        guard = ResponseFreshnessGuard()
        with self.assertRaises(ContractError):
            guard.accept(mission_id="MISSION-FRESH-02", command_id="COMMAND-FRESH-02", response_version=-1)
        with self.assertRaises(ContractError):
            guard.accept(mission_id="MISSION-FRESH-02", command_id="COMMAND-FRESH-02", response_version=True)

    def test_runtime_does_not_adopt_a_stale_report(self):
        registry = AgentRegistry()
        guard = ResponseFreshnessGuard()
        runtime = CommandRuntime(registry, freshness=guard)
        command = command_for(
            registry,
            command_id="MISSION-FRESH-03-T01",
            parent_agent_id="google-general-commander",
            child_agent_id="product-specialist",
            mission_id="MISSION-FRESH-03",
        )
        self.assertTrue(guard.accept(
            mission_id=command.mission_id,
            command_id=command.command_id,
            response_version=2,
        ))
        report = runtime.dispatch(command, lambda value: success_report(value))
        self.assertEqual(report.status, "failed")
        self.assertIn("STALE_RESPONSE_REJECTED", report.errors)


if __name__ == "__main__":
    unittest.main()
