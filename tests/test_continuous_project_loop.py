import unittest
from unittest.mock import patch

from scripts.continuous_project_loop import (
    AUTO_NEXT_SAFE,
    PROJECT_BOUNDARY,
    build_routing_policy,
    run_continuous_project_loop,
)


def openrouter_ready():
    return {
        "state": "PROJECT_COMPLETE",
        "handoff": {
            "ready_role_count": 1,
            "selected_workers": {
                "CODING_WORKER": {"model": "vendor/code:free", "score": 0.91},
            },
        },
        "probe": {
            "status": "FREE_ACTIVE",
            "results": [
                {
                    "status": "FREE_ACTIVE",
                    "requested_model": "vendor/code:free",
                    "response_model": "vendor/code:free",
                    "fallback_used": False,
                }
            ],
        },
        "benchmark": {
            "rankings": {
                "CODING_WORKER": [
                    {"model": "vendor/code:free", "score": 0.91},
                    {"model": "vendor/standby:free", "score": 0.84},
                ]
            }
        },
    }


class ContinuousProjectLoopTests(unittest.TestCase):
    def test_project_boundary_predicts_next_project_without_running_it(self):
        result = run_continuous_project_loop(
            source_head="a" * 40,
            openrouter_report=openrouter_ready(),
            api_key="secret-placeholder",
            network_enabled=True,
            mode=PROJECT_BOUNDARY,
        )
        self.assertEqual(result["state"], "PROJECT_BOUNDARY_REACHED")
        self.assertEqual(result["completed_project_ids"], ["openrouter-worker-army-v1"])
        self.assertEqual(result["next_project_candidates"][0]["project_id"], "openrouter-worker-canary-v1")

    def test_auto_mode_runs_three_safe_projects_then_predicts_integrator_project(self):
        canary = {
            "status": "CANARY_READY",
            "results": {
                "CODING_WORKER": {"status": "CANARY_OK", "model": "vendor/code:free"},
            },
        }
        with patch("scripts.continuous_project_loop.run_worker_canary", return_value=canary) as canary_call:
            result = run_continuous_project_loop(
                source_head="b" * 40,
                openrouter_report=openrouter_ready(),
                api_key="secret-placeholder",
                network_enabled=True,
                mode=AUTO_NEXT_SAFE,
                max_auto_projects=3,
            )
        canary_call.assert_called_once()
        self.assertEqual(result["state"], "PROJECT_BATCH_COMPLETE")
        self.assertIn("openrouter-worker-canary-v1", result["completed_project_ids"])
        self.assertIn("worker-routing-policy-v1", result["completed_project_ids"])
        self.assertIn("worker-resilience-rehearsal-v1", result["completed_project_ids"])
        self.assertEqual(result["next_project_candidates"][0]["project_id"], "quota-observability-hardening-v1")
        self.assertEqual(result["next_project_candidates"][0]["execution_class"], "INTEGRATOR_REQUIRED")
        self.assertEqual(result["next_action"], "WORK_INTEGRATE_PREDICTED_PROJECT")
        self.assertFalse(result["paid_fallback"])
        self.assertFalse(result["external_repository_write"])

    def test_external_dependency_checkpoints_same_project_instead_of_advancing(self):
        result = run_continuous_project_loop(
            source_head="c" * 40,
            openrouter_report=openrouter_ready(),
            api_key="",
            network_enabled=True,
            mode=AUTO_NEXT_SAFE,
        )
        self.assertEqual(result["state"], "PROJECT_CHECKPOINTED")
        self.assertEqual(result["active_project_id"], "openrouter-worker-canary-v1")
        self.assertTrue(result["next_action"].startswith("RESUME_SAME_PROJECT"))
        self.assertEqual(result["completed_project_ids"], ["openrouter-worker-army-v1"])

    def test_routing_policy_never_enables_automatic_fallback(self):
        canary = {
            "status": "CANARY_READY",
            "results": {
                "CODING_WORKER": {"status": "CANARY_OK", "model": "vendor/code:free"},
            },
        }
        policy = build_routing_policy(openrouter_ready(), canary)
        role = policy["roles"]["CODING_WORKER"]
        self.assertEqual(policy["status"], "ROUTING_POLICY_READY")
        self.assertFalse(role["automatic_fallback"])
        self.assertEqual(role["fallback_rule"], "BLOCK_AND_RESELECT_FROM_FRESH_EXACT_FREE_EVIDENCE")
        self.assertEqual(role["standby_candidates"][0]["status"], "STANDBY_REQUIRES_FRESH_EXACT_FREE_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
