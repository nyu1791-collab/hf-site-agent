import subprocess
import sys
import unittest


class ParallelWorkerCouncilDirectExecutionTests(unittest.TestCase):
    def test_direct_script_help_bootstraps_repository_package(self):
        result = subprocess.run(
            [sys.executable, "scripts/parallel_worker_council.py", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("--probe", result.stdout)
        self.assertIn("--benchmark", result.stdout)


if __name__ == "__main__":
    unittest.main()
