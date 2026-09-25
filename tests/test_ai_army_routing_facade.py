from __future__ import annotations

import unittest

from scripts.ai_army_routing_facade import plan_route


class RoutingFacadeTests(unittest.TestCase):
    def test_deterministic_work_uses_machine_path(self) -> None:
        plan = plan_route({
            "task_class": "DETERMINISTIC_TRANSFORM",
            "dependency_shape": "SEQUENTIAL",
            "mutation_scope": "READ_ONLY",
            "independent_workstreams": 1,
            "deterministic": True,
        })
        self.assertEqual(plan["route_kind"], "DETERMINISTIC_TOOL_PATH")
        self.assertFalse(plan["paid"])
        self.assertFalse(plan["generic_paid_fallback"])

    def test_research_uses_preauthorized_deepseek_supervisor(self) -> None:
        plan = plan_route({
            "task_class": "RESEARCH",
            "dependency_shape": "PARALLEL",
            "mutation_scope": "READ_ONLY",
            "independent_workstreams": 4,
            "high_impact": True,
        })
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["route_kind"], "DEEPSEEK_EXECUTIVE_SUPERVISOR")
        self.assertTrue(plan["paid"])
        self.assertTrue(plan["preauthorized_scope_only"])
        self.assertTrue(plan["chatgpt_final_authority"])

    def test_paid_supervisor_can_be_disabled_per_mission(self) -> None:
        plan = plan_route({
            "task_class": "STRATEGY",
            "dependency_shape": "SEQUENTIAL",
            "mutation_scope": "READ_ONLY",
            "independent_workstreams": 1,
            "paid_supervisor_allowed": False,
        })
        self.assertEqual(plan["route_kind"], "CHATGPT_SINGLE_CONTROLLER")
        self.assertFalse(plan["paid"])

    def test_direct_specialist_bypass_fails_closed_without_fresh_registry(self) -> None:
        plan = plan_route({
            "task_class": "CODING",
            "dependency_shape": "SEQUENTIAL",
            "mutation_scope": "SAME_TARGET",
            "independent_workstreams": 1,
        })
        self.assertEqual(plan["route_kind"], "DIRECT_SPECIALIST_BYPASS")
        self.assertEqual(plan["status"], "BLOCKED_PROVIDER_REGISTRY_REQUIRED")
        self.assertFalse(plan["paid"])
        self.assertTrue(plan["single_writer_required"])

    def test_same_target_parallel_research_keeps_single_writer(self) -> None:
        plan = plan_route({
            "task_class": "PEER_REVIEW",
            "dependency_shape": "PARALLEL",
            "mutation_scope": "SAME_TARGET",
            "independent_workstreams": 3,
        })
        self.assertEqual(plan["route_kind"], "DEEPSEEK_EXECUTIVE_SUPERVISOR")
        self.assertTrue(plan["single_writer_required"])


if __name__ == "__main__":
    unittest.main()
