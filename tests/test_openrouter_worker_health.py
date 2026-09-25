import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.openrouter_worker_health import (
    evidence_quality_summary,
    load_recent_evidence,
    merge_proven_into_candidates,
    rank_candidates,
)


class OpenRouterWorkerHealthTests(unittest.TestCase):
    def test_quality_summary_keeps_sample_count_separate_from_latency(self):
        summary = evidence_quality_summary({
            "successes": 3,
            "quality_failures": 1,
            "rate_limits": 0,
            "avg_latency_ms": 1,
        })
        self.assertEqual(summary["observed_outcomes"], 4)
        self.assertEqual(summary["quality_pass_rate"], 0.75)
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

    def test_proven_model_cannot_expand_prevalidated_shortlist(self):
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
        self.assertNotIn("proven:free", out)
        self.assertEqual(out, ["a:free", "b:free", "c:free", "d:free"])

    def test_health_cannot_promote_paid_catalog_entry(self):
        out = merge_proven_into_candidates(
            ["a:free"],
            catalog_model_ids={"a:free", "paid/model"},
            evidence={"paid/model": {"successes": 3, "quality_failures": 0, "rate_limits": 0}},
        )
        self.assertEqual(out, ["a:free"])

    def test_missing_or_invalid_expiry_is_ignored(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "evidence.json"
            path.write_text(json.dumps({"models": {
                "missing:free": {"successes": 1},
                "invalid:free": {"successes": 1, "valid_until_utc": "not-a-date"},
                "fresh:free": {"successes": 1, "valid_until_utc": "2026-09-22T01:00:00Z"},
            }}), encoding="utf-8")
            out = load_recent_evidence(now=datetime(2026, 9, 21, tzinfo=timezone.utc), path=path)
        self.assertEqual(set(out), {"fresh:free"})

    def test_domain_specific_failure_does_not_poison_other_domain(self):
        evidence = {
            "specialist:free": {
                "successes": 2,
                "quality_failures": 1,
                "rate_limits": 0,
                "avg_latency_ms": 4000,
                "domain_stats": {
                    "PLANNING_ORCHESTRATION": {
                        "successes": 1,
                        "quality_failures": 0,
                        "rate_limits": 0,
                        "avg_latency_ms": 1200,
                    },
                    "QUALITY_REVIEW": {
                        "successes": 0,
                        "quality_failures": 1,
                        "rate_limits": 0,
                        "avg_latency_ms": 9000,
                    },
                },
            }
        }
        planning = rank_candidates(
            ["unknown:free", "specialist:free"],
            evidence=evidence,
            domain="PLANNING_ORCHESTRATION",
        )
        review = rank_candidates(
            ["unknown:free", "specialist:free"],
            evidence=evidence,
            domain="QUALITY_REVIEW",
        )
        self.assertEqual(planning[0], "specialist:free")
        self.assertEqual(review[0], "unknown:free")

    def test_domain_success_can_override_global_mixed_record(self):
        evidence = {
            "mixed:free": {
                "successes": 2,
                "quality_failures": 1,
                "rate_limits": 0,
                "avg_latency_ms": 4900,
                "domain_stats": {
                    "PLANNING_ORCHESTRATION": {
                        "successes": 1,
                        "quality_failures": 0,
                        "rate_limits": 0,
                        "avg_latency_ms": 2800,
                    },
                    "QUALITY_REVIEW": {
                        "successes": 0,
                        "quality_failures": 1,
                        "rate_limits": 0,
                        "avg_latency_ms": 10000,
                    },
                },
            }
        }
        planning = rank_candidates(
            ["unknown:free", "mixed:free"],
            evidence=evidence,
            domain="PLANNING_ORCHESTRATION",
        )
        review = rank_candidates(
            ["mixed:free", "unknown:free"],
            evidence=evidence,
            domain="QUALITY_REVIEW",
        )
        self.assertEqual(planning[0], "mixed:free")
        self.assertEqual(review[0], "unknown:free")


if __name__ == "__main__":
    unittest.main()
