import unittest

from scripts.worker_benchmark_ranking import (
    benchmark_record_valid,
    rank_benchmarked_workers,
    select_benchmarked_worker,
)


def record(model, role, *, quality, schema, latency, tokens, revision=0.1, error=0.02, status="BENCHMARK_OK"):
    return {
        "status": status,
        "model": model,
        "worker_role": role,
        "task_quality": quality,
        "schema_success_rate": schema,
        "latency_ms": latency,
        "tokens_per_success": tokens,
        "revision_rate": revision,
        "error_rate": error,
    }


class WorkerBenchmarkRankingTests(unittest.TestCase):
    def test_invalid_or_failed_benchmark_is_never_ranked(self):
        bad = record("vendor/bad:free", "GENERAL_WORKER", quality=0.9, schema=0.9, latency=100, tokens=100, status="BENCHMARK_FAILED")
        self.assertFalse(benchmark_record_valid(bad))
        self.assertEqual(rank_benchmarked_workers([bad], "GENERAL_WORKER"), [])

    def test_general_worker_prefers_quality_over_raw_speed(self):
        records = [
            record("vendor/fast:free", "GENERAL_WORKER", quality=0.65, schema=0.92, latency=100, tokens=120),
            record("vendor/strong:free", "GENERAL_WORKER", quality=0.95, schema=0.98, latency=300, tokens=220),
        ]
        ranking = rank_benchmarked_workers(records, "GENERAL_WORKER")
        self.assertEqual(ranking[0]["model"], "vendor/strong:free")

    def test_fast_worker_can_prefer_faster_more_efficient_candidate(self):
        records = [
            record("vendor/fast:free", "FAST_WORKER", quality=0.80, schema=0.96, latency=80, tokens=80),
            record("vendor/slow:free", "FAST_WORKER", quality=0.92, schema=0.98, latency=600, tokens=400),
        ]
        ranking = rank_benchmarked_workers(records, "FAST_WORKER")
        self.assertEqual(ranking[0]["model"], "vendor/fast:free")

    def test_ranking_is_deterministic_and_uses_stable_tie_breaker(self):
        records = [
            record("vendor/b:free", "REVIEW_WORKER", quality=0.9, schema=0.9, latency=100, tokens=100),
            record("vendor/a:free", "REVIEW_WORKER", quality=0.9, schema=0.9, latency=100, tokens=100),
        ]
        first = rank_benchmarked_workers(records, "REVIEW_WORKER")
        second = rank_benchmarked_workers(list(reversed(records)), "REVIEW_WORKER")
        self.assertEqual(first, second)
        self.assertEqual(first[0]["model"], "vendor/a:free")

    def test_selection_requires_commander_review_and_does_not_auto_activate(self):
        result = select_benchmarked_worker(
            [record("vendor/code:free", "CODING_WORKER", quality=0.95, schema=0.99, latency=180, tokens=200)],
            "CODING_WORKER",
        )
        self.assertEqual(result["status"], "ready_for_commander_review")
        self.assertEqual(result["model"], "vendor/code:free")
        self.assertFalse(result["automatic_activation"])


if __name__ == "__main__":
    unittest.main()
