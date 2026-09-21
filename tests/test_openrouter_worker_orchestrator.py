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


def settled_google_outage(*, google_calls=3, nvidia_calls=0, revision_count=0):
    providers = {"google": google_calls}
    if nvidia_calls:
        providers["nvidia"] = nvidia_calls
    return {
        "status": "blocked",
        "runtime": {"stop_reason": "PROVIDER_INTERRUPTED", "revision_count": revision_count},
        "budget": {"requests_used": google_calls, "unsettled_requests": 0},
        "live_staging": {
            "operational": False,
            "executor_provider": "google",
            "reviewer_provider": "nvidia",
            "family_separation_pass": True,
            "external_model_calls": google_calls + nvidia_calls,
            "providers": providers,
        },
        "safety": {
            "paid_execution_count": 0,
            "paid_fallback_count": 0,
            "production_active": False,
            "secret_values_displayed": 0,
            "secret_values_logged": 0,
            "secret_values_persisted": 0,
            "secret_values_returned_to_model": 0,
        },
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
        self.assertTrue(result["next_action"].startswith("RESUME_SAME_PROJECT"))

    def test_ambiguous_google_interruption_never_bypasses_two_agent_gate(self):
        live = settled_google_outage()
        live["budget"]["unsettled_requests"] = 1
        with patch("scripts.openrouter_worker_orchestrator.run_multi_probe") as probe_call:
            result = run_pipeline(
                source_head="a" * 40,
                live_report=live,
                api_key="secret-placeholder",
                network_enabled=True,
            )
        probe_call.assert_not_called()
        self.assertEqual(result["state"], "WAITING_FOR_TWO_AGENT_COMPLETION")
        self.assertEqual(result["commander_gate_mode"], "PENDING")

    def test_settled_repeated_google_outage_can_bootstrap_workers_without_claiming_commander_acceptance(self):
        probe = {
            "status": "FREE_ACTIVE",
            "model_calls": 1,
            "results": [],
            "role_probe_candidates": {},
        }
        benchmark = {
            "status": "BENCHMARK_READY",
            "assignments": {
                "FAST_WORKER": {
                    "status": "ready_for_commander_review",
                    "model": "vendor/fast:free",
                    "score": 0.9,
                },
            },
        }
        project_loop = {
            "state": "PROJECT_BATCH_COMPLETE",
            "next_action": "WORK_INTEGRATE_PREDICTED_PROJECT",
            "completed_project_ids": ["openrouter-worker-army-v1"],
        }
        with patch("scripts.openrouter_worker_orchestrator.run_multi_probe", return_value=probe) as probe_call, patch(
            "scripts.openrouter_worker_orchestrator.run_benchmarks", return_value=benchmark
        ) as benchmark_call, patch(
            "scripts.openrouter_worker_orchestrator.run_continuous_project_loop", return_value=project_loop
        ) as project_call:
            result = run_pipeline(
                source_head="e" * 40,
                live_report=settled_google_outage(),
                api_key="secret-placeholder",
                network_enabled=True,
            )
        probe_call.assert_called_once()
        benchmark_call.assert_called_once()
        project_call.assert_called_once()
        self.assertEqual(result["commander_gate_mode"], "DEGRADED_WORKER_BOOTSTRAP")
        self.assertTrue(result["commander_acceptance_pending"])
        self.assertFalse(result.get("reviewer_already_participated", False))
        self.assertTrue(result["handoff"]["commander_acceptance_pending"])
        self.assertFalse(result["automatic_activation"])
        self.assertEqual(result["handoff"]["ready_role_count"], 1)
        self.assertEqual(result["next_action_after_commander_recovery"], "REVIEW_STAGED_WORKER_HANDOFF_WITH_GOOGLE_AND_NVIDIA")

    def test_settled_google_revision_failure_bootstraps_workers_without_replaying_nvidia(self):
        probe = {
            "status": "FREE_ACTIVE",
            "model_calls": 1,
            "results": [],
            "role_probe_candidates": {},
        }
        benchmark = {
            "status": "BENCHMARK_READY",
            "assignments": {
                "CODING_WORKER": {
                    "status": "ready_for_commander_review",
                    "model": "vendor/code:free",
                    "score": 0.93,
                },
            },
        }
        project_loop = {
            "state": "PROJECT_BATCH_COMPLETE",
            "next_action": "WORK_INTEGRATE_PREDICTED_PROJECT",
            "completed_project_ids": ["openrouter-worker-army-v1"],
        }
        with patch("scripts.openrouter_worker_orchestrator.run_multi_probe", return_value=probe) as probe_call, patch(
            "scripts.openrouter_worker_orchestrator.run_benchmarks", return_value=benchmark
        ) as benchmark_call, patch(
            "scripts.openrouter_worker_orchestrator.run_continuous_project_loop", return_value=project_loop
        ) as project_call:
            result = run_pipeline(
                source_head="f" * 40,
                live_report=settled_google_outage(google_calls=6, nvidia_calls=1, revision_count=1),
                api_key="secret-placeholder",
                network_enabled=True,
            )
        probe_call.assert_called_once()
        benchmark_call.assert_called_once()
        project_call.assert_called_once()
        self.assertEqual(result["commander_gate_mode"], "DEGRADED_WORKER_BOOTSTRAP")
        self.assertTrue(result["commander_acceptance_pending"])
        self.assertTrue(result["reviewer_already_participated"])
        self.assertEqual(result["handoff"]["ready_role_count"], 1)
        self.assertFalse(result["automatic_activation"])

    def test_nvidia_failure_without_revision_proof_does_not_bypass_gate(self):
        live = settled_google_outage(google_calls=3, nvidia_calls=1, revision_count=0)
        with patch("scripts.openrouter_worker_orchestrator.run_multi_probe") as probe_call:
            result = run_pipeline(
                source_head="g" * 40,
                live_report=live,
                api_key="secret-placeholder",
                network_enabled=True,
            )
        probe_call.assert_not_called()
        self.assertEqual(result["state"], "WAITING_FOR_TWO_AGENT_COMPLETION")

    def test_completed_two_agent_result_continues_through_project_boundary_without_user_action(self):
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
        project_loop = {
            "state": "PROJECT_BATCH_COMPLETE",
            "next_action": "WORK_INTEGRATE_PREDICTED_PROJECT",
            "completed_project_ids": [
                "openrouter-worker-army-v1",
                "openrouter-worker-canary-v1",
                "worker-routing-policy-v1",
                "worker-resilience-rehearsal-v1",
            ],
        }
        with patch("scripts.openrouter_worker_orchestrator.run_multi_probe", return_value=probe) as probe_call, patch(
            "scripts.openrouter_worker_orchestrator.run_benchmarks", return_value=benchmark
        ) as benchmark_call, patch(
            "scripts.openrouter_worker_orchestrator.run_continuous_project_loop", return_value=project_loop
        ) as project_call:
            result = run_pipeline(
                source_head="b" * 40,
                live_report=live_completed(),
                api_key="secret-placeholder",
                network_enabled=True,
            )
        probe_call.assert_called_once()
        benchmark_call.assert_called_once()
        project_call.assert_called_once()
        self.assertEqual(result["state"], "PROJECT_BATCH_COMPLETE")
        self.assertEqual(result["commander_gate_mode"], "TWO_AGENT_ACCEPTED")
        self.assertFalse(result["commander_acceptance_pending"])
        self.assertEqual(result["handoff"]["ready_role_count"], 2)
        self.assertFalse(result["automatic_activation"])
        self.assertEqual(result["next_action"], "WORK_INTEGRATE_PREDICTED_PROJECT")
        self.assertEqual(
            [event["stage"] for event in result["events"]],
            ["TWO_AGENT_RESULT_GATE", "OPENROUTER_PROBE", "WORKER_BENCHMARK", "COMMANDER_HANDOFF", "PROJECT_CONTINUATION"],
        )

    def test_missing_openrouter_secret_waits_without_restarting_project(self):
        result = run_pipeline(
            source_head="c" * 40,
            live_report=live_completed(),
            api_key="",
            network_enabled=True,
        )
        self.assertEqual(result["state"], "WAITING_FOR_OPENROUTER_SECRET")
        self.assertEqual(result["next_action"], "RESUME_SAME_PROJECT_WHEN_SECRET_IS_AVAILABLE")

    def test_no_benchmark_winner_preserves_same_project_resume(self):
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
        self.assertEqual(result["next_action"], "RESUME_SAME_PROJECT_WITH_EXISTING_PROBE_EVIDENCE")

    def test_jev_fast_lane_is_bounded_and_does_not_auto_activate(self):
        probe = {"status": "FREE_ACTIVE", "model_calls": 1, "results": [], "role_probe_candidates": {}}
        benchmark = {
            "status": "BENCHMARK_READY",
            "model_calls": 2,
            "assignments": {
                "CODING_WORKER": {
                    "status": "ready_for_commander_review",
                    "model": "vendor/code:free",
                    "score": 0.93,
                    "ranking": [
                        {"model": "vendor/code:free", "rank": 1, "score": 0.93},
                        {"model": "vendor/review:free", "rank": 2, "score": 0.88},
                    ],
                },
            },
        }
        jev = {
            "status": "JEV_FAST_DECISION_OK",
            "decision": {
                "workers": ["vendor/code:free"],
                "parallel": False,
                "fanout": 1,
                "execution_mode": "SINGLE",
                "independent_verification": False,
                "confidence": 0.94,
            },
        }
        project_loop = {"state": "PROJECT_BATCH_COMPLETE", "next_action": "DONE", "completed_project_ids": ["x"]}
        with patch("scripts.openrouter_worker_orchestrator.run_multi_probe", return_value=probe), patch(
            "scripts.openrouter_worker_orchestrator.run_benchmarks", return_value=benchmark
        ), patch(
            "scripts.openrouter_worker_orchestrator.jev_decide", return_value=jev
        ) as jev_call, patch(
            "scripts.openrouter_worker_orchestrator.run_continuous_project_loop", return_value=project_loop
        ):
            result = run_pipeline(
                source_head="h" * 40,
                live_report=live_completed(),
                api_key="secret-placeholder",
                network_enabled=True,
                jev_enabled=True,
            )
        jev_call.assert_called_once()
        self.assertEqual(result["scoped_paid_decision_calls"], 1)
        self.assertEqual(result["handoff"]["fast_lane_recommendation"]["selected_models"], ["vendor/code:free"])
        self.assertFalse(result["handoff"]["fast_lane_recommendation"]["automatic_activation"])
        self.assertFalse(result["automatic_activation"])


if __name__ == "__main__":
    unittest.main()
