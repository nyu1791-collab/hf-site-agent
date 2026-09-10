import subprocess
import sys
import unittest

from scripts.organization_coordination import (
    OrganizationTask,
    PriorityTaskQueue,
    SharedBlackboard,
    WorkerCircuitBreaker,
    build_blackboard_from_council,
    parallel_batches,
    should_early_stop,
    tasks_conflict,
)


class PriorityQueueTests(unittest.TestCase):
    def test_critical_path_and_priority_ordering(self):
        now = [100.0]
        queue = PriorityTaskQueue(clock=lambda: now[0], aging_seconds=60)
        queue.put(OrganizationTask("bulk", "DOC", priority="BULK", critical_path_rank=0, enqueued_at=100.0))
        queue.put(OrganizationTask("critical-late", "SCHEDULER_DAG", priority="CRITICAL", critical_path_rank=2, enqueued_at=100.0))
        queue.put(OrganizationTask("critical-first", "FAILURE_RETRY", priority="CRITICAL", critical_path_rank=1, enqueued_at=100.0))
        self.assertEqual(queue.pop().task_id, "critical-first")
        self.assertEqual(queue.pop().task_id, "critical-late")
        self.assertEqual(queue.pop().task_id, "bulk")

    def test_aging_promotes_waiting_background_work(self):
        now = [300.0]
        queue = PriorityTaskQueue(clock=lambda: now[0], aging_seconds=60)
        queue.put(OrganizationTask("old", "DOC", priority="BACKGROUND", critical_path_rank=0, enqueued_at=0.0))
        queue.put(OrganizationTask("new", "DOC2", priority="NORMAL", critical_path_rank=50, enqueued_at=300.0))
        self.assertEqual(queue.pop().task_id, "old")


class ConflictPlannerTests(unittest.TestCase):
    def test_read_only_tasks_can_share_batch(self):
        left = OrganizationTask("a", "A", read_set=("a.py",))
        right = OrganizationTask("b", "B", read_set=("a.py",))
        self.assertFalse(tasks_conflict(left, right))
        self.assertEqual(len(parallel_batches([left, right])), 1)

    def test_overlapping_write_serializes(self):
        left = OrganizationTask("a", "A", write_set=("shared.py",))
        right = OrganizationTask("b", "B", read_set=("shared.py",))
        self.assertTrue(tasks_conflict(left, right))
        self.assertEqual(len(parallel_batches([left, right])), 2)


class BlackboardTests(unittest.TestCase):
    def test_duplicate_entry_is_reused(self):
        board = SharedBlackboard("M-1", "head")
        first = board.publish(kind="FACT", subject="x", payload={"v": 1}, source="a")
        second = board.publish(kind="FACT", subject="x", payload={"v": 1}, source="b")
        self.assertEqual(first, second)
        snapshot = board.snapshot()
        self.assertEqual(snapshot["entry_count"], 1)
        self.assertEqual(snapshot["duplicate_count"], 1)

    def test_council_compacts_success_and_failure(self):
        council = {
            "selected_model_count": 2,
            "primary_successful_lane_count": 1,
            "successful_lane_count": 1,
            "selected_models": [
                {"specialist_lane": "SCHEDULER_DAG"},
                {"specialist_lane": "PERFORMANCE_TELEMETRY"},
            ],
            "parallel_metrics": {"parallel_speedup": 2.0, "successful_tasks_per_ai_call": 0.5},
            "results": [
                {"specialist_lane": "SCHEDULER_DAG", "model": "a:free", "status": "COUNCIL_OK", "response": "ok", "phase": "PRIMARY"},
                {"specialist_lane": "PERFORMANCE_TELEMETRY", "model": "b:free", "status": "COUNCIL_FAILED", "error": "empty_visible_content", "finish_reason": "length", "phase": "PRIMARY"},
            ],
        }
        report = build_blackboard_from_council(council, source_head="abc")
        self.assertEqual(report["status"], "BLACKBOARD_READY")
        kinds = {entry["kind"] for entry in report["entries"]}
        self.assertIn("RESULT", kinds)
        self.assertIn("FAILURE", kinds)
        self.assertIn("OPEN_TASK", kinds)
        self.assertFalse(report["early_stop"]["stop"])

    def test_direct_script_entrypoint_bootstraps_repository_package(self):
        completed = subprocess.run(
            [sys.executable, "scripts/organization_coordination.py", "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--council", completed.stdout)


class WorkerCircuitTests(unittest.TestCase):
    def test_worker_opens_then_half_open_probe_closes_on_success(self):
        now = [0.0]
        breaker = WorkerCircuitBreaker(failure_threshold=2, cooldown_seconds=10, clock=lambda: now[0])
        self.assertTrue(breaker.allow("worker"))
        breaker.record_failure("worker")
        self.assertTrue(breaker.allow("worker"))
        breaker.record_failure("worker")
        self.assertFalse(breaker.allow("worker"))
        now[0] = 11.0
        self.assertTrue(breaker.allow("worker"))
        self.assertFalse(breaker.allow("worker"))
        breaker.record_success("worker")
        self.assertTrue(breaker.allow("worker"))
        self.assertEqual(breaker.snapshot()["worker"]["state"], "CLOSED")


class EarlyStopTests(unittest.TestCase):
    def test_all_required_lanes_can_stop(self):
        rows = [
            {"specialist_lane": "SCHEDULER_DAG", "status": "COUNCIL_OK"},
            {"specialist_lane": "FAILURE_RETRY", "status": "COUNCIL_OK"},
        ]
        report = should_early_stop(rows, required_lanes=["SCHEDULER_DAG", "FAILURE_RETRY"])
        self.assertTrue(report["stop"])
        self.assertEqual(report["reason"], "ALL_REQUIRED_LANES_COMPLETE")

    def test_validation_does_not_hide_critical_gap(self):
        rows = [{"specialist_lane": "TEST_VALIDATION", "status": "COUNCIL_OK"}]
        report = should_early_stop(
            rows,
            required_lanes=["SCHEDULER_DAG", "TEST_VALIDATION"],
            deterministic_validation_passed=True,
        )
        self.assertFalse(report["stop"])
        self.assertEqual(report["unresolved_critical_lanes"], ["SCHEDULER_DAG"])


if __name__ == "__main__":
    unittest.main()
