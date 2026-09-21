import unittest
from unittest.mock import patch

from scripts.jev_routing_coordinator import coordinate


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
            "status": "JEV_DECISION_OK",
            "decision": {
                "selected_models": [
                    "deepseek/deepseek-v4-flash-0731:free",
                    "qwen/qwen3.8-27b:free",
                ],
                "fanout": 2,
                "execution_mode": "PARALLEL",
                "lane": "GENERAL_REASONING",
                "independent_verification": True,
                "action": "EXECUTE",
                "confidence": 0.91,
                "low_confidence": False,
            },
        }
        with patch("scripts.jev_routing_coordinator.decide", return_value=fake):
            result = coordinate({"task_class": "GENERAL", "objective": "Compare two independent approaches."}, CATALOG, use_jev=True, api_key="x")
        self.assertEqual(result["route_source"], "JEV_FAST_DECISION_PLANE")
        self.assertEqual(result["final_plan"]["active_model_count"], 2)
        self.assertEqual(result["final_plan"]["execution_mode"], "PARALLEL")

    def test_jev_failure_preserves_deterministic_route(self):
        with patch("scripts.jev_routing_coordinator.decide", return_value={"status": "JEV_UNAVAILABLE"}):
            result = coordinate({"task_class": "GENERAL"}, CATALOG, use_jev=True, api_key="x")
        self.assertEqual(result["route_source"], "DETERMINISTIC_FALLBACK_AFTER_JEV_UNAVAILABLE")
        self.assertEqual(result["final_plan"], result["baseline"])


if __name__ == "__main__":
    unittest.main()
