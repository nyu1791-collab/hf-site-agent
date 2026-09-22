import unittest
from unittest.mock import patch

from scripts.jev_routing_coordinator import coordinate, coordinate_many


def entry(model, *, context=131072):
    return {
        "id": model,
        "pricing": {"prompt": "0", "completion": "0"},
        "context_length": context,
        "supported_parameters": ["tools", "tool_choice"],
        "architecture": {"input_modalities": ["text"]},
    }


CATALOG = [
    entry("deepseek/deepseek-v4-flash-0731:free"),
    entry("qwen/qwen3.8-27b:free"),
    entry("z-ai/glm-5.2:free"),
]


class JevRoutingCoordinatorTests(unittest.TestCase):
    def test_deterministic_baseline_can_bypass_jev(self):
        result = coordinate({"task_class": "GENERAL", "deterministic": True}, CATALOG, use_jev=True)
        self.assertEqual(result["route_source"], "DETERMINISTIC_BASELINE")
        self.assertIsNone(result["jev"])

    def test_jev_can_refine_only_prevalidated_candidates(self):
        fake = {
            "status": "JEV_FAST_DECISION_OK",
            "decision": {
                "workers": [
                    "deepseek/deepseek-v4-flash-0731:free",
                    "qwen/qwen3.8-27b:free",
                ],
                "fanout": 2,
                "parallel": True,
                "execution_mode": "PARALLEL",
                "lane": "GENERAL_REASONING",
                "independent_verification": True,
                "action": "EXECUTE",
                "confidence": 0.91,
                "low_confidence": False,
            },
        }
        with patch("scripts.jev_routing_coordinator.decide_fast", return_value=fake):
            result = coordinate(
                {
                    "task_class": "GENERAL",
                    "objective": "Compare two independent approaches.",
                    "requires_distinct_specialists": True,
                },
                CATALOG,
                use_jev=True,
                api_key="x",
            )
        self.assertEqual(result["route_source"], "JEV_FAST_DECISION_PLANE")
        self.assertEqual(result["final_plan"]["active_model_count"], 2)
        self.assertEqual(result["final_plan"]["execution_mode"], "PARALLEL")
        self.assertEqual(result["final_plan"]["parallel_model_calls"], 2)

    def test_jev_failure_preserves_deterministic_route(self):
        with patch("scripts.jev_routing_coordinator.decide_lean", return_value={"status": "JEV_UNAVAILABLE"}):
            result = coordinate({"task_class": "GENERAL"}, CATALOG, use_jev=True, api_key="x")
        self.assertEqual(result["route_source"], "DETERMINISTIC_FALLBACK_AFTER_JEV_UNAVAILABLE")
        self.assertEqual(result["final_plan"]["selected_models"], result["baseline"]["selected_models"])
        self.assertEqual(result["final_plan"]["final_execution_admission"]["status"], "PASS")

    def test_coordinate_many_uses_one_batch_surface_for_many_tasks(self):
        tasks = [
            {"task_id": f"task_{i:02d}", "task_class": "GENERAL", "objective": f"Task {i}"}
            for i in range(10)
        ]
        decisions = {
            f"task_{i:02d}": {
                "workers": ["deepseek/deepseek-v4-flash-0731:free"],
                "fanout": 1,
                "parallel": False,
                "execution_mode": "SINGLE",
                "lane": "GENERAL_REASONING",
                "independent_verification": False,
                "action": "EXECUTE",
                "confidence": 0.9,
                "low_confidence": False,
            }
            for i in range(10)
        }
        fake = {
            "status": "JEV_LEAN_MANY_OK",
            "record_count": 10,
            "batch_count": 1,
            "parallel_batch_count": 1,
            "decisions": decisions,
        }
        with patch("scripts.jev_routing_coordinator.decide_many_lean", return_value=fake) as call:
            result = coordinate_many(tasks, CATALOG, use_jev=True, api_key="x")
        call.assert_called_once()
        self.assertEqual(result["task_count"], 10)
        self.assertEqual(len(result["plans"]), 10)
        self.assertTrue(all(plan["active_model_count"] == 1 for plan in result["plans"].values()))

    def test_batch_plan_never_exceeds_remaining_free_worker_budget(self):
        tasks = [
            {"task_id": "task_a", "task_class": "GENERAL", "objective": "A"},
            {"task_id": "task_b", "task_class": "GENERAL", "objective": "B"},
        ]
        fake = {
            "status": "JEV_LEAN_MANY_OK",
            "record_count": 2,
            "batch_count": 1,
            "parallel_batch_count": 1,
            "decisions": {
                "task_a": {
                    "workers": [
                        "deepseek/deepseek-v4-flash-0731:free",
                        "qwen/qwen3.8-27b:free",
                        "z-ai/glm-5.2:free",
                    ],
                    "parallel": True,
                    "execution_mode": "PARALLEL",
                    "lane": "GENERAL_REASONING",
                    "independent_verification": False,
                    "action": "EXECUTE",
                    "confidence": 0.9,
                    "low_confidence": False,
                },
                "task_b": {
                    "workers": [
                        "deepseek/deepseek-v4-flash-0731:free",
                        "qwen/qwen3.8-27b:free",
                        "z-ai/glm-5.2:free",
                    ],
                    "parallel": True,
                    "execution_mode": "PARALLEL",
                    "lane": "GENERAL_REASONING",
                    "independent_verification": False,
                    "action": "EXECUTE",
                    "confidence": 0.9,
                    "low_confidence": False,
                },
            },
        }
        with patch("scripts.jev_routing_coordinator.decide_many_lean", return_value=fake):
            result = coordinate_many(
                tasks,
                CATALOG,
                use_jev=True,
                api_key="x",
                free_requests_today=43,
            )
        total = sum(len(plan.get("selected_models", [])) for plan in result["plans"].values())
        self.assertLessEqual(total, 2)

    def test_low_confidence_routine_task_uses_bounded_hedge(self):
        fake = {
            "status": "JEV_LEAN_DECISION_OK",
            "decision": {
                "workers": ["qwen/qwen3.8-27b:free"],
                "fanout": 1,
                "parallel": False,
                "execution_mode": "SINGLE",
                "lane": "GENERAL_REASONING",
                "independent_verification": False,
                "action": "ESCALATE",
                "confidence": 0.44,
                "low_confidence": True,
            },
        }
        with patch("scripts.jev_routing_coordinator.decide_lean", return_value=fake), patch(
            "scripts.jev_routing_coordinator.load_recent_evidence", return_value={}
        ):
            result = coordinate(
                {"task_class": "GENERAL", "objective": "Routine synthesis.", "independent_workstreams": 1},
                CATALOG,
                use_jev=True,
                api_key="x",
            )
        self.assertEqual(result["route_source"], "JEV_LOW_CONFIDENCE_BOUNDED_HEDGE")
        self.assertLessEqual(result["final_plan"]["active_model_count"], 2)
        self.assertNotEqual(result["final_plan"]["status"], "REQUIRES_CHATGPT_ADJUDICATION")

    def test_low_confidence_high_impact_task_returns_to_chatgpt(self):
        fake = {
            "status": "JEV_FAST_DECISION_OK",
            "decision": {
                "workers": ["qwen/qwen3.8-27b:free"],
                "fanout": 1,
                "parallel": False,
                "execution_mode": "SINGLE",
                "lane": "GENERAL_REASONING",
                "independent_verification": True,
                "action": "ESCALATE",
                "confidence": 0.44,
                "low_confidence": True,
            },
        }
        with patch("scripts.jev_routing_coordinator.decide_fast", return_value=fake), patch(
            "scripts.jev_routing_coordinator.load_recent_evidence", return_value={}
        ):
            result = coordinate(
                {"task_class": "GENERAL", "objective": "High impact decision.", "high_impact": True},
                CATALOG,
                use_jev=True,
                api_key="x",
            )
        self.assertEqual(result["route_source"], "CHATGPT_ADJUDICATION_AFTER_JEV")
        self.assertEqual(result["final_plan"]["status"], "REQUIRES_CHATGPT_ADJUDICATION")

    def test_batch_high_impact_low_confidence_keeps_chatgpt_stop(self):
        fake = {
            "status": "JEV_FAST_MANY_OK",
            "record_count": 1,
            "batch_count": 1,
            "decisions": {
                "risk": {
                    "workers": ["qwen/qwen3.8-27b:free"],
                    "parallel": False,
                    "execution_mode": "SINGLE",
                    "lane": "GENERAL_REASONING",
                    "independent_verification": True,
                    "action": "ESCALATE",
                    "confidence": 0.4,
                    "low_confidence": True,
                }
            },
        }
        with patch("scripts.jev_routing_coordinator.decide_many_fast", return_value=fake):
            result = coordinate_many(
                [{"task_id": "risk", "task_class": "GENERAL", "high_impact": True}],
                CATALOG,
                use_jev=True,
                api_key="x",
            )
        plan = result["plans"]["risk"]
        self.assertEqual(plan["status"], "REQUIRES_CHATGPT_ADJUDICATION")
        self.assertEqual(plan["selected_models"], [])

    def test_final_guard_serializes_shared_state_and_marks_verifier_role(self):
        fake = {
            "status": "JEV_FAST_DECISION_OK",
            "decision": {
                "workers": ["deepseek/deepseek-v4-flash-0731:free", "qwen/qwen3.8-27b:free"],
                "parallel": True,
                "execution_mode": "PARALLEL",
                "lane": "GENERAL_REASONING",
                "independent_verification": True,
                "action": "EXECUTE",
                "confidence": 0.9,
                "low_confidence": False,
            },
        }
        with patch("scripts.jev_routing_coordinator.decide_fast", return_value=fake):
            result = coordinate(
                {"task_class": "GENERAL", "shared_mutable_state": True, "requires_distinct_specialists": True},
                CATALOG,
                use_jev=True,
                api_key="x",
            )
        plan = result["final_plan"]
        self.assertEqual(plan["execution_mode"], "SEQUENTIAL")
        self.assertEqual(plan["parallel_model_calls"], 1)
        self.assertEqual(plan["worker_roles"][1]["role"], "INDEPENDENT_VERIFIER")

    def test_slow_proven_primary_gets_one_latency_challenger(self):
        fake = {
            "status": "JEV_LEAN_DECISION_OK",
            "decision": {
                "workers": ["deepseek/deepseek-v4-flash-0731:free"],
                "fanout": 1,
                "parallel": False,
                "execution_mode": "SINGLE",
                "lane": "GENERAL_REASONING",
                "independent_verification": False,
                "action": "EXECUTE",
                "confidence": 0.95,
                "low_confidence": False,
            },
        }
        evidence = {
            "deepseek/deepseek-v4-flash-0731:free": {
                "successes": 3,
                "quality_failures": 0,
                "rate_limits": 0,
                "avg_latency_ms": 4500,
                "domain_stats": {"GENERAL": {"successes": 3, "quality_failures": 0, "rate_limits": 0, "avg_latency_ms": 4500}},
            },
            "qwen/qwen3.8-27b:free": {
                "successes": 1,
                "quality_failures": 0,
                "rate_limits": 0,
                "avg_latency_ms": 0,
                "domain_stats": {"GENERAL": {"successes": 1, "quality_failures": 0, "rate_limits": 0, "avg_latency_ms": 1200}},
            },
        }
        with patch("scripts.jev_routing_coordinator.decide_lean", return_value=fake), patch(
            "scripts.jev_routing_coordinator.load_recent_evidence", return_value=evidence
        ):
            result = coordinate(
                {"task_class": "GENERAL", "objective": "Routine task.", "independent_workstreams": 1},
                CATALOG,
                use_jev=True,
                api_key="x",
            )
        self.assertEqual(result["final_plan"]["active_model_count"], 2)
        self.assertEqual(result["final_plan"]["execution_mode"], "PARALLEL")
        self.assertIn("RECENT_PRIMARY_SLOW_LATENCY_CHALLENGER_ADDED", result["final_plan"]["fanout_reason"])
        self.assertEqual(result["final_plan"]["latency_challenger_timeout_seconds"], 3.5)

    def test_explicit_single_stream_with_fuzzy_primary_uses_lean_two_question_route(self):
        fake = {
            "status": "JEV_LEAN_DECISION_OK",
            "decision": {
                "workers": ["qwen/qwen3.8-27b:free"],
                "fanout": 1,
                "parallel": False,
                "execution_mode": "SINGLE",
                "lane": "GENERAL_REASONING",
                "independent_verification": False,
                "action": "EXECUTE",
                "confidence": 0.91,
                "low_confidence": False,
            },
        }
        with patch("scripts.jev_routing_coordinator.decide_shape") as shape, patch(
            "scripts.jev_routing_coordinator.decide_lean", return_value=fake
        ) as lean, patch(
            "scripts.jev_routing_coordinator.decide_fast"
        ) as fast, patch(
            "scripts.jev_routing_coordinator.load_recent_evidence", return_value={}
        ):
            result = coordinate(
                {
                    "task_class": "GENERAL",
                    "objective": "Routine single-stream task.",
                    "independent_workstreams": 1,
                },
                CATALOG,
                use_jev=True,
                api_key="x",
            )
        shape.assert_not_called()
        lean.assert_called_once()
        fast.assert_not_called()
        self.assertEqual(result["route_source"], "JEV_LEAN_DECISION_PLANE")
        self.assertIn("JEV_LEAN_TWO_QUESTION_DECISION", result["final_plan"]["fanout_reason"])

    def test_global_only_health_cannot_unlock_zero_question_route(self):
        evidence = {
            "deepseek/deepseek-v4-flash-0731:free": {
                "successes": 5,
                "quality_failures": 0,
                "rate_limits": 0,
                "avg_latency_ms": 300,
                "domain_stats": {"DATA_EXTRACTION": {"successes": 5, "quality_failures": 0, "rate_limits": 0, "avg_latency_ms": 300}},
            }
        }
        fake = {"status": "JEV_LEAN_DECISION_OK", "decision": {
            "workers": ["deepseek/deepseek-v4-flash-0731:free"], "parallel": False,
            "execution_mode": "SINGLE", "lane": "GENERAL_REASONING",
            "independent_verification": False, "action": "EXECUTE", "confidence": 0.9, "low_confidence": False,
        }}
        with patch("scripts.jev_routing_coordinator.load_recent_evidence", return_value=evidence), patch(
            "scripts.jev_routing_coordinator.decide_lean", return_value=fake
        ) as lean:
            result = coordinate({"task_class": "GENERAL", "objective": "General task."}, CATALOG, use_jev=True, api_key="x")
        lean.assert_called_once()
        self.assertEqual(result["route_source"], "JEV_LEAN_DECISION_PLANE")

    def test_explicit_parallel_pair_with_fuzzy_primary_uses_lean_two_question_route(self):
        fake = {
            "status": "JEV_LEAN_DECISION_OK",
            "decision": {
                "workers": [
                    "deepseek/deepseek-v4-flash-0731:free",
                    "qwen/qwen3.8-27b:free",
                ],
                "fanout": 2,
                "parallel": True,
                "execution_mode": "PARALLEL",
                "lane": "GENERAL_REASONING",
                "independent_verification": True,
                "action": "EXECUTE",
                "confidence": 0.9,
                "low_confidence": False,
            },
        }
        with patch("scripts.jev_routing_coordinator.decide_shape") as shape, patch(
            "scripts.jev_routing_coordinator.decide_lean", return_value=fake
        ) as lean, patch(
            "scripts.jev_routing_coordinator.decide_fast"
        ) as fast, patch(
            "scripts.jev_routing_coordinator.load_recent_evidence", return_value={}
        ):
            result = coordinate(
                {
                    "task_class": "GENERAL",
                    "objective": "Two independent workstreams.",
                    "independent_workstreams": 2,
                    "parallelizable_fraction": 0.9,
                },
                CATALOG,
                use_jev=True,
                api_key="x",
            )
        shape.assert_not_called()
        lean.assert_called_once()
        fast.assert_not_called()
        self.assertEqual(result["route_source"], "JEV_LEAN_DECISION_PLANE")
        self.assertEqual(result["final_plan"]["execution_mode"], "PARALLEL")

    def test_ambiguous_shape_uses_two_question_lean_route(self):
        fake = {
            "status": "JEV_LEAN_DECISION_OK",
            "decision": {
                "workers": ["qwen/qwen3.8-27b:free"],
                "fanout": 1,
                "parallel": False,
                "execution_mode": "SINGLE",
                "lane": "GENERAL_REASONING",
                "independent_verification": False,
                "action": "EXECUTE",
                "confidence": 0.9,
                "low_confidence": False,
            },
        }
        with patch(
            "scripts.jev_routing_coordinator.decide_lean", return_value=fake
        ) as lean, patch(
            "scripts.jev_routing_coordinator.decide_fast"
        ) as fast, patch(
            "scripts.jev_routing_coordinator.load_recent_evidence", return_value={}
        ):
            result = coordinate(
                {
                    "task_class": "GENERAL",
                    "objective": "Two workstreams with uncertain parallel value.",
                    "independent_workstreams": 2,
                    "parallelizable_fraction": 0.3,
                },
                CATALOG,
                use_jev=True,
                api_key="x",
            )
        lean.assert_called_once()
        fast.assert_not_called()
        self.assertEqual(result["route_source"], "JEV_LEAN_DECISION_PLANE")

    def test_distinct_specialists_keep_rich_route(self):
        fake = {
            "status": "JEV_FAST_DECISION_OK",
            "decision": {
                "workers": [
                    "deepseek/deepseek-v4-flash-0731:free",
                    "qwen/qwen3.8-27b:free",
                ],
                "fanout": 2,
                "parallel": True,
                "execution_mode": "PARALLEL",
                "lane": "GENERAL_REASONING",
                "independent_verification": True,
                "action": "EXECUTE",
                "confidence": 0.9,
                "low_confidence": False,
            },
        }
        with patch(
            "scripts.jev_routing_coordinator.decide_lean"
        ) as lean, patch(
            "scripts.jev_routing_coordinator.decide_fast", return_value=fake
        ) as fast, patch(
            "scripts.jev_routing_coordinator.load_recent_evidence", return_value={}
        ):
            result = coordinate(
                {
                    "task_class": "GENERAL",
                    "objective": "Use distinct complementary specialists.",
                    "independent_workstreams": 2,
                    "parallelizable_fraction": 0.9,
                    "requires_distinct_specialists": True,
                },
                CATALOG,
                use_jev=True,
                api_key="x",
            )
        lean.assert_not_called()
        fast.assert_called_once()
        self.assertEqual(result["route_source"], "JEV_FAST_DECISION_PLANE")


    def test_clear_primary_and_clear_shape_use_zero_question_fast_path(self):
        evidence = {
            "deepseek/deepseek-v4-flash-0731:free": {
                "successes": 2,
                "quality_failures": 0,
                "rate_limits": 0,
                "avg_latency_ms": 900,
                "domain_stats": {"GENERAL": {"successes": 2, "quality_failures": 0, "rate_limits": 0, "avg_latency_ms": 900}},
            }
        }
        with patch("scripts.jev_routing_coordinator.load_recent_evidence", return_value=evidence), patch(
            "scripts.jev_routing_coordinator.decide_shape"
        ) as shape, patch(
            "scripts.jev_routing_coordinator.decide_lean"
        ) as lean, patch(
            "scripts.jev_routing_coordinator.decide_fast"
        ) as fast:
            result = coordinate(
                {
                    "task_class": "GENERAL",
                    "objective": "Routine single-stream task.",
                    "independent_workstreams": 1,
                },
                CATALOG,
                use_jev=True,
                api_key="x",
            )
        shape.assert_not_called()
        lean.assert_not_called()
        fast.assert_not_called()
        self.assertEqual(result["route_source"], "DETERMINISTIC_HEALTH_FAST_PATH")
        self.assertIn("PYTHON_CLEAR_PRIMARY_AND_CLEAR_SHAPE", result["final_plan"]["fanout_reason"])

    def test_clear_primary_and_ambiguous_shape_use_shape_one_question(self):
        evidence = {
            "deepseek/deepseek-v4-flash-0731:free": {
                "successes": 2,
                "quality_failures": 0,
                "rate_limits": 0,
                "avg_latency_ms": 900,
                "domain_stats": {"GENERAL": {"successes": 2, "quality_failures": 0, "rate_limits": 0, "avg_latency_ms": 900}},
            }
        }
        fake = {
            "status": "JEV_SHAPE_DECISION_OK",
            "decision": {
                "workers": ["deepseek/deepseek-v4-flash-0731:free"],
                "fanout": 1,
                "parallel": False,
                "execution_mode": "SINGLE",
                "lane": "GENERAL_REASONING",
                "independent_verification": False,
                "action": "EXECUTE",
                "confidence": 0.96,
                "low_confidence": False,
                "route_shape": "SINGLE",
            },
        }
        with patch("scripts.jev_routing_coordinator.load_recent_evidence", return_value=evidence), patch(
            "scripts.jev_routing_coordinator.decide_shape", return_value=fake
        ) as shape, patch(
            "scripts.jev_routing_coordinator.decide_lean"
        ) as lean, patch(
            "scripts.jev_routing_coordinator.decide_fast"
        ) as fast:
            result = coordinate(
                {
                    "task_class": "GENERAL",
                    "objective": "Two workstreams with uncertain parallel value.",
                    "independent_workstreams": 2,
                    "parallelizable_fraction": 0.3,
                },
                CATALOG,
                use_jev=True,
                api_key="x",
            )
        shape.assert_called_once()
        lean.assert_not_called()
        fast.assert_not_called()
        self.assertEqual(result["route_source"], "JEV_SHAPE_DECISION_PLANE")
        self.assertIn("JEV_SHAPE_ONE_QUESTION_DECISION", result["final_plan"]["fanout_reason"])


if __name__ == "__main__":
    unittest.main()
