import unittest

from scripts import benchmark_free_workers, run_nvidia_worker_expansion, worker_canary


class WorkerOutputResilienceTests(unittest.TestCase):
    def test_benchmark_accepts_fenced_or_prefaced_json(self):
        fenced = '```json\n{"fix":"return a + b","bug":"subtracts"}\n```'
        prefaced = 'Result:\n{"fix":"return a + b","bug":"subtracts"}'
        self.assertEqual(benchmark_free_workers._parse_json_object(fenced)["fix"], "return a + b")
        self.assertEqual(benchmark_free_workers._parse_json_object(prefaced)["fix"], "return a + b")
        self.assertEqual(benchmark_free_workers.RESPONSE_REASONING["effort"], "minimal")
        self.assertTrue(benchmark_free_workers.RESPONSE_REASONING["exclude"])

    def test_canary_accepts_fenced_or_prefaced_json(self):
        fenced = '```json\n{"severity":"high","finding":"access allowed"}\n```'
        prefaced = 'JSON follows: {"severity":"high","finding":"access allowed"}'
        self.assertEqual(worker_canary._parse_json_object(fenced)["severity"], "high")
        self.assertEqual(worker_canary._parse_json_object(prefaced)["severity"], "high")
        self.assertEqual(worker_canary.RESPONSE_REASONING["effort"], "minimal")
        self.assertTrue(worker_canary.RESPONSE_REASONING["exclude"])


class NvidiaWorkerContextIsolationTests(unittest.TestCase):
    def test_worker_expansion_context_does_not_inherit_google_files(self):
        files = set(run_nvidia_worker_expansion.EXPANSION_FILES)
        markers = set(run_nvidia_worker_expansion.WORKER_CONTEXT_MARKERS)
        self.assertTrue(files)
        self.assertTrue(markers)
        self.assertTrue(markers <= files)
        self.assertFalse(any("google" in path.lower() for path in files))
        self.assertFalse(any("google" in path.lower() for path in markers))
        self.assertIn("scripts/china_bulk_coding_pool.py", files)
        self.assertIn("scripts/worker_canary.py", files)


if __name__ == "__main__":
    unittest.main()
