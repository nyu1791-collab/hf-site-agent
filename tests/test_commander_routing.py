import copy
import unittest

from scripts.commander_routing import (
    build_commander_command,
    route_mission,
    validate_commander_command,
    RoutingError,
)
from scripts.provider_registry import load_provider_registry


class CommanderRoutingTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_provider_registry()

    def test_routes_one_mission_to_one_suitable_commander(self):
        for mission_type, provider, agent_id in (
            ("research", "google", "google-general-commander"),
            ("repository", "nvidia", "nvidia-engineering-commander"),
            ("bulk", "groq", "groq-rapid-commander"),
        ):
            route = route_mission(mission_type, self.registry)
            self.assertEqual(route["provider"], provider)
            self.assertEqual(route["commander_agent_id"], agent_id)
            self.assertEqual(route["max_provider_paths"], 1)
            self.assertFalse(route["broadcast"])
            self.assertFalse(route["paid_fallback"])
            self.assertEqual(route["status"], "blocked_provider_not_ready")

    def test_python_and_worker_routes_do_not_make_openrouter_a_commander(self):
        python_route = route_mission("simple", self.registry)
        self.assertEqual(python_route["status"], "python_first")
        self.assertIsNone(python_route["commander_agent_id"])
        worker_route = route_mission("light_worker", self.registry)
        self.assertEqual(worker_route["status"], "worker_route_required")
        self.assertIsNone(worker_route["commander_agent_id"])

    def test_explicit_cross_check_is_at_most_two_paths(self):
        route = route_mission("research", self.registry, independent_verifier="nvidia")
        self.assertEqual(route["independent_verifier"], "nvidia")
        self.assertEqual(route["max_provider_paths"], 2)
        self.assertTrue(route["broadcast"])
        with self.assertRaises(RoutingError):
            route_mission("research", self.registry, independent_verifier="google")

    def test_commander_command_is_direct_bounded_and_read_only(self):
        route, command = build_commander_command(
            "MISSION-ROUTE-01",
            "coding",
            "リポジトリを読み取り、修正案と検証計画を作る",
            self.registry,
            request_budget=3,
        )
        self.assertEqual(route["provider"], "nvidia")
        self.assertEqual(command.parent_agent_id, "chatgpt-work")
        self.assertEqual(command.child_agent_id, "nvidia-engineering-commander")
        self.assertEqual(command.provider_preference, "nvidia")
        self.assertEqual(command.request_budget, 3)
        self.assertEqual(command.side_effect_level, "read_only_draft")
        validate_commander_command(command)

    def test_route_does_not_bypass_provider_health_or_paid_policy(self):
        candidate = copy.deepcopy(self.registry)
        candidate["providers"]["google"]["enabled"] = True
        candidate["providers"]["google"]["health_status"] = "HEALTHY"
        candidate["providers"]["google"]["probe_status"] = "PROBE_OK"
        candidate["providers"]["google"]["activation_approved"] = True
        candidate["providers"]["google"]["last_probe_at"] = "2026-09-08T00:00:00Z"
        candidate["providers"]["google"]["last_success_at"] = "2026-09-08T00:00:00Z"
        # Registry validation still requires the circuit and the policy; a
        # route never turns those controls on implicitly.
        route = route_mission("planning", candidate)
        self.assertEqual(route["status"], "ready")
        self.assertFalse(route["paid_fallback"])

    def test_unknown_or_unsafe_routes_are_rejected(self):
        with self.assertRaises(RoutingError):
            route_mission("unknown", self.registry)
        with self.assertRaises(RoutingError):
            route_mission("simple", self.registry, independent_verifier="groq")


if __name__ == "__main__":
    unittest.main()
