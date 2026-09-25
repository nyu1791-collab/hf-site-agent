import unittest

from scripts.multi_agent_execution_schedule import build_dispatch_schedule


def admitted_plan():
    return {
        "status": "READY",
        "final_execution_admission": {"status": "PASS"},
    }


class MultiAgentExecutionScheduleTests(unittest.TestCase):
    def test_releases_ready_critical_path_work_before_unrelated_batch_tail(self):
        schedule = build_dispatch_schedule(
            [
                {"task_id": "background"},
                {"task_id": "critical", "critical_path_priority": 10, "user_visible": True},
                {"task_id": "dependent", "depends_on": ["critical"]},
            ],
            {"background": admitted_plan(), "critical": admitted_plan(), "dependent": admitted_plan()},
            max_parallel_tasks=1,
        )
        self.assertEqual(schedule["initial_dispatch_waves"], [["critical"], ["background"]])
        self.assertEqual(schedule["waiting_for_verified_dependencies"][0]["task_id"], "dependent")

    def test_never_releases_plan_that_failed_admission(self):
        schedule = build_dispatch_schedule(
            [{"task_id": "blocked"}],
            {"blocked": {"status": "READY", "final_execution_admission": {"status": "BLOCKED"}}},
        )
        self.assertEqual(schedule["initial_dispatch_waves"], [])
        self.assertEqual(schedule["blocked"][0]["reason"], "PLAN_NOT_ADMITTED")

    def test_builds_full_weighted_critical_path_release_plan(self):
        schedule = build_dispatch_schedule(
            [
                {"task_id": "short_root", "estimated_duration_ms": 10},
                {"task_id": "long_root", "estimated_duration_ms": 10},
                {"task_id": "long_leaf", "depends_on": ["long_root"], "estimated_duration_ms": 100},
            ],
            {task_id: admitted_plan() for task_id in ("short_root", "long_root", "long_leaf")},
            max_parallel_tasks=1,
        )
        self.assertEqual(schedule["schema_version"], "multi-agent-execution-schedule-v2")
        self.assertEqual(schedule["initial_dispatch_waves"], [["long_root"], ["short_root"]])
        self.assertEqual(
            schedule["planned_dependency_release_waves"],
            [["long_root"], ["long_leaf"], ["short_root"]],
        )
        self.assertGreater(
            schedule["critical_path_score_ms"]["long_root"],
            schedule["critical_path_score_ms"]["short_root"],
        )

    def test_cycles_unknown_dependencies_and_duplicates_fail_closed(self):
        schedule = build_dispatch_schedule(
            [
                {"task_id": "a", "depends_on": ["b"]},
                {"task_id": "b", "depends_on": ["a"]},
                {"task_id": "unknown", "depends_on": ["outside"]},
                {"task_id": "dup"},
                {"task_id": "dup"},
            ],
            {task_id: admitted_plan() for task_id in ("a", "b", "unknown", "dup")},
        )
        self.assertEqual(schedule["initial_dispatch_waves"], [])
        self.assertEqual(schedule["dependency_cycle_task_ids"], ["a", "b"])
        reasons = {(row["task_id"], row["reason"]) for row in schedule["blocked"]}
        self.assertIn(("a", "DEPENDENCY_CYCLE"), reasons)
        self.assertIn(("b", "DEPENDENCY_CYCLE"), reasons)
        self.assertIn(("unknown", "UNKNOWN_DEPENDENCY"), reasons)
        self.assertIn(("dup", "DUPLICATE_TASK_ID"), reasons)

    def test_parallel_task_ceiling_is_never_raised_above_three(self):
        task_ids = [f"t{index}" for index in range(5)]
        schedule = build_dispatch_schedule(
            [{"task_id": task_id} for task_id in task_ids],
            {task_id: admitted_plan() for task_id in task_ids},
            max_parallel_tasks=99,
        )
        self.assertEqual(schedule["max_parallel_tasks"], 3)
        self.assertTrue(all(len(wave) <= 3 for wave in schedule["initial_dispatch_waves"]))


if __name__ == "__main__":
    unittest.main()
