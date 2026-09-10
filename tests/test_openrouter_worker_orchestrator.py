import unittest
from unittest.mock import patch

from scripts.openrouter_worker_orchestrator import run_pipeline


def live_completed():
    return {
        "status": "completed",
        "live_staging": {"operational": True, "live_agent_count": 2},
        "paid_execution_count": 0,
        "paid_fallback_count": 0,
        "production_active": False,
    }


class OpenRouterWorkerOrchestratorTests(unittest.TestCase):
    def test_blocked_two_agent_result_does_not_probe_openrouter(self):
        result = run_pipeline(
            source_head="a" * 40,
            live_report={"status": "blocked", "stop_reason": "provider_unavailable"},
            api_key="secret-placeholder",
            network_enabled=True,
        )
        self.assertEqual(result["state"], "WAITING_FOR_TWO_AGENT_COMPLETION")
        self.assertEqual(result["probe"], {})
        self.assertTrue(result["next_action"].startswith("RESUME_SAME_MISSION"))

    def test_completed_two_agent_result_continues_without_user_action(self):
        probe = {
            "status": "FREE_ACTIVE",
            "model_calls": 2,
            "results": [],
            "role_probe_candidates": {},
        }
        benchmark = {
            "status": "BENCHMARK_READY",
            "assignments": {
                "CODING_WORKER": {
                    "status": "ready_for_commander_review",
                    "model": "vendor/code:free",
                    "score": 0.91,
                },
                "FAST_WORKER": {
                    "status": "ready_for_commander_review",
                    "model": "vendor/fast:free",
                    "score": 0.88,
                },
            },
        }
        with patch("scripts.openrouter_worker_orchestrator.run_multi_probe", return_value=probe) as probe_call, patch(
            "scripts.openrouter_worker_orchestrator.run_benchmarks", return_value=benchmark
        ) as benchmark_call:
            result = run_pipeline(
                source_head="b" * 40,
                live_report=live_completed(),
                api_key="secret-placeholder",
                network_enabled=True,
            )
        probe_call.assert_called_once()
        benchmark_call.assert_called_once()
        self.assertEqual(result["state"], "READY_FOR_WORK_INTEGRATION")
        self.assertEqual(result["handoff"]["ready_role_count"], 2)
        self.assertFalse(result["automatic_activation"])
        self.assertEqual(
            [event["stage"] for event in result["events"]],
            ["TWO_AGENT_RESULT_GATE", "OPENROUTER_PROBE", "WORKER_BENCHMARK", "COMMANDER_HANDOFF"],
        )

    def test_missing_openrouter_secret_waits_without_restarting_mission(self):
        result = run_pipeline(
            source_head="c" * 40,
            live_report=live_completed(),
            api_key="",
            network_enabled=True,
        )
        self.assertEqual(result["state"], "WAITING_FOR_OPENROUTER_SECRET")
        self.assertEqual(result["next_action"], "RESUME_SAME_MISSION_WHEN_SECRET_IS_AVAILABLE")

    def test_no_benchmark_winner_preserves_same_mission_resume(self):
        probe = {"status": "FREE_ACTIVE", "model_calls": 1, "results": [], "role_probe_candidates": {}}
        benchmark = {"status": "COMPLETED_WITH_BLOCKS", "assignments": {}}
        with patch("scripts.openrouter_worker_orchestrator.run_multi_probe", return_value=probe), patch(
            "scripts.openrouter_worker_orchestrator.run_benchmarks", return_value=benchmark
        ):
            result = run_pipeline(
                source_head="d" * 40,
                live_report=live_completed(),
                api_key="secret-placeholder",
                network_enabled=True,
            )
        self.assertEqual(result["state"], "WAITING_FOR_BENCHMARK_RECOVERY")
        self.assertEqual(result["next_action"], "RESUME_SAME_MISSION_WITH_EXISTING_PROBE_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
