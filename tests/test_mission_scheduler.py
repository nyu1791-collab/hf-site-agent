import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import time
import unittest

from scripts.mission_scheduler import (
    HierarchicalMissionScheduler,
    IdempotencyConflict,
    LedgerError,
    MissionCheckpointStore,
    MissionPlan,
    MissionReservationLedger,
    MissionTask,
    ProviderInterrupted,
    SchedulerError,
    TaskResult,
)


PROVIDERS = ("google", "nvidia", "groq", "openrouter")


def limits(requests=20, tokens=2_000):
    return {provider: {"requests": requests, "tokens": tokens} for provider in PROVIDERS}


def task(
    task_id,
    *,
    mission_id="M-1",
    provider_id="google",
    owner_corps="GOOGLE",
    depends_on=(),
    depth=1,
    budget=1,
    tokens=100,
    version=1,
):
    return MissionTask(
        mission_id=mission_id,
        task_id=task_id,
        parent_task_id=None,
        parent_agent_id=f"{owner_corps.lower()}-commander",
        owner_corps=owner_corps,
        role="SPECIALIST",
        required_capabilities=("structured_output",),
        priority=0,
        risk_level="LOW",
        complexity_level=1,
        deadline=None,
        request_budget=budget,
        token_budget=tokens,
        estimated_cost=0,
        idempotency_key=f"idem-{mission_id}-{task_id}",
        response_version=version,
        delegation_depth=depth,
        provider_id=provider_id,
        depends_on=tuple(depends_on),
        parallel_group="stage-a" if not depends_on else "stage-b",
    )


def plan(tasks, *, mission_id="M-1", max_requests=20, max_tokens=2_000, provider_request_budgets=None, provider_token_budgets=None):
    return MissionPlan(
        mission_id=mission_id,
        tasks=tuple(tasks),
        max_total_requests=max_requests,
        max_total_tokens=max_tokens,
        max_parallel=3,
        provider_request_budgets=provider_request_budgets or {provider: max_requests for provider in PROVIDERS},
        provider_token_budgets=provider_token_budgets or {provider: max_tokens for provider in PROVIDERS},
    )


class MissionReservationLedgerTests(unittest.TestCase):
    def test_unknown_provider_limit_fails_closed(self):
        ledger = MissionReservationLedger(provider_limits={"google": {"requests": None, "tokens": None}})
        ledger.register_mission(
            "M-1", request_budget=2, token_budget=200,
            provider_request_budgets={"google": 2}, provider_token_budgets={"google": 200},
        )
        with self.assertRaises(LedgerError):
            ledger.reserve(
                mission_id="M-1", task_id="T-1", provider_id="google", idempotency_key="K-1",
                payload={"operation": "probe"}, requested_requests=1, requested_tokens=100,
            )

    def test_same_key_replays_and_different_payload_conflicts(self):
        ledger = MissionReservationLedger(provider_limits=limits())
        ledger.register_mission(
            "M-1", request_budget=3, token_budget=300,
            provider_request_budgets={"google": 3}, provider_token_budgets={"google": 300},
        )
        first = ledger.reserve(
            mission_id="M-1", task_id="T-1", provider_id="google", idempotency_key="K-1",
            payload={"objective": "same"}, requested_requests=1, requested_tokens=100,
        )
        replay = ledger.reserve(
            mission_id="M-1", task_id="T-1", provider_id="google", idempotency_key="K-1",
            payload={"objective": "same"}, requested_requests=1, requested_tokens=100,
        )
        self.assertEqual(first["reservation_id"], replay["reservation_id"])
        with self.assertRaises(IdempotencyConflict):
            ledger.reserve(
                mission_id="M-1", task_id="T-1", provider_id="google", idempotency_key="K-1",
                payload={"objective": "different"}, requested_requests=1, requested_tokens=100,
            )

    def test_atomic_reservation_across_two_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            kwargs = {"provider_limits": {"google": {"requests": 1, "tokens": 100}}}
            left = MissionReservationLedger(path, **kwargs)
            right = MissionReservationLedger(path, **kwargs)
            for ledger in (left, right):
                ledger.register_mission(
                    "M-1", request_budget=1, token_budget=100,
                    provider_request_budgets={"google": 1}, provider_token_budgets={"google": 100},
                )

            def reserve(ledger, task_id):
                try:
                    return ledger.reserve(
                        mission_id="M-1", task_id=task_id, provider_id="google", idempotency_key=f"K-{task_id}",
                        payload={"task": task_id}, requested_requests=1, requested_tokens=100,
                    )["state"]
                except LedgerError as exc:
                    return str(exc)

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda pair: reserve(*pair), ((left, "T-1"), (right, "T-2"))))
            self.assertEqual(results.count("reserved"), 1)
            self.assertEqual(sum(result == "REQUEST_RESERVATION_EXCEEDED" for result in results), 1)

    def test_unsettled_is_retained_and_not_released_by_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = MissionReservationLedger(Path(directory) / "ledger.json", provider_limits=limits())
            ledger.register_mission(
                "M-1", request_budget=2, token_budget=200,
                provider_request_budgets={"google": 2}, provider_token_budgets={"google": 200},
            )
            record = ledger.reserve(
                mission_id="M-1", task_id="T-1", provider_id="google", idempotency_key="K-1",
                payload={"operation": "call"}, requested_requests=1, requested_tokens=100,
            )
            ledger.mark_dispatched(record["reservation_id"])
            recovered = ledger.recover()
            self.assertEqual(len(recovered), 1)
            self.assertEqual(ledger.get(record["reservation_id"])["state"], "unsettled")


class MissionSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = MissionReservationLedger(provider_limits=limits())
        self.checkpoints = MissionCheckpointStore()
        self.scheduler = HierarchicalMissionScheduler(
            self.ledger,
            checkpoints=self.checkpoints,
            provider_states={
                provider: {"health_status": "HEALTHY", "circuit_state": "CLOSED"}
                for provider in PROVIDERS
            },
        )

    def test_dag_join_parallelizes_independent_direct_corps_and_reserves_before_handler(self):
        tasks = (
            task("T-G", provider_id="google", owner_corps="GOOGLE"),
            task("T-N", provider_id="nvidia", owner_corps="NVIDIA"),
            task("T-J", provider_id="groq", owner_corps="GROQ", depends_on=("T-G", "T-N")),
        )
        mission = plan(tasks)
        entered = []
        barrier = threading.Barrier(2)

        def handler(current):
            reservation_id = self.scheduler._reservation_ids[current.mission_id][current.task_id]
            reservation = self.ledger.get(reservation_id)
            self.assertEqual(reservation["state"], "reserved")
            entered.append(current.task_id)
            if current.task_id in {"T-G", "T-N"}:
                barrier.wait(timeout=2)
            return TaskResult(
                status="completed", summary=f"done {current.task_id}", response_version=1,
                requests_used=1, input_tokens=5, output_tokens=5,
            )

        report = self.scheduler.run(mission, {current.task_id: handler for current in tasks})
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["counts"]["completed"], 3)
        self.assertGreaterEqual(report["parallelism"]["max_parallel_observed"], 2)
        self.assertLessEqual(report["parallelism"]["max_concurrent_requests_per_provider"], 1)
        self.assertEqual(entered[-1], "T-J")
        self.assertEqual(report["safety"]["production_routing_changed"], False)

    def test_same_provider_never_runs_concurrently(self):
        tasks = (
            task("T-1", provider_id="google", owner_corps="GOOGLE"),
            task("T-2", provider_id="google", owner_corps="GOOGLE"),
        )
        current = 0
        maximum = 0
        guard = threading.Lock()

        def handler(item):
            nonlocal current, maximum
            with guard:
                current += 1
                maximum = max(maximum, current)
            time.sleep(0.02)
            with guard:
                current -= 1
            return TaskResult(status="completed", summary="ok", input_tokens=1, output_tokens=1)

        report = self.scheduler.run(plan(tasks), {item.task_id: handler for item in tasks})
        self.assertEqual(report["status"], "completed")
        self.assertEqual(maximum, 1)

    def test_mission_cancellation_does_not_affect_other_mission(self):
        first_started = threading.Event()
        release_first = threading.Event()
        first = task("T-A", mission_id="M-A", provider_id="google", owner_corps="GOOGLE")
        second = task("T-B", mission_id="M-B", provider_id="groq", owner_corps="GROQ")
        first_plan = plan((first,), mission_id="M-A")
        second_plan = plan((second,), mission_id="M-B")

        def first_handler(item):
            first_started.set()
            release_first.wait(timeout=2)
            return TaskResult(status="completed", summary="late result", input_tokens=1, output_tokens=1)

        def second_handler(item):
            return TaskResult(status="completed", summary="independent", input_tokens=1, output_tokens=1)

        result_holder = {}
        thread = threading.Thread(
            target=lambda: result_holder.update(self.scheduler.run_many(
                (first_plan, second_plan),
                {"M-A": {"T-A": first_handler}, "M-B": {"T-B": second_handler}},
            ))
        )
        thread.start()
        self.assertTrue(first_started.wait(timeout=2))
        self.scheduler.cancel_mission("M-A")
        release_first.set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result_holder["M-A"]["task_statuses"]["T-A"], "cancelled")
        self.assertEqual(result_holder["M-B"]["task_statuses"]["T-B"], "completed")

    def test_checkpoint_resume_skips_completed_task(self):
        first = task("T-A")
        second = task("T-B", depends_on=("T-A",))
        mission = plan((first, second))
        self.checkpoints.save("M-1", {
            "task_statuses": {"T-A": "completed", "T-B": "queued"},
            "reports": {"T-A": {"task_id": "T-A", "status": "completed", "summary": "saved", "response_version": 1}},
            "response_versions": {"T-A": 1, "T-B": 0},
            "reservation_ids": {}, "provider_state": {"cancelled": False},
        })
        calls = []

        def handler(item):
            calls.append(item.task_id)
            return TaskResult(status="completed", summary="resumed", input_tokens=1, output_tokens=1)

        report = self.scheduler.run(mission, {item.task_id: handler for item in mission.tasks}, resume=True)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(calls, ["T-B"])

    def test_provider_crash_with_unknown_usage_is_not_replayed(self):
        current = task("T-A")
        mission = plan((current,))
        calls = []

        def handler(item):
            calls.append(item.task_id)
            raise ProviderInterrupted("timeout with unknown usage")

        first = self.scheduler.run(mission, {"T-A": handler})
        self.assertEqual(first["task_statuses"]["T-A"], "blocked")
        self.assertEqual(calls, ["T-A"])
        second = self.scheduler.run(mission, {"T-A": handler}, resume=True)
        self.assertEqual(second["task_statuses"]["T-A"], "blocked")
        self.assertEqual(calls, ["T-A"])
        self.assertGreaterEqual(second["ledger"]["unsettled_count"], 1)

    def test_stale_response_is_rejected(self):
        current = task("T-A")
        mission = plan((current,))
        self.checkpoints.save("M-1", {
            "task_statuses": {"T-A": "queued"}, "reports": {}, "response_versions": {"T-A": 2},
            "reservation_ids": {}, "provider_state": {"cancelled": False},
        })

        def handler(item):
            return TaskResult(status="completed", summary="old", response_version=1, input_tokens=1, output_tokens=1)

        report = self.scheduler.run(mission, {"T-A": handler}, resume=True)
        self.assertEqual(report["task_statuses"]["T-A"], "failed")
        self.assertIn("STALE_RESPONSE_REJECTED", report["tasks"]["T-A"]["errors"])

    def test_depth_and_mutation_are_rejected(self):
        with self.assertRaises(SchedulerError):
            plan((task("T-A", depth=3),))
        mutation = task("T-A")
        mutation.side_effect_level = "mutation"
        with self.assertRaises(SchedulerError):
            plan((mutation,))

        orphan = task("T-A")
        orphan.parent_task_id = "MISSING"
        with self.assertRaises(SchedulerError):
            plan((orphan,))

    def test_unknown_cost_is_rejected_in_free_only_mode(self):
        unknown = task("T-A")
        unknown.estimated_cost = "UNKNOWN"
        with self.assertRaises(SchedulerError):
            plan((unknown,))

    def test_unhealthy_provider_is_blocked_without_affecting_other_provider(self):
        tasks = (
            task("T-G", provider_id="google", owner_corps="GOOGLE"),
            task("T-N", provider_id="nvidia", owner_corps="NVIDIA"),
        )
        self.scheduler.provider_states["google"] = {"health_status": "DEGRADED", "circuit_state": "OPEN"}
        called = []

        def handler(item):
            called.append(item.task_id)
            return TaskResult(status="completed", summary="ok", input_tokens=1, output_tokens=1)

        report = self.scheduler.run(plan(tasks), {item.task_id: handler for item in tasks})
        self.assertEqual(report["task_statuses"]["T-G"], "blocked")
        self.assertEqual(report["task_statuses"]["T-N"], "completed")
        self.assertEqual(called, ["T-N"])


if __name__ == "__main__":
    unittest.main()
