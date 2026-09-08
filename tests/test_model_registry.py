import copy
import unittest

from scripts.model_registry import (
    GENERIC_FREE_IDS,
    load_registry,
    resolve_role_model,
    role_candidates,
    validate_registry,
    watch_catalog,
)


class ModelRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry()

    def test_registry_is_valid_and_generic_router_is_not_a_candidate(self):
        validate_registry(self.registry)
        for role_name in self.registry["roles"]:
            self.assertNotIn("openrouter/free", role_candidates(self.registry, role_name))
        self.assertEqual(self.registry["policy"]["allow_paid_models"], False)
        self.assertEqual(self.registry["policy"]["allow_generic_free_router"], False)
        self.assertIn("openrouter/free", GENERIC_FREE_IDS)

    def test_inactive_commander_never_resolves_even_when_catalog_is_free(self):
        entries = [
            {"id": "thinkingmachines/inkling:free", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "poolside/laguna-s-2.1:free", "pricing": {"prompt": "0", "completion": "0"}},
        ]
        for role_name in ("ROLE_GENERAL_COMMANDER", "ROLE_ENGINEERING_COMMANDER"):
            result = resolve_role_model(self.registry, entries, role_name)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["reason"], "role_inactive_requires_commander_approval")
            self.assertEqual(result["paid_fallback"], False)

    def test_active_role_can_resolve_only_same_role_zero_price_candidate(self):
        registry = copy.deepcopy(self.registry)
        registry["roles"]["ROLE_GENERAL_COMMANDER"]["active"] = True
        registry["roles"]["ROLE_ENGINEERING_COMMANDER"]["active"] = True
        entries = [
            {"id": "thinkingmachines/inkling:free", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "poolside/laguna-s-2.1:free", "pricing": {"prompt": "0", "completion": "0"}},
        ]
        general = resolve_role_model(registry, entries, "ROLE_GENERAL_COMMANDER")
        engineering = resolve_role_model(registry, entries, "ROLE_ENGINEERING_COMMANDER")
        self.assertEqual(general["model"], "thinkingmachines/inkling:free")
        self.assertEqual(engineering["model"], "poolside/laguna-s-2.1:free")
        self.assertTrue(general["model"].endswith(":free"))
        self.assertTrue(engineering["model"].endswith(":free"))

    def test_paid_primary_is_not_used_without_paid_policy(self):
        registry = copy.deepcopy(self.registry)
        registry["roles"]["ROLE_GENERAL_COMMANDER"]["active"] = True
        entries = [
            {"id": "z-ai/glm-5.3-flash", "pricing": {"prompt": "0.000000075", "completion": "0.00000025"}},
        ]
        result = resolve_role_model(registry, entries, "ROLE_GENERAL_COMMANDER", "z-ai/glm-5.3-flash")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "no_current_zero_priced_role_candidate")

    def test_cross_role_and_legacy_ids_are_rejected(self):
        self.assertEqual(
            role_candidates(self.registry, "ROLE_GENERAL_COMMANDER", "poolside/laguna-s-2.1:free"),
            [],
        )
        self.assertEqual(
            role_candidates(self.registry, "ROLE_GENERAL_COMMANDER", "qwen/qwen3-32b:free"),
            [],
        )
        legacy_ids = {entry["id"] for entry in self.registry["legacy"]}
        self.assertIn("qwen/qwen3-32b:free", legacy_ids)

    def test_catalog_watch_is_read_only(self):
        report = watch_catalog(
            self.registry,
            [{"id": "z-ai/glm-5.3-flash", "pricing": {"prompt": "0.000000075", "completion": "0.00000025"}}],
        )
        self.assertEqual(report["model_calls"], 0)
        self.assertFalse(report["paid_operations"])
        self.assertFalse(report["active_roles_changed"])
        self.assertTrue(any(item["model_id"] == "z-ai/glm-5.3-flash" for item in report["models"]))


if __name__ == "__main__":
    unittest.main()
