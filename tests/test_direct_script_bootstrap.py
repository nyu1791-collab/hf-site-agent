import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DirectScriptBootstrapTests(unittest.TestCase):
    def _run_help(self, script: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script), "--help"],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )

    def test_google_readiness_direct_entrypoint_resolves_scripts_package(self):
        result = self._run_help("google_staging_readiness.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("ModuleNotFoundError", result.stderr)

    def test_openrouter_orchestrator_direct_entrypoint_resolves_scripts_package(self):
        result = self._run_help("openrouter_worker_orchestrator.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("ModuleNotFoundError", result.stderr)


if __name__ == "__main__":
    unittest.main()
