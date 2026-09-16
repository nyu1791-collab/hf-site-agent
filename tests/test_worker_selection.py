import unittest

from scripts.worker_selection import WorkerSelectionError, catalog_worker_candidates, select_free_worker


def worker_entry(model_id, *, capabilities=None, context_length=65536, parameters=None, prompt="0", completion="0"):
    return {
        "id": model_id,
        "pricing": {"prompt": prompt, "completion": completion},
        "context_length": context_length,
        "supported_parameters": parameters or ["tools", "tool_choice", "structured_outputs"],
        "capability_tags": capabilities or [],
    }


def active_probe(model_id, *, response_model=None, status="FREE_ACTIVE", cost="0", credits=True, fallback=False, allow_fallbacks=False):
    return {
        "status": status,
        "requested_model": model_id,
        "response_model": response_model or model_id,
        "usage_cost": cost,
        "credits_unchanged": credits,
        "fallback_used": fallback,
        "provider_allow_fallbacks": allow_fallbacks,
    }


class WorkerSelectionTests(unittest.TestCase):
    def test_selects_verified_catalog_worker_deterministically(self):
        catalog = [
            worker_entry("vendor/small:free", capabilities=["coding"], context_length=32768),
            worker_entry("vendor/large:free", capabilities=["coding"], context_length=131072),
        ]
        probes = {model["id"]: active_probe(model["id"]) for model in catalog}
        result = select_free_worker(catalog, probes, "CODING_WORKER")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["model"], "vendor/large:free")
        self.assertTrue(result["role_benchmark_required"])
        self.assertFalse(result["paid_fallback"])
        self.assertFalse(result["generic_router"])

    def test_missing_nonstandard_capability_tags_do_not_block_candidate(self):
        entry = worker_entry("vendor/current-catalog:free", capabilities=[], context_length=65536)
        candidates = catalog_worker_candidates([entry], "CODING_WORKER")
        self.assertEqual([item["model"] for item in candidates], [entry["id"]])
        self.assertFalse(candidates[0]["capability_hint_match"])
        probes = {entry["id"]: active_probe(entry["id"])}
        result = select_free_worker([entry], probes, "CODING_WORKER")
        self.assertEqual(result["status"], "ready")
        self.assertFalse(result["capability_hint_match"])
        self.assertTrue(result["role_benchmark_required"])

    def test_optional_native_features_are_preference_not_hard_gate(self):
        no_native = worker_entry(
            "vendor/plain:free",
            capabilities=[],
            context_length=131072,
            parameters=["temperature"],
        )
        feature_rich = worker_entry(
            "vendor/rich:free",
            capabilities=[],
            context_length=32768,
            parameters=["tools", "tool_choice", "response_format"],
        )
        candidates = catalog_worker_candidates([no_native, feature_rich], "CODING_WORKER")
        self.assertEqual([item["model"] for item in candidates], [feature_rich["id"], no_native["id"]])
        self.assertEqual(candidates[0]["preferred_feature_hits"], 2)
        self.assertEqual(candidates[1]["preferred_feature_hits"], 0)
        probes = {entry["id"]: active_probe(entry["id"]) for entry in (no_native, feature_rich)}
        result = select_free_worker([no_native, feature_rich], probes, "CODING_WORKER")
        self.assertEqual(result["model"], feature_rich["id"])

    def test_explicit_role_tag_is_strong_preference_hint(self):
        tagged = worker_entry("vendor/tagged:free", capabilities=["coding"], context_length=32768)
        untagged = worker_entry("vendor/untagged:free", capabilities=[], context_length=131072)
        probes = {entry["id"]: active_probe(entry["id"]) for entry in (tagged, untagged)}
        result = select_free_worker([untagged, tagged], probes, "CODING_WORKER")
        self.assertEqual(result["model"], tagged["id"])
        self.assertTrue(result["capability_hint_match"])

    def test_catalog_only_candidate_is_not_active(self):
        entry = worker_entry("vendor/candidate:free", capabilities=["general"])
        result = select_free_worker([entry], {}, "GENERAL_WORKER")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "no_current_verified_free_worker")

    def test_paid_or_non_free_id_is_rejected(self):
        entries = [
            worker_entry("vendor/paid", capabilities=["general"], prompt="0", completion="0"),
            worker_entry("vendor/free-with-paid-price:free", capabilities=["general"], prompt="0.1"),
        ]
        probes = {entry["id"]: active_probe(entry["id"]) for entry in entries}
        result = select_free_worker(entries, probes, "GENERAL_WORKER")
        self.assertEqual(result["status"], "blocked")

    def test_probe_mismatch_cost_credit_or_fallback_blocks_worker(self):
        entry = worker_entry("vendor/candidate:free", capabilities=["general"])
        for probe in (
            active_probe(entry["id"], response_model="vendor/other:free"),
            active_probe(entry["id"], cost="0.01"),
            active_probe(entry["id"], credits=False),
            active_probe(entry["id"], fallback=True),
            active_probe(entry["id"], allow_fallbacks=True),
        ):
            result = select_free_worker([entry], {entry["id"]: probe}, "GENERAL_WORKER")
            self.assertEqual(result["status"], "blocked")

    def test_generic_free_router_is_never_used(self):
        result = select_free_worker(
            [worker_entry("openrouter/free", capabilities=["general"])],
            {"openrouter/free": active_probe("openrouter/free")},
            "GENERAL_WORKER",
            requested_model="openrouter/free",
        )
        self.assertEqual(result["reason"], "generic_free_router_forbidden")

    def test_requested_plain_model_can_enter_probe_stage_without_tool_support(self):
        plain = worker_entry(
            "vendor/plain:free",
            capabilities=["general"],
            parameters=["temperature"],
        )
        coding = worker_entry("vendor/coding:free", capabilities=["coding"])
        probes = {entry["id"]: active_probe(entry["id"]) for entry in (plain, coding)}
        result = select_free_worker([plain, coding], probes, "CODING_WORKER", requested_model=plain["id"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["model"], plain["id"])
        self.assertEqual(result["preferred_feature_hits"], 0)
        result = select_free_worker([plain, coding], probes, "CODING_WORKER", requested_model=coding["id"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["model"], coding["id"])

    def test_unknown_worker_role_is_rejected(self):
        with self.assertRaises(WorkerSelectionError):
            select_free_worker([], {}, "COMMANDER")


if __name__ == "__main__":
    unittest.main()
