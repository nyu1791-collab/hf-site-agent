import unittest

from scripts.reconcile_agent_organization import (
    candidates_from_openrouter_reports,
    merge_candidates,
    reconcile,
)
from scripts.replaceable_agent_organization import load_config


class ReconcileAgentOrganizationTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_openrouter_requires_exact_active_probe_and_benchmark(self):
        probe = {
            "results": [
                {
                    "status": "FREE_ACTIVE",
                    "requested_model": "good:free",
                    "response_model": "good:free",
                    "usage_cost": "0",
                    "credits_unchanged": True,
                    "fallback_used": False,
                    "provider_allow_fallbacks": False,
                },
                {
                    "status": "FREE_ACTIVE",
                    "requested_model": "mismatch:free",
                    "response_model": "other:free",
                    "usage_cost": "0",
                    "credits_unchanged": True,
                    "fallback_used": False,
                    "provider_allow_fallbacks": False,
                },
            ]
        }
        benchmark = {
            "records": [
                {"status": "BENCHMARK_OK", "worker_role": "CODING_WORKER", "model": "good:free", "task_quality": 1.0, "latency_ms": 4000},
                {"status": "BENCHMARK_OK", "worker_role": "FAST_WORKER", "model": "good:free", "task_quality": 1.0, "latency_ms": 2000},
                {"status": "BENCHMARK_OK", "worker_role": "CODING_WORKER", "model": "mismatch:free", "task_quality": 1.0, "latency_ms": 1000},
            ]
        }
        rows = candidates_from_openrouter_reports(probe, benchmark)
        self.assertEqual([row["model"] for row in rows], ["good:free"])
        self.assertEqual(rows[0]["role_scores"]["CODING_WORKER"], 1.0)
        self.assertTrue(rows[0]["free_verified"])

    def test_cross_provider_portfolio_fills_roles_by_measured_fit(self):
        probe = {
            "results": [{
                "status": "FREE_ACTIVE",
                "requested_model": "reviewer:free",
                "response_model": "reviewer:free",
                "usage_cost": "0",
                "credits_unchanged": True,
                "fallback_used": False,
                "provider_allow_fallbacks": False,
            }]
        }
        benchmark = {
            "records": [
                {"status": "BENCHMARK_OK", "worker_role": "REVIEW_WORKER", "model": "reviewer:free", "task_quality": 1.0, "latency_ms": 3500},
                {"status": "BENCHMARK_OK", "worker_role": "GENERAL_WORKER", "model": "reviewer:free", "task_quality": 0.9, "latency_ms": 3600},
            ]
        }
        direct = {
            "provider_status": {"zai": {"free_verified": True}},
            "rankings": [{
                "provider": "zai",
                "model": "glm-stable",
                "free_admitted": True,
                "task_count": 3,
                "task_success_count": 3,
                "weighted_quality_score": 1.0,
                "average_latency_ms": 2500,
                "task_scores": {"JSON": 1.0, "CODING": 1.0, "FAST": 1.0},
            }],
            "results": [],
        }
        report = reconcile(
            config=self.config,
            openrouter_probe=probe,
            openrouter_benchmark=benchmark,
            direct_free_report=direct,
        )
        self.assertEqual(report["candidate_provider_counts"], {"openrouter": 1, "zai": 1})
        self.assertTrue(report["model_names_are_replaceable"])
        self.assertTrue(report["role_slots_are_stable"])
        self.assertEqual(report["new_model_path"], "PROBE_BENCHMARK_SHADOW_CANARY_REPLACE")
        self.assertEqual(report["assignments"]["CODE_EXECUTOR"]["model"], "glm-stable")
        self.assertEqual(report["assignments"]["QA_VALIDATOR"]["model"], "reviewer:free")

    def test_new_better_model_replaces_incumbent_after_evidence_margin(self):
        direct = {
            "provider_status": {"zai": {"free_verified": True}},
            "rankings": [{
                "provider": "zai",
                "model": "new-glm",
                "free_admitted": True,
                "task_count": 3,
                "task_success_count": 3,
                "weighted_quality_score": 1.0,
                "average_latency_ms": 2000,
                "task_scores": {"JSON": 1.0, "CODING": 1.0, "FAST": 1.0},
            }],
            "results": [],
        }
        incumbents = {
            "assignments": {
                "CODE_EXECUTOR": {
                    "status": "ASSIGNED",
                    "provider": "openrouter",
                    "model": "old:free",
                    "free_verified": True,
                    "samples": 5,
                    "successes": 4,
                    "success_rate": 0.8,
                    "weighted_quality_score": 0.7,
                    "quality_score": 0.7,
                    "average_latency_ms": 15000,
                    "role_scores": {"CODING_WORKER": 0.7, "CODING": 0.7},
                    "roles": ["CODING_WORKER"],
                }
            }
        }
        report = reconcile(config=self.config, direct_free_report=direct, incumbent_report=incumbents)
        row = report["assignments"]["CODE_EXECUTOR"]
        self.assertEqual(row["model"], "new-glm")
        self.assertEqual(row["swap_decision"]["decision"], "REPLACE")

    def test_one_sample_challenger_is_shadow_canary_not_churn(self):
        incumbent = {
            "provider": "openrouter",
            "model": "stable:free",
            "free_verified": True,
            "samples": 6,
            "successes": 6,
            "success_rate": 1.0,
            "quality_score": 0.75,
            "weighted_quality_score": 0.75,
            "average_latency_ms": 9000,
            "role_scores": {"CODING_WORKER": 0.75, "CODING": 0.75},
            "roles": ["CODING_WORKER"],
        }
        direct = {
            "provider_status": {"zai": {"free_verified": True}},
            "rankings": [{
                "provider": "zai",
                "model": "brand-new",
                "free_admitted": True,
                "task_count": 1,
                "task_success_count": 1,
                "weighted_quality_score": 1.0,
                "average_latency_ms": 1000,
                "task_scores": {"JSON": 1.0, "CODING": 1.0, "FAST": 1.0},
            }],
            "results": [],
        }
        report = reconcile(
            config=self.config,
            direct_free_report=direct,
            incumbent_report={"assignments": {"CODE_EXECUTOR": incumbent}},
        )
        decision = report["assignments"]["CODE_EXECUTOR"]["swap_decision"]
        self.assertEqual(decision["decision"], "SHADOW_CANARY")
        self.assertEqual(report["assignments"]["CODE_EXECUTOR"]["model"], "stable:free")

    def test_merge_candidates_deduplicates_same_provider_model(self):
        rows = merge_candidates(
            [{"provider": "zai", "model": "m", "samples": 1, "role_scores": {"FAST": 1.0}}],
            [{"provider": "zai", "model": "m", "samples": 3, "role_scores": {"CODING": 0.9}}],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["samples"], 3)
        self.assertEqual(rows[0]["role_scores"]["FAST"], 1.0)
        self.assertEqual(rows[0]["role_scores"]["CODING"], 0.9)


if __name__ == "__main__":
    unittest.main()
