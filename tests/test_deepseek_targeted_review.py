from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import time
import unittest
from unittest.mock import patch

from scripts import deepseek_specialist_trial as base
from scripts import deepseek_targeted_review as targeted
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
SAFETY_BOUNDARY = {
    "repository_write": False,
    "merge": False,
    "deploy": False,
    "publish": False,
    "secret_mutation": False,
    "production_routing": False,
    "generic_paid_fallback": False,
    "auto_top_up": False,
}


def _isolation_manifest(task_id: str, marker: str) -> dict[str, object]:
    return {
        "focus_id": f"focus-{task_id}",
        "paid_execution_approved": True,
        "max_calls": 1,
        "max_parallel_calls": 1,
        "max_estimated_cost_usd": 0.01,
        "safety_boundary": dict(SAFETY_BOUNDARY),
        "context_markers": {
            "scripts/deepseek_targeted_review.py": [marker],
        },
        "task": {
            "task_id": task_id,
            "role": "CODE_REVIEW",
            "objective": f"Review isolation for {task_id}",
        },
    }


def _isolation_config() -> dict[str, object]:
    return {
        "trial_budget": {
            "max_calls": 6,
            "max_parallel_calls": 3,
            "max_estimated_cost_usd": 1.0,
        }
    }


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
        for boundary in (
            "repository_write",
            "merge",
            "deploy",
            "publish",
            "secret_mutation",
            "production_routing",
            "generic_paid_fallback",
            "auto_top_up",
        ):
            changed = copy.deepcopy(self.manifest)
            changed["safety_boundary"][boundary] = True
            with self.assertRaises(ValueError):
                _validate_safety(changed)

    def test_multiple_calls_are_rejected(self) -> None:
        changed = copy.deepcopy(self.manifest)
        changed["max_calls"] = 2
        with self.assertRaises(ValueError):
            _validate_safety(changed)

    def test_parallel_reviews_do_not_cross_contaminate_shared_overrides(self) -> None:
        observed: list[tuple[str, tuple[str, ...]]] = []

        def fake_trial(**_: object) -> dict[str, object]:
            # Observe after a small overlap window. Without the review lock,
            # a second caller can replace the shared task/context first.
            time.sleep(0.03)
            task_id = str(base.TASKS[0]["task_id"])
            markers = tuple(base.COMMON_CONTEXT_MARKERS["scripts/deepseek_targeted_review.py"])
            observed.append((task_id, markers))
            return {
                "status": "TRIAL_DRY_RUN",
                "requested_model": "deepseek-flash",
                "selected_task_count": 1,
            }

        manifests = (
            _isolation_manifest("lane-a", "marker-a"),
            _isolation_manifest("lane-b", "marker-b"),
        )
        with patch.object(targeted.v4, "run_trial", side_effect=fake_trial):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(
                        targeted.run_targeted_review,
                        config=_isolation_config(),
                        manifest=manifest,
                        api_key="",
                        network=False,
                        confirm="",
                    )
                    for manifest in manifests
                ]
                reports = [future.result(timeout=3) for future in futures]

        self.assertEqual(
            {str(report["focus_id"]) for report in reports},
            {"focus-lane-a", "focus-lane-b"},
        )
        self.assertEqual(
            set(observed),
            {
                ("lane-a", ("marker-a",)),
                ("lane-b", ("marker-b",)),
            },
        )

    def test_shared_overrides_are_restored_when_trial_raises(self) -> None:
        original_tasks = base.TASKS
        original_markers = base.COMMON_CONTEXT_MARKERS

        with patch.object(targeted.v4, "run_trial", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                targeted.run_targeted_review(
                    config=_isolation_config(),
                    manifest=_isolation_manifest("restore", "restore-marker"),
                    api_key="",
                    network=False,
                    confirm="",
                )

        self.assertIs(base.TASKS, original_tasks)
        self.assertIs(base.COMMON_CONTEXT_MARKERS, original_markers)


if __name__ == "__main__":
    unittest.main()
