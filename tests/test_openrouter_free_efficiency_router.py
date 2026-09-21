import unittest

from scripts.openrouter_free_efficiency_router import (
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


class OpenRouterFreeEfficiencyRouterTests(unittest.TestCase):
    def test_exact_free_gate_rejects_generic_and_paid(self):
        self.assertTrue(exact_free_catalog_entry(entry("vendor/model:free")))
        self.assertFalse(exact_free_catalog_entry(entry("openrouter/free")))
        self.assertFalse(exact_free_catalog_entry(entry("vendor/model")))
        self.assertFalse(exact_free_catalog_entry(entry("vendor/model:free", prompt="0.01")))

    def test_one_primary_only_and_sequential_standby(self):
        catalog = [
            entry("deepseek/deepseek-v4-flash-0731:free", params=["tools", "tool_choice"]),
            entry("qwen/qwen3.8-27b:free", params=["tools", "tool_choice"]),
            entry("vendor/other:free"),
        ]
        plan = plan_task({"task_class": "GENERAL"}, catalog)
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["active_model_count"], 1)
        self.assertEqual(plan["parallel_model_calls"], 1)
        self.assertEqual(plan["primary_model"], "deepseek/deepseek-v4-flash-0731:free")
        self.assertEqual(plan["standby_mode"], "SEQUENTIAL_ESCALATION_ONLY")
        self.assertFalse(plan["paid_fallback"])

    def test_unverified_account_uses_conservative_hard_stop(self):
        catalog = [entry("deepseek/deepseek-v4-flash-0731:free")]
        self.assertEqual(plan_task({"task_class": "GENERAL"}, catalog)["quota_hard_stop"], 45)
        self.assertEqual(
            plan_task(
                {"task_class": "GENERAL"},
                catalog,
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
