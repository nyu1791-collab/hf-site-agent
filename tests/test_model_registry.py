import copy
import unittest

from scripts.model_registry import (
    GENERIC_FREE_IDS,
    MODEL_RECORD_FIELDS,
    load_registry,
    normalized_model_records,
    resolve_role_model,
    role_candidates,
    validate_registry,
    watch_catalog,
)


def free_entry(model_id):
    return {
        "id": model_id,
        "pricing": {"prompt": "0", "completion": "0"},
        "supported_parameters": ["tools", "tool_choice", "structured_outputs"],
        "context_length": 1048576,
    }


class ModelRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry()

    def test_registry_is_valid_and_generic_router_is_not_a_candidate(self):
        validate_registry(self.registry)
        for role_name in self.registry["roles"]:
            self.assertNotIn("openrouter/free", role_candidates(self.registry, role_name))
        self.assertFalse(self.registry["policy"]["allow_paid_models"])
        self.assertFalse(self.registry["policy"]["allow_generic_free_router"])
        self.assertIn("openrouter/free", GENERIC_FREE_IDS)

    def test_inactive_commander_never_resolves_even_when_catalog_is_free(self):
        entries = [
            free_entry("z-ai/glm-5.3-flash:free"),
            free_entry("deepseek/deepseek-v4-flash:free"),
        ]
        for role_name in ("ROLE_GENERAL_COMMANDER", "ROLE_ENGINEERING_COMMANDER"):
            result = resolve_role_model(self.registry, entries, role_name)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["reason"], "role_inactive_requires_commander_approval")
            self.assertFalse(result["paid_fallback"])

    def test_provider_commander_and_worker_roles_start_unresolved_and_inactive(self):
        expected = {
            "ROLE_GOOGLE_GENERAL_COMMANDER": "google",
            "ROLE_NVIDIA_ENGINEERING_COMMANDER": "nvidia",
            "ROLE_GROQ_RAPID_EXECUTION_COMMANDER": "groq",
            "ROLE_OPENROUTER_WORKER": "openrouter",
        }
        for role_name, provider_id in expected.items():
            role = self.registry["roles"][role_name]
            self.assertEqual(role["provider_id"], provider_id)
            self.assertEqual(role["primary_model"], None)
            self.assertEqual(role["candidate_models"], [])
            self.assertFalse(role["active"])
            self.assertEqual(role_candidates(self.registry, role_name), [])
        self.assertTrue(self.registry["roles"]["ROLE_OPENROUTER_WORKER"]["worker_only"])
        self.assertFalse(self.registry["roles"]["ROLE_OPENROUTER_WORKER"]["generic_router_allowed"])

    def test_provider_role_rejects_catalog_metadata_from_another_provider(self):
        registry = copy.deepcopy(self.registry)
        role = registry["roles"]["ROLE_GOOGLE_GENERAL_COMMANDER"]
        role["active"] = True
        role["approved"] = True
        role["candidate_models"] = ["z-ai/glm-5.3-flash:free"]
        result = resolve_role_model(
            registry,
            [free_entry("z-ai/glm-5.3-flash:free")],
            "ROLE_GOOGLE_GENERAL_COMMANDER",
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "no_current_zero_priced_role_candidate")

    def _active_registry(self):
        registry = copy.deepcopy(self.registry)
        for role_name, model_id in (
            ("ROLE_GENERAL_COMMANDER", "z-ai/glm-5.3-flash:free"),
            ("ROLE_ENGINEERING_COMMANDER", "deepseek/deepseek-v4-flash:free"),
        ):
            registry["roles"][role_name]["active"] = True
            registry["models"][model_id]["free_available"] = True
            registry["models"][model_id]["status"] = "FREE_ACTIVE"
        return registry

    def test_active_role_can_resolve_only_same_role_zero_price_candidate(self):
        registry = self._active_registry()
        entries = [
            free_entry("z-ai/glm-5.3-flash:free"),
            free_entry("deepseek/deepseek-v4-flash:free"),
        ]
        general = resolve_role_model(registry, entries, "ROLE_GENERAL_COMMANDER")
        engineering = resolve_role_model(registry, entries, "ROLE_ENGINEERING_COMMANDER")
        self.assertEqual(general["model"], "z-ai/glm-5.3-flash:free")
        self.assertEqual(engineering["model"], "deepseek/deepseek-v4-flash:free")

    def test_paid_primary_is_not_used_without_paid_policy(self):
        registry = self._active_registry()
        result = resolve_role_model(
            registry,
            [{"id": "z-ai/glm-5.3-flash", "pricing": {"prompt": "0.000000075", "completion": "0.00000025"}}],
            "ROLE_GENERAL_COMMANDER",
            "z-ai/glm-5.3-flash",
        )
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "requested_model_not_allowed_for_role")

    def test_cross_role_and_legacy_ids_are_rejected(self):
        self.assertEqual(
            role_candidates(self.registry, "ROLE_GENERAL_COMMANDER", "deepseek/deepseek-v4-flash:free"),
            [],
        )
        self.assertEqual(
            role_candidates(self.registry, "ROLE_GENERAL_COMMANDER", "qwen/qwen3-32b:free"),
            [],
        )
        legacy_ids = {entry["id"] for entry in self.registry["legacy"]}
        self.assertIn("qwen/qwen3-32b:free", legacy_ids)
        self.assertNotIn("deepseek/deepseek-v4-flash:free", legacy_ids)

    def test_catalog_watch_is_read_only(self):
        report = watch_catalog(
            self.registry,
            [{"id": "z-ai/glm-5.3-flash", "pricing": {"prompt": "0.000000075", "completion": "0.00000025"}}],
        )
        self.assertEqual(report["model_calls"], 0)
        self.assertFalse(report["paid_operations"])
        self.assertFalse(report["active_roles_changed"])
        self.assertTrue(any(item["model_id"] == "z-ai/glm-5.3-flash" for item in report["models"]))

    def test_normalized_model_projection_exposes_v2_evaluation_contract_without_mutation(self):
        before = copy.deepcopy(self.registry)
        records = normalized_model_records(self.registry)
        self.assertEqual(set(MODEL_RECORD_FIELDS), set(records["deepseek/deepseek-v4-pro"]))
        self.assertEqual(records["deepseek/deepseek-v4-pro"]["provider_id"], "openrouter")
        self.assertEqual(records["deepseek/deepseek-v4-pro"]["coding"], True)
        self.assertEqual(records["deepseek/deepseek-v4-pro"]["free_verified"], False)
        self.assertEqual(self.registry, before)


if __name__ == "__main__":
    unittest.main()
