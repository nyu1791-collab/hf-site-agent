from pathlib import Path
import tempfile
import unittest

from scripts.autonomous_mission import (
    AutonomousBounds,
    AutonomousMissionRuntime,
    TaskLoopCallbacks,
)
from scripts.mission_scheduler import (
    MissionCheckpointStore,
    MissionPlan,
    MissionReservationLedger,
    MissionTask,
    ProviderInterrupted,
)


PROVIDERS = ("google", "nvidia", "groq", "openrouter")


def provider_limits(requests=30, tokens=10_000):
    return {provider: {"requests": requests, "tokens": tokens} for provider in PROVIDERS}


def make_task(task_id, *, mission_id="M-AUTO", provider_id="google", owner_corps="GOOGLE", depends_on=(), budget=8):
    return MissionTask(
        mission_id=mission_id,
        task_id=task_id,
        parent_task_id=None,
        parent_agent_id=f"{owner_corps.lower()}-commander",
        owner_corps=owner_corps,
        role="SPECIALIST",
        required_capabilities=("structured_output",),
        priority=1,
        risk_level="LOW",
        complexity_level=2,
        deadline=None,
        request_budget=budget,
        token_budget=1_000,
        estimated_cost=0,
        idempotency_key=f"idem-{mission_id}-{task_id}",
        response_version=1,
        delegation_depth=1,
        provider_id=provider_id,
        depends_on=tuple(depends_on),
        parallel_group="stage-a" if not depends_on else "stage-b",
        side_effect_level="read_only_draft",
    )


def make_plan(tasks, *, mission_id="M-AUTO", max_requests=16, max_tokens=2_000):
    return MissionPlan(
        mission_id=mission_id,
        tasks=tuple(tasks),
        max_total_requests=max_requests,
        max_total_tokens=max_tokens,
        max_parallel=3,
        max_delegation_depth=2,
        provider_request_budgets={provider: max_requests for provider in PROVIDERS},
        provider_token_budgets={provider: max_tokens for provider in PROVIDERS},
        free_only=True,
    )


def callback_result(summary, *, requests_used=1, model="fixture/model", **extra):
    return {
        "summary": summary,
        "model": model,
        "requests_used": requests_used,
        "input_tokens": 10 if requests_used else 0,
        "output_tokens": 10 if requests_used else 0,
        **extra,
    }


class AutonomousMissionRuntimeTests(unittest.TestCase):
    def _runtime(self, directory, *, bounds=None):
        ledger = MissionReservationLedger(Path(directory) / "ledger.json", provider_limits=provider_limits())
        return AutonomousMissionRuntime(
            ledger,
            checkpoints=MissionCheckpointStore(Path(directory) / "checkpoints"),
            provider_states={
                provider: {"health_status": "HEALTHY", "circuit_state": "CLOSED"}
                for provider in PROVIDERS
            },
            bounds=bounds,
        )

    def test_task_loop_revises_then_mission_loop_dispatches_next_task(self):
        with tempfile.TemporaryDirectory() as directory:
            first = make_task("T-A", provider_id="google", owner_corps="GOOGLE", budget=8)
            second = make_task("T-B", provider_id="groq", owner_corps="GROQ", depends_on=("T-A",), budget=8)
            mission = make_plan((first, second))
            calls = []

            def execute(task, context):
                calls.append((task.task_id, context["phase"], context["iteration_count"]))
                return callback_result("needs revision" if task.task_id == "T-B" else "requirements mapped", proposal="draft")

            def revise(task, context):
                calls.append((task.task_id, context["phase"], context["iteration_count"]))
                return callback_result("revised proposal", proposal="accepted draft")

            def validate(task, context):
                return callback_result("schema valid", requests_used=0, passed=True)

            def review(task, context):
                is_first_bad = task.task_id == "T-B" and context["candidate"].get("summary") == "needs revision"
                return callback_result(
                    "review failed" if is_first_bad else "review passed",
                    decision="FAIL" if is_first_bad else "PASS",
                    failure_signature="missing_acceptance_test" if is_first_bad else "",
                    review_findings=["add acceptance test"] if is_first_bad else [],
                )

            callbacks = {
                task.task_id: TaskLoopCallbacks(
                    executor=execute,
                    validator=validate,
                    reviewer=review,
                    reviser=revise,
                )
                for task in mission.tasks
            }
            report = self._runtime(directory).run(mission, callbacks)

        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["counts"]["completed"], 2)
        self.assertTrue(report["runtime"]["task_loop"])
        self.assertTrue(report["runtime"]["mission_loop"])
        self.assertTrue(report["runtime"]["next_task_auto_dispatch"])
        self.assertFalse(report["runtime"]["user_continue_required"])
        task_result = report["tasks"]["T-B"]["result"]["autonomous"]
        self.assertEqual(task_result["revision_count"], 1)
        self.assertEqual(task_result["iteration_count"], 2)
        self.assertEqual(calls[-1][0], "T-B")

    def test_repeated_failure_enters_bounded_task_replan_and_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            task = make_task("T-A", budget=10)
            mission = make_plan((task,), max_requests=10, max_tokens=2_000)
            executor_calls = []

            def execute(current, context):
                executor_calls.append(context["replan_count"])
                return callback_result("recovered after replan" if context["replan_count"] else "same failure", proposal="candidate")

            def revise(current, context):
                return callback_result("recovered after replan" if context["replan_count"] else "same failure", proposal="candidate")

            def validate(current, context):
                return callback_result("schema valid", requests_used=0, passed=True)

            def review(current, context):
                passed = context["candidate"].get("summary") == "recovered after replan"
                return callback_result(
                    "pass" if passed else "same review finding",
                    decision="PASS" if passed else "FAIL",
                    failure_signature="same_failure" if not passed else "",
                )

            def replan(current, context):
                return callback_result("alternate strategy selected", strategy="alternate")

            callbacks = {
                "T-A": TaskLoopCallbacks(
                    executor=execute,
                    validator=validate,
                    reviewer=review,
                    reviser=revise,
                    replanner=replan,
                )
            }
            report = self._runtime(
                directory,
                bounds=AutonomousBounds(max_iterations=6, max_revisions=3, max_replans=2, max_requests=10, max_tokens=2_000),
            ).run(mission, callbacks)

        self.assertEqual(report["status"], "completed")
        state = report["tasks"]["T-A"]["result"]["autonomous"]
        self.assertEqual(state["replan_count"], 1)
        self.assertEqual(state["revision_count"], 1)
        self.assertTrue(report["runtime"]["replan_loop"])
        self.assertLessEqual(state["iteration_count"], 6)
        self.assertEqual(executor_calls, [0])

    def test_limits_stop_without_infinite_revision_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            task = make_task("T-A", budget=6)
            mission = make_plan((task,), max_requests=6, max_tokens=1_000)

            def always_bad(current, context):
                return callback_result("bad draft", proposal="bad")

            callbacks = {
                "T-A": TaskLoopCallbacks(
                    executor=always_bad,
                    reviser=always_bad,
                    validator=lambda current, context: callback_result("valid", requests_used=0, passed=True),
                    reviewer=lambda current, context: callback_result(
                        "rejected", decision="FAIL", failure_signature="persistent_failure"
                    ),
                )
            }
            report = self._runtime(
                directory,
                bounds=AutonomousBounds(max_iterations=3, max_revisions=2, max_replans=0, max_requests=6, max_tokens=1_000),
            ).run(mission, callbacks)

        self.assertEqual(report["status"], "blocked")
        state = report["tasks"]["T-A"]["result"]["autonomous"]
        self.assertLessEqual(state["iteration_count"], 3)
        self.assertEqual(report["runtime"]["user_continue_required"], False)
        self.assertEqual(report["runtime"]["stop_reason"], "MAX_REPLANS_REACHED")

    def test_provider_interruption_retains_uncertain_usage_and_resume_does_not_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            task = make_task("T-A", budget=4)
            mission = make_plan((task,), max_requests=4, max_tokens=1_000)
            calls = []

            def interrupted(current, context):
                calls.append(current.task_id)
                raise ProviderInterrupted("provider timeout with unknown usage")

            callbacks = {
                "T-A": TaskLoopCallbacks(
                    executor=interrupted,
                    validator=lambda current, context: callback_result("not reached", requests_used=0, passed=True),
                    reviewer=lambda current, context: callback_result("not reached", decision="PASS"),
                )
            }
            runtime = self._runtime(directory, bounds=AutonomousBounds(max_requests=4, max_tokens=1_000))
            first = runtime.run(mission, callbacks)
            second = runtime.run(mission, callbacks, resume=True)

        self.assertEqual(first["status"], "blocked")
        self.assertEqual(first["budget"]["unsettled_requests"], 1)
        self.assertTrue(first["runtime"]["provider_interruption_resume"])
        self.assertEqual(second["status"], "blocked")
        self.assertEqual(calls, ["T-A"])

    def test_unsettled_provider_failure_does_not_trigger_mission_replan(self):
        with tempfile.TemporaryDirectory() as directory:
            task = make_task("T-A", budget=4)
            mission = make_plan((task,), max_requests=4, max_tokens=1_000)
            replan_calls = []

            def interrupted(current, context):
                raise ProviderInterrupted("provider timeout with unknown usage")

            callbacks = {
                "T-A": TaskLoopCallbacks(
                    executor=interrupted,
                    validator=lambda current, context: callback_result("not reached", requests_used=0, passed=True),
                    reviewer=lambda current, context: callback_result("not reached", decision="PASS"),
                )
            }

            def replan(current, context):
                replan_calls.append(current.mission_id)
                return current

            report = self._runtime(directory).run(mission, callbacks, mission_replanner=replan)

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(replan_calls, [])
        self.assertEqual(report["budget"]["unsettled_requests"], 1)

    def test_task_budget_stops_before_an_extra_provider_call(self):
        with tempfile.TemporaryDirectory() as directory:
            task = make_task("T-A", budget=1)
            mission = make_plan((task,), max_requests=1, max_tokens=1_000)
            reviewer_calls = []

            callbacks = {
                "T-A": TaskLoopCallbacks(
                    executor=lambda current, context: callback_result("draft"),
                    validator=lambda current, context: callback_result("valid", requests_used=0, passed=True),
                    reviewer=lambda current, context: reviewer_calls.append(True),
                )
            }
            report = self._runtime(directory).run(mission, callbacks)

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(reviewer_calls, [])
        self.assertEqual(report["budget"]["unsettled_requests"], 0)
        self.assertEqual(report["tasks"]["T-A"]["requests_used"], 1)

    def test_mission_replanner_rebuilds_generation_without_rerunning_completed_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            task = make_task("T-A", budget=4)
            mission = make_plan((task,), max_requests=8, max_tokens=2_000)
            executor_calls = 0

            def execute(current, context):
                nonlocal executor_calls
                executor_calls += 1
                return callback_result("good" if executor_calls > 1 else "bad", proposal="candidate")

            callbacks = {
                "T-A": TaskLoopCallbacks(
                    executor=execute,
                    validator=lambda current, context: callback_result("valid", requests_used=0, passed=True),
                    reviewer=lambda current, context: callback_result(
                        "accepted" if context["candidate"].get("summary") == "good" else "rejected",
                        decision="PASS" if context["candidate"].get("summary") == "good" else "FAIL",
                        failure_signature="wrong_assumption" if context["candidate"].get("summary") != "good" else "",
                    ),
                )
            }

            proposed_task = make_task("T-A", mission_id="PROPOSED", budget=4)
            proposed = make_plan((proposed_task,), mission_id="PROPOSED", max_requests=8, max_tokens=2_000)
            report = self._runtime(
                directory,
                bounds=AutonomousBounds(max_iterations=4, max_revisions=0, max_replans=1, max_requests=8, max_tokens=2_000),
            ).run(mission, callbacks, mission_replanner=lambda current, context: proposed)

        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["counts"]["generations"], 2)
        self.assertEqual(report["runtime"]["execution_generation"], 2)
        self.assertEqual(executor_calls, 2)
        self.assertEqual(report["tasks"]["T-A"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
