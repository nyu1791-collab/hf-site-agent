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


if __name__ == "__main__":
    unittest.main()
