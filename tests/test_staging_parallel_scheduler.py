import threading
import time
import unittest

from scripts.mission_scheduler import MissionPlan, MissionReservationLedger, MissionTask, TaskResult
from scripts.staging_parallel_scheduler import StagingParallelMissionScheduler


PROVIDERS = ("google", "nvidia", "groq", "openrouter")


def _limits(requests=20, tokens=2_000):
    return {provider: {"requests": requests, "tokens": tokens} for provider in PROVIDERS}


def _task(task_id, *, provider_id="openrouter", owner_corps="NVIDIA"):
    return MissionTask(
        mission_id="M-STAGING-PARALLEL",
        task_id=task_id,
        parent_task_id=None,
        parent_agent_id="nvidia-engineering-commander",
        owner_corps=owner_corps,
        role="SPECIALIST",
        required_capabilities=("structured_output",),
        priority=0,
        risk_level="LOW",
        complexity_level=1,
        deadline=None,
        request_budget=1,
        token_budget=100,
        estimated_cost=0,
        idempotency_key=f"idem-{task_id}",
        response_version=1,
        delegation_depth=2 if provider_id == "openrouter" else 1,
        provider_id=provider_id,
        parallel_group="parallel-stage",
    )


def _plan(tasks):
    return MissionPlan(
        mission_id="M-STAGING-PARALLEL",
        tasks=tuple(tasks),
        max_total_requests=20,
        max_total_tokens=2_000,
        max_parallel=3,
        provider_request_budgets={provider: 20 for provider in PROVIDERS},
        provider_token_budgets={provider: 2_000 for provider in PROVIDERS},
    )


class StagingParallelMissionSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = StagingParallelMissionScheduler(
            MissionReservationLedger(provider_limits=_limits()),
            max_subordinate_parallel=3,
            provider_states={
                provider: {"health_status": "HEALTHY", "circuit_state": "CLOSED"}
                for provider in PROVIDERS
            },
        )

    def test_three_openrouter_subordinates_can_execute_together(self):
        tasks = [_task("T-A"), _task("T-B"), _task("T-C")]
        barrier = threading.Barrier(3)
        active = 0
        maximum = 0
        guard = threading.Lock()

        def handler(item):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            barrier.wait(timeout=2)
            time.sleep(0.01)
            with guard:
                active -= 1
            return TaskResult(status="completed", summary=f"done {item.task_id}", input_tokens=1, output_tokens=1)

        report = self.scheduler.run(_plan(tasks), {item.task_id: handler for item in tasks})
        self.assertEqual(report["status"], "completed")
        self.assertEqual(maximum, 3)
        self.assertGreaterEqual(report["parallelism"]["max_parallel_observed"], 3)
        self.assertEqual(report["parallelism"]["max_parallel_subordinate_workers"], 3)
        self.assertEqual(report["parallelism"]["max_concurrent_requests_per_provider"], 3)
        self.assertFalse(self.scheduler.production_parallel_routing_allowed)

    def test_direct_nvidia_requests_remain_serial(self):
        tasks = [_task("T-A", provider_id="nvidia"), _task("T-B", provider_id="nvidia")]
        active = 0
        maximum = 0
        guard = threading.Lock()

        def handler(item):
            nonlocal active, maximum
            with guard:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with guard:
                active -= 1
            return TaskResult(status="completed", summary=f"done {item.task_id}", input_tokens=1, output_tokens=1)

        report = self.scheduler.run(_plan(tasks), {item.task_id: handler for item in tasks})
        self.assertEqual(report["status"], "completed")
        self.assertEqual(maximum, 1)

    def test_parallelism_bound_rejects_four(self):
        with self.assertRaises(Exception):
            StagingParallelMissionScheduler(
                MissionReservationLedger(provider_limits=_limits()),
                max_subordinate_parallel=4,
                provider_states={
                    provider: {"health_status": "HEALTHY", "circuit_state": "CLOSED"}
                    for provider in PROVIDERS
                },
            )


if __name__ == "__main__":
    unittest.main()
