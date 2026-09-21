import unittest

from scripts.openrouter_free_efficiency_router import (
    decide_fanout,
    exact_free_catalog_entry,
    plan_task,
)


def entry(model, *, prompt="0", completion="0", context=131072, params=None, modalities=None):
    return {
        "id": model,
        "pricing": {"prompt": prompt, "completion": completion},
        "context_length": context,
        "supported_parameters": params or [],
        "architecture": {"input_modalities": modalities or ["text"]},
    }


CATALOG = [
    entry("deepseek/deepseek-v4-flash-0731:free", params=["tools", "tool_choice"]),
    entry("qwen/qwen3.8-27b:free", params=["tools", "tool_choice"], modalities=["text", "image", "video"]),
    entry("nvidia/nemotron-3-ultra-550b-a55b:free"),
    entry("z-ai/glm-5.2:free"),
]


class OpenRouterFreeEfficiencyRouterTests(unittest.TestCase):
    def test_exact_free_gate_rejects_generic_and_paid(self):
        self.assertTrue(exact_free_catalog_entry(entry("vendor/model:free")))
        self.assertFalse(exact_free_catalog_entry(entry("openrouter/free")))
        self.assertFalse(exact_free_catalog_entry(entry("vendor/model")))
        self.assertFalse(exact_free_catalog_entry(entry("vendor/model:free", prompt="0.01")))

    def test_simple_task_uses_one_model_when_total_value_is_highest(self):
        plan = plan_task({"task_class": "GENERAL"}, CATALOG)
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["active_model_count"], 1)
        self.assertEqual(plan["parallel_model_calls"], 1)
        self.assertEqual(plan["primary_model"], "deepseek/deepseek-v4-flash-0731:free")
        self.assertIn("ONE_MODEL_HAS_HIGHEST_EXPECTED_TOTAL_SYSTEM_VALUE", plan["fanout_reason"])
        self.assertFalse(plan["paid_fallback"])

    def test_parallel_independent_workstreams_can_use_three_models(self):
        plan = plan_task(
            {
                "task_class": "GENERAL",
                "independent_workstreams": 3,
                "parallelizable_fraction": 0.85,
                "latency_priority": "high",
            },
            CATALOG,
        )
        self.assertEqual(plan["active_model_count"], 3)
        self.assertEqual(plan["parallel_model_calls"], 3)
        self.assertEqual(plan["execution_mode"], "PARALLEL_INDEPENDENT_OR_VERIFICATION")
        self.assertIn("INDEPENDENT_WORKSTREAMS_REDUCE_WALL_CLOCK", plan["fanout_reason"])

    def test_quality_and_verification_can_use_two_models(self):
        plan = plan_task(
            {
                "task_class": "GENERAL",
                "high_impact": True,
                "independent_verification": True,
                "quality_priority": "critical",
            },
            CATALOG,
        )
        self.assertEqual(plan["active_model_count"], 2)
        self.assertIn("INDEPENDENT_VERIFICATION_MATERIALLY_REDUCES_RISK", plan["fanout_reason"])

    def test_coordination_overhead_forces_single_model(self):
        fanout, reasons = decide_fanout(
            {
                "independent_workstreams": 3,
                "parallelizable_fraction": 0.9,
                "latency_priority": "critical",
                "coordination_overhead_ratio": 0.5,
            },
            candidate_count=4,
            remaining_quota=40,
        )
        self.assertEqual(fanout, 1)
        self.assertEqual(reasons, ["COORDINATION_OVERHEAD_EXCEEDS_EXPECTED_FANOUT_GAIN"])

    def test_low_quota_forces_single_model(self):
        fanout, reasons = decide_fanout(
            {
                "independent_workstreams": 3,
                "parallelizable_fraction": 0.9,
                "latency_priority": "critical",
            },
            candidate_count=4,
            remaining_quota=5,
        )
        self.assertEqual(fanout, 1)
        self.assertEqual(reasons, ["FREE_QUOTA_HEADROOM_LOW"])

    def test_unverified_account_uses_conservative_hard_stop(self):
        self.assertEqual(plan_task({"task_class": "GENERAL"}, CATALOG)["quota_hard_stop"], 45)
        self.assertEqual(
            plan_task(
                {"task_class": "GENERAL"},
                CATALOG,
                account_ten_dollar_eligibility_verified=True,
            )["quota_hard_stop"],
            900,
        )

    def test_vision_task_filters_nonvision_primary(self):
        catalog = [
            entry("deepseek/deepseek-v4-flash-0731:free"),
            entry("qwen/qwen3.8-27b:free", modalities=["text", "image", "video"]),
            entry("inclusionai/ling-3.0-flash-vl:free", modalities=["text", "image", "video"]),
        ]
        plan = plan_task({"task_class": "VISION", "requires_image": True}, catalog)
        self.assertEqual(plan["primary_model"], "qwen/qwen3.8-27b:free")


if __name__ == "__main__":
    unittest.main()
