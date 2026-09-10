import unittest

from scripts.china_bulk_coding_pool import build_bulk_coding_pool, china_coding_family


def probe(*models):
    return {
        "results": [
            {
                "status": "FREE_ACTIVE",
                "requested_model": model,
                "response_model": model,
                "fallback_used": False,
                "provider_allow_fallbacks": False,
            }
            for model in models
        ]
    }


def row(model, *, rank=1, score=0.95, quality=1.0, schema=1.0, latency=200.0, tokens=100.0, revision=0.0, error=0.0):
    return {
        "model": model,
        "rank": rank,
        "score": score,
        "task_quality": quality,
        "schema_success_rate": schema,
        "latency_ms": latency,
        "tokens_per_success": tokens,
        "revision_rate": revision,
        "error_rate": error,
    }


class ChinaBulkCodingPoolTests(unittest.TestCase):
    def test_family_detection_is_prefix_based(self):
        self.assertEqual(china_coding_family("qwen/qwen3-coder:free"), "QWEN")
        self.assertEqual(china_coding_family("deepseek/deepseek-chat:free"), "DEEPSEEK")
        self.assertEqual(china_coding_family("z-ai/glm-4:free"), "GLM")
        self.assertEqual(china_coding_family("moonshotai/kimi:free"), "KIMI")
        self.assertEqual(china_coding_family("vendor/not-china:free"), "")

    def test_only_current_exact_free_coding_quality_models_are_admitted(self):
        good = "qwen/good:free"
        weak = "deepseek/weak:free"
        not_verified = "z-ai/unverified:free"
        benchmark = {
            "rankings": {
                "CODING_WORKER": [
                    row(good, score=0.97, latency=140, tokens=80),
                    row(weak, rank=2, score=0.90, quality=0.70),
                    row(not_verified, rank=3, score=0.95),
                ]
            }
        }
        result = build_bulk_coding_pool(probe=probe(good, weak), benchmark=benchmark)
        self.assertEqual(result["status"], "BULK_CODING_POOL_READY")
        self.assertEqual([item["model"] for item in result["models"]], [good])
        self.assertEqual(result["additional_benchmark_calls"], 0)
        self.assertFalse(result["paid_fallback"])
        self.assertFalse(result["provider_automatic_fallback"])

    def test_efficiency_breaks_ties_after_quality_floor(self):
        fast = "qwen/fast:free"
        lean = "deepseek/lean:free"
        benchmark = {
            "rankings": {
                "CODING_WORKER": [
                    row(fast, score=0.95, latency=80, tokens=160),
                    row(lean, rank=2, score=0.95, latency=240, tokens=60),
                ]
            }
        }
        result = build_bulk_coding_pool(probe=probe(fast, lean), benchmark=benchmark)
        self.assertEqual(result["model_count"], 2)
        self.assertEqual(set(item["family"] for item in result["models"]), {"QWEN", "DEEPSEEK"})
        self.assertLessEqual(result["recommended_parallelism"], 4)

    def test_non_china_model_does_not_enter_preference_pool(self):
        model = "nvidia/nemotron:free"
        benchmark = {"rankings": {"CODING_WORKER": [row(model)]}}
        result = build_bulk_coding_pool(probe=probe(model), benchmark=benchmark)
        self.assertEqual(result["status"], "BULK_CODING_POOL_BLOCKED")
        self.assertEqual(result["models"], [])

    def test_canary_marks_matching_coding_model(self):
        model = "inclusionai/ling-code:free"
        benchmark = {"rankings": {"CODING_WORKER": [row(model)]}}
        canary = {"results": {"CODING_WORKER": {"status": "CANARY_OK", "model": model}}}
        result = build_bulk_coding_pool(probe=probe(model), benchmark=benchmark, canary=canary)
        self.assertTrue(result["models"][0]["coding_canary_verified"])


if __name__ == "__main__":
    unittest.main()
