import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.openrouter_worker_health import (
    load_recent_evidence,
    merge_proven_into_candidates,
    rank_candidates,
)


class OpenRouterWorkerHealthTests(unittest.TestCase):
    def test_expired_evidence_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "evidence.json"
            path.write_text(json.dumps({
                "models": {
                    "a:free": {
                        "successes": 1,
                        "valid_until_utc": "2026-09-20T00:00:00Z",
                    },
                    "b:free": {
                        "successes": 1,
                        "valid_until_utc": "2026-09-22T00:00:00Z",
                    },
                }
            }), encoding="utf-8")
            out = load_recent_evidence(
                now=datetime(2026, 9, 21, tzinfo=timezone.utc),
                path=path,
            )
        self.assertNotIn("a:free", out)
        self.assertIn("b:free", out)

    def test_fast_proven_success_beats_unknown(self):
        evidence = {
            "fast:free": {
                "successes": 1,
                "quality_failures": 0,
                "rate_limits": 0,
                "avg_latency_ms": 2500,
            }
        }
        ranked = rank_candidates(["unknown:free", "fast:free"], evidence=evidence)
        self.assertEqual(ranked[0], "fast:free")

    def test_very_slow_success_does_not_beat_unknown(self):
        evidence = {
            "slow:free": {
                "successes": 1,
                "quality_failures": 0,
                "rate_limits": 0,
                "avg_latency_ms": 95000,
            }
        }
        ranked = rank_candidates(["slow:free", "unknown:free"], evidence=evidence)
        self.assertEqual(ranked[0], "unknown:free")

    def test_rate_limited_model_is_deprioritized(self):
        evidence = {
            "limited:free": {
                "successes": 5,
                "quality_failures": 0,
                "rate_limits": 1,
                "avg_latency_ms": 100,
            }
        }
        ranked = rank_candidates(["limited:free", "unknown:free"], evidence=evidence)
        self.assertEqual(ranked[0], "unknown:free")

    def test_proven_model_is_merged_into_shortlist(self):
        evidence = {
            "proven:free": {
                "successes": 1,
                "quality_failures": 0,
                "rate_limits": 0,
                "avg_latency_ms": 2000,
            }
        }
        out = merge_proven_into_candidates(
            ["a:free", "b:free", "c:free", "d:free", "e:free"],
            catalog_model_ids={"a:free", "b:free", "c:free", "d:free", "e:free", "proven:free"},
            evidence=evidence,
            max_candidates=4,
        )
        self.assertIn("proven:free", out)
        self.assertEqual(out[0], "proven:free")


if __name__ == "__main__":
    unittest.main()
