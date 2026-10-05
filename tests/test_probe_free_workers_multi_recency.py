import unittest

from scripts.probe_free_workers_multi import (
    CANDIDATES_PER_ROLE,
    RECENT_CANDIDATES_PER_ROLE,
    _portfolio_candidates,
)


class ProbeFreeWorkersMultiRecencyTests(unittest.TestCase):
    def test_portfolio_preserves_quality_head_and_includes_newest_candidates(self):
        candidates = []
        for index in range(CANDIDATES_PER_ROLE + 12):
            candidates.append({
                "model": f"vendor/model-{index}:free",
                "catalog_created_epoch": 1_000 + index,
            })

        selected, recent = _portfolio_candidates(candidates)
        selected_ids = [item["model"] for item in selected]

        quality_budget = CANDIDATES_PER_ROLE - RECENT_CANDIDATES_PER_ROLE
        self.assertEqual(selected_ids[:quality_budget], [
            f"vendor/model-{index}:free" for index in range(quality_budget)
        ])
        self.assertEqual(len(selected_ids), CANDIDATES_PER_ROLE)
        self.assertEqual(recent, [
            f"vendor/model-{index}:free"
            for index in range(len(candidates) - 1, len(candidates) - RECENT_CANDIDATES_PER_ROLE - 1, -1)
        ])
        for model in recent:
            self.assertIn(model, selected_ids)

    def test_missing_created_metadata_never_displaces_normal_candidates(self):
        candidates = [
            {"model": f"vendor/model-{index}:free", "catalog_created_epoch": None}
            for index in range(CANDIDATES_PER_ROLE + 3)
        ]
        selected, recent = _portfolio_candidates(candidates)
        self.assertEqual(recent, [])
        self.assertEqual(
            [item["model"] for item in selected],
            [f"vendor/model-{index}:free" for index in range(CANDIDATES_PER_ROLE)],
        )


if __name__ == "__main__":
    unittest.main()
