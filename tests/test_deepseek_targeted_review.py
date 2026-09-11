from __future__ import annotations

import copy
import unittest

from scripts import deepseek_specialist_trial as base
from scripts.deepseek_targeted_review import (
    DEFAULT_MANIFEST,
    _load_manifest,
    _targeted_config,
    _validate_safety,
    _validated_context_markers,
    _validated_task,
)


SUPPORTED_ROLES = {
    "CODING_DEEP",
    "DEBUGGING",
    "CODE_REVIEW",
    "ARCHITECTURE",
    "TEST_STRATEGY",
    "INTEGRATION_REVIEW",
}
ALLOWED_CONTEXT_PREFIXES = ("scripts/", "tests/", "config/", "docs/")


class DeepSeekTargetedReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = base._load_json(base.DEFAULT_CONFIG)
        self.manifest = _load_manifest(DEFAULT_MANIFEST)

    def test_manifest_is_exactly_one_serial_call_under_five_cents(self) -> None:
        _validate_safety(self.manifest)
        tuned = _targeted_config(self.config, self.manifest)
        budget = tuned["trial_budget"]
        self.assertEqual(budget["max_calls"], 1)
        self.assertEqual(budget["max_parallel_calls"], 1)
        self.assertLessEqual(budget["max_estimated_cost_usd"], 0.05)
        self.assertTrue(budget["stop_before_estimated_budget_exceeded"])

    def test_context_is_repo_bounded_and_task_is_supported(self) -> None:
        context = _validated_context_markers(self.manifest)
        task = _validated_task(self.manifest)
        self.assertTrue(context)
        self.assertLessEqual(len(context), 8)
        for path, markers in context.items():
            self.assertTrue(path.startswith(ALLOWED_CONTEXT_PREFIXES), path)
            self.assertTrue(markers)
            self.assertLessEqual(len(markers), 8)
        self.assertIn(task["role"], SUPPORTED_ROLES)
        self.assertTrue(task["task_id"])
        self.assertTrue(task["objective"])

    def test_any_authority_escalation_is_rejected(self) -> None:
        for boundary in ("repository_write", "merge", "deploy", "publish", "secret_mutation", "production_routing", "generic_paid_fallback", "auto_top_up"):
            changed = copy.deepcopy(self.manifest)
            changed["safety_boundary"][boundary] = True
            with self.assertRaises(ValueError):
                _validate_safety(changed)

    def test_multiple_calls_are_rejected(self) -> None:
        changed = copy.deepcopy(self.manifest)
        changed["max_calls"] = 2
        with self.assertRaises(ValueError):
            _validate_safety(changed)


if __name__ == "__main__":
    unittest.main()
