import unittest

from scripts.provider_route_matrix import approved_fallback, load_matrix, route_for_task


class ProviderRouteMatrixTests(unittest.TestCase):
    def setUp(self):
        self.matrix=load_matrix()

    def test_unknown_task_fails_closed(self):
        out=route_for_task("UNREGISTERED_TASK",self.matrix)
        self.assertEqual(out["reason"],"BLOCKED_NO_APPROVED_FALLBACK")
        self.assertFalse(out["execution_allowed"])

    def test_news_fallback_is_direct_deepseek_official(self):
        out=approved_fallback("RSS_NEWS_SCRIPT",self.matrix)
        self.assertTrue(out["execution_allowed"])
        self.assertEqual(out["fallback_provider"],"deepseek_official")
        self.assertEqual(out["fallback_model"],"deepseek-flash")

    def test_openrouter_worker_has_no_cross_provider_fallback(self):
        out=approved_fallback("OPENROUTER_WORKER",self.matrix)
        self.assertFalse(out["execution_allowed"])
        self.assertEqual(out["reason"],"BLOCKED_NO_APPROVED_FALLBACK")

    def test_no_route_uses_openrouter_as_fallback(self):
        for row in self.matrix["task_routes"]:
            self.assertNotEqual(str(row.get("fallback_provider") or "").lower(),"openrouter")


if __name__=="__main__":
    unittest.main()
