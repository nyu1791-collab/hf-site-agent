import unittest

from scripts.free_evidence import resolve_free_evidence


def quota():
    return {
        "quota_verified": True,
        "quota_safe": True,
        "quota_source": "official-fixture",
        "quota_limits": {"rpm": 30, "tpm": 8000},
    }


class FreeEvidenceResolverTests(unittest.TestCase):
    def test_expected_candidate_matrix_is_independent_and_fail_closed(self):
        cases = [
            (
                "google",
                "gemini-3.8-flash",
                [{"name": "models/gemini-3.8-flash"}],
                {},
                {"free_tier": {"input": "Free of charge", "output": "Free of charge"}, "paid_tier": {"input": "0.75", "output": "3.75"}},
                "BLOCKED",
            ),
            (
                "groq",
                "qwen/qwen3.8-27b",
                [{"id": "qwen/qwen3.8-27b", "pricing": {"prompt": "0.80", "completion": "4.00"}}],
                {},
                {"free_plan_limits": {"qwen/qwen3.8-27b": {"rpm": 30}}, "paid_tier": {"input": "0.80", "output": "4.00"}},
                "BLOCKED",
            ),
            (
                "nvidia",
                "deepseek-ai/deepseek-v4-flash-0731",
                [{"id": "deepseek-ai/deepseek-v4-flash-0731"}],
                {},
                {"free_endpoint_available": True, "free_price_verified": True, "automatic_paid_transition_possible": False, "fallback_to_paid_possible": False},
                "BLOCKED",
            ),
            (
                "nvidia",
                "nvidia/nemotron-3.5-lightning-30b-a3b",
                [{"id": "nvidia/nemotron-3.5-lightning-30b-a3b"}],
                {},
                {"free_endpoint_available": True, "free_price_verified": True, "automatic_paid_transition_possible": False, "fallback_to_paid_possible": False},
                "BLOCKED",
            ),
            (
                "openrouter",
                "z-ai/glm-5.3-flash:free",
                [{"id": "z-ai/glm-5.3-flash", "pricing": {"prompt": "0.000000075", "completion": "0.00000025"}}],
                {"current_account_eligible": True, "automatic_paid_transition_possible": False, "fallback_to_paid_possible": False},
                {"public_page_exists": True, "api_catalog_exists": False, "provider_allow_fallbacks": False},
                "INCONSISTENT",
            ),
        ]
        for provider, model, catalog, account, pricing, expected_status in cases:
            with self.subTest(provider=provider, model=model):
                result = resolve_free_evidence(
                    provider,
                    model,
                    catalog,
                    account,
                    pricing,
                    quota(),
                    evidence_source=f"official-current-fixture:{provider}",
                    evidence_timestamp="2026-09-09T00:00:00Z",
                )
                self.assertEqual(result["status"], expected_status)
                self.assertFalse(result["zero_cost_verified"])

    def test_google_paid_price_and_free_tier_are_separate(self):
        result = resolve_free_evidence(
            "google",
            "gemini-3.8-flash",
            [{"name": "models/gemini-3.8-flash"}],
            {
                "current_account_tier": "FREE",
                "automatic_paid_transition_possible": False,
                "fallback_to_paid_possible": False,
            },
            {
                "free_tier": {"input": "Free of charge", "output": "Free of charge"},
                "paid_tier": {"input": "0.75", "output": "3.75"},
                "catalog_verified": True,
                "exact_model_verified": True,
            },
            quota(),
            evidence_source="google-official-pricing-fixture",
            evidence_timestamp="2026-09-09T00:00:00Z",
        )
        self.assertEqual(result["status"], "VERIFIED")
        self.assertTrue(result["free_program_exists"])
        self.assertTrue(result["paid_price_exists"])
        self.assertEqual(result["selected_route"], "FREE_TIER")
        self.assertTrue(result["zero_cost_verified"])

    def test_google_free_tier_label_normalizes_without_changing_paid_price(self):
        result = resolve_free_evidence(
            "google",
            "gemini-3.8-flash",
            [{"id": "gemini-3.8-flash"}],
            {"current_account_tier": "Free Tier", "automatic_paid_transition_possible": False, "fallback_to_paid_possible": False},
            {"free_tier": {"input": "0", "output": "0"}, "paid_tier": {"input": "0.75", "output": "3.75"}},
            quota(),
            evidence_source="google-tier-label-fixture",
        )
        self.assertEqual(result["current_account_tier"], "FREE")
        self.assertTrue(result["zero_cost_verified"])

    def test_google_free_program_does_not_override_paid_current_account(self):
        result = resolve_free_evidence(
            "google",
            "gemini-3.8-flash",
            [{"id": "gemini-3.8-flash"}],
            {
                "current_account_tier": "PAID",
                "automatic_paid_transition_possible": True,
                "fallback_to_paid_possible": True,
            },
            {
                "free_tier": {"input": "Free of charge", "output": "Free of charge"},
                "paid_tier": {"input": "0.75", "output": "3.75"},
            },
            quota(),
            evidence_source="fixture",
        )
        self.assertTrue(result["free_program_exists"])
        self.assertFalse(result["zero_cost_verified"])
        self.assertIn("CURRENT_ACCOUNT_NOT_ELIGIBLE", result["blockers"])

    def test_groq_paid_model_price_can_coexist_with_free_plan_limits(self):
        result = resolve_free_evidence(
            "groq",
            "qwen/qwen3.8-27b",
            [{"id": "qwen/qwen3.8-27b", "pricing": {"prompt": "0.80", "completion": "4.00"}}],
            {
                "current_org_plan": "FREE",
                "automatic_paid_transition_possible": False,
                "fallback_to_paid_possible": False,
            },
            {
                "free_plan_limits": {"qwen/qwen3.8-27b": {"rpm": 30, "rpd": 1000}},
                "paid_tier": {"input": "0.80", "output": "4.00"},
            },
            quota(),
            evidence_source="groq-official-limits-fixture",
        )
        self.assertEqual(result["status"], "VERIFIED")
        self.assertTrue(result["free_program_exists"])
        self.assertTrue(result["paid_price_exists"])
        self.assertEqual(result["input_price"], "0")
        self.assertTrue(result["zero_cost_verified"])

    def test_groq_unknown_org_plan_is_not_zero_cost(self):
        result = resolve_free_evidence(
            "groq",
            "qwen/qwen3.8-27b",
            [{"id": "qwen/qwen3.8-27b"}],
            {
                "automatic_paid_transition_possible": False,
                "fallback_to_paid_possible": False,
            },
            {"free_plan_limits": {"qwen/qwen3.8-27b": {"rpm": 30}}},
            quota(),
            evidence_source="fixture",
        )
        self.assertTrue(result["free_program_exists"])
        self.assertFalse(result["zero_cost_verified"])
        self.assertIn("CURRENT_ACCOUNT_ELIGIBILITY_UNKNOWN", result["blockers"])

    def test_nvidia_selects_free_endpoint_over_partner_route(self):
        result = resolve_free_evidence(
            "nvidia",
            "deepseek-ai/deepseek-v4-flash-0731",
            [{"id": "deepseek-ai/deepseek-v4-flash-0731"}],
            {
                "current_account_eligible": True,
                "automatic_paid_transition_possible": False,
                "fallback_to_paid_possible": False,
            },
            {
                "free_endpoint_available": True,
                "available_access_routes": ["FREE_ENDPOINT", "PARTNER_ENDPOINT"],
                "free_price_verified": True,
            },
            quota(),
            evidence_source="nvidia-build-official-fixture",
        )
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["selected_route"], "FREE_ENDPOINT")
        self.assertTrue(result["paid_fallback_disabled"])

    def test_openrouter_colon_suffix_is_exact_and_paid_sibling_is_not_selected(self):
        model = "z-ai/glm-5.3-flash:free"
        result = resolve_free_evidence(
            "openrouter",
            model,
            [{"id": model, "pricing": {"prompt": "0", "completion": "0"}}],
            {
                "current_account_eligible": True,
                "automatic_paid_transition_possible": False,
            },
            {
                "provider_allow_fallbacks": False,
                "api_catalog_exists": True,
            },
            quota(),
            evidence_source="openrouter-api-fixture",
        )
        self.assertEqual(result["model_id"], model)
        self.assertTrue(result["model_id"].endswith(":free"))
        self.assertEqual(result["selected_route"], "FREE_MODEL_ENDPOINT")
        self.assertTrue(result["zero_cost_verified"])

    def test_openrouter_public_page_and_api_catalog_conflict_fails_closed(self):
        result = resolve_free_evidence(
            "openrouter",
            "z-ai/glm-5.3-flash:free",
            [{"id": "z-ai/glm-5.3-flash", "pricing": {"prompt": "0.000000075", "completion": "0.00000025"}}],
            {"current_account_eligible": True, "automatic_paid_transition_possible": False},
            {
                "public_page_exists": True,
                "api_catalog_exists": False,
                "provider_allow_fallbacks": False,
            },
            quota(),
            evidence_source="openrouter-page-and-api-fixture",
        )
        self.assertEqual(result["status"], "INCONSISTENT")
        self.assertIn("CATALOG_INCONSISTENCY", result["blockers"])
        self.assertFalse(result["zero_cost_verified"])

    def test_large_catalog_and_colon_suffix_are_not_truncated_or_normalized_away(self):
        model = "z-ai/glm-5.3-flash:free"
        catalog = [{"id": f"vendor/model-{index}"} for index in range(600)]
        catalog.append({"id": model, "pricing": {"prompt": "0", "completion": "0"}})
        result = resolve_free_evidence(
            "openrouter",
            model,
            catalog,
            {"current_account_eligible": True, "automatic_paid_transition_possible": False},
            {"api_catalog_exists": True, "provider_allow_fallbacks": False},
            quota(),
            evidence_source="openrouter-paginated-fixture",
            evidence_timestamp="2026-09-09T00:00:00Z",
        )
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["model_id"], model)
        self.assertTrue(result["catalog_id_present"])

    def test_duplicate_catalog_metadata_conflict_is_inconsistent(self):
        result = resolve_free_evidence(
            "openrouter",
            "z-ai/glm-5.3-flash:free",
            [
                {"id": "z-ai/glm-5.3-flash:free", "pricing": {"prompt": "0", "completion": "0"}},
                {"id": "z-ai/glm-5.3-flash:free", "pricing": {"prompt": "0.1", "completion": "0.2"}},
            ],
            {"current_account_eligible": True, "automatic_paid_transition_possible": False},
            {"api_catalog_exists": True, "provider_allow_fallbacks": False},
            quota(),
            evidence_source="duplicate-catalog-fixture",
        )
        self.assertEqual(result["status"], "INCONSISTENT")
        self.assertIn("DUPLICATE_CATALOG_METADATA_CONFLICT", result["blockers"])

    def test_missing_evidence_source_blocks_even_zero_priced_current_data(self):
        result = resolve_free_evidence(
            "google",
            "gemini-3.8-flash",
            [{"id": "gemini-3.8-flash"}],
            {"current_account_tier": "FREE", "automatic_paid_transition_possible": False, "fallback_to_paid_possible": False},
            {"free_tier": {"input": "0", "output": "0"}},
            quota(),
            evidence_timestamp="2026-09-09T00:00:00Z",
        )
        self.assertFalse(result["zero_cost_verified"])
        self.assertIn("EVIDENCE_SOURCE_REQUIRED", result["blockers"])


if __name__ == "__main__":
    unittest.main()
