import unittest
from unittest.mock import patch

from scripts.jev_mixed_router import decide_many_lean_fast


def rec(i):
    return {
        "id": f"task_{i:02d}",
        "task_summary": "Task",
        "candidate_models": ["a:free", "b:free"],
        "candidate_profiles": {"a:free": "A", "b:free": "B"},
        "quota_pressure": "AMPLE",
        "lane": "GENERAL_REASONING",
        "allow_third": False,
        "shared_mutable_state": False,
        "high_risk": False,
    }


class JevMixedRouterTests(unittest.TestCase):
    def test_empty_is_noop(self):
        out = decide_many_lean_fast(
            lean_records=[],
            fast_records=[],
            api_key="x",
            catalog_entries=[],
        )
        self.assertEqual(out["status"], "JEV_LEAN_FAST_MANY_OK")
        self.assertEqual(out["batch_count"], 0)

    def test_combined_surfaces_share_twenty_record_chunks(self):
        lean = [rec(i) for i in range(25)]
        fast = [rec(i + 25) for i in range(10)]

        def fake_batch(*, lean_records, fast_records, **kwargs):
            rows = [*lean_records, *fast_records]
            return {
                "status": "JEV_LEAN_FAST_BATCH_OK",
                "latency_ms": 100.0,
                "question_count": len(rows) * 2,
                "decisions": {
                    row["id"]: {
                        "workers": ["a:free"],
                        "action": "EXECUTE",
                        "low_confidence": False,
                    }
                    for row in rows
                },
                "usage": {"cost": 0.001},
            }

        with patch("scripts.jev_mixed_router.decide_lean_fast_batch", side_effect=fake_batch) as call:
            out = decide_many_lean_fast(
                lean_records=lean,
                fast_records=fast,
                api_key="x",
                catalog_entries=[],
            )
        self.assertEqual(out["status"], "JEV_LEAN_FAST_MANY_OK")
        self.assertEqual(out["record_count"], 35)
        self.assertEqual(out["batch_count"], 2)
        self.assertEqual(len(out["decisions"]), 35)
        self.assertEqual(call.call_count, 2)
        sizes = sorted(
            len(c.kwargs["lean_records"]) + len(c.kwargs["fast_records"])
            for c in call.call_args_list
        )
        self.assertEqual(sizes, [15, 20])


if __name__ == "__main__":
    unittest.main()
