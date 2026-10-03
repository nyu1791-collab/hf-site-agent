import unittest

from scripts.commander_routing import build_commander_command, route_mission, validate_commander_command
from scripts.provider_registry import load_provider_registry


MISSION_MATRIX = {
    "google": ("research", "planning", "long_context", "multimodal", "synthesis"),
    "nvidia": ("repository", "coding", "debug", "testing", "code_review"),
    "groq": ("summary", "classification", "extraction", "json_transform", "log_triage"),
}


class MissionMatrixTests(unittest.TestCase):
    def setUp(self):
        self.provider_registry = load_provider_registry()

    def test_each_commander_matrix_case_has_one_primary_route(self):
        for provider, mission_types in MISSION_MATRIX.items():
            for index, mission_type in enumerate(mission_types, start=1):
                route = route_mission(mission_type, self.provider_registry)
                self.assertEqual(route["provider"], provider)
                self.assertEqual(route["max_provider_paths"], 1)
                self.assertFalse(route["broadcast"])
                self.assertFalse(route["paid_fallback"])
                route, command = build_commander_command(
                    f"MISSION-MATRIX-{provider.upper()}-{index}",
                    mission_type,
                    f"{mission_type}のread-only contract test",
                    self.provider_registry,
                    request_budget=1,
                )
                self.assertEqual(command.provider_preference, provider)
                validate_commander_command(command)

    def test_verification_is_explicit_and_never_three_way_voting(self):
        route = route_mission("code_review", self.provider_registry, independent_verifier="google")
        self.assertEqual(route["max_provider_paths"], 2)
        self.assertTrue(route["broadcast"])
        self.assertNotIn("groq", {route["provider"], route["independent_verifier"]})


if __name__ == "__main__":
    unittest.main()
