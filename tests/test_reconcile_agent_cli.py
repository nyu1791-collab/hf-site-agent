import subprocess
import sys
import unittest


class ReconcileAgentCliTests(unittest.TestCase):
    def test_direct_entrypoint_resolves_scripts_package(self):
        completed = subprocess.run(
            [sys.executable, "scripts/reconcile_agent_organization.py", "--help"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--direct-free-report", completed.stdout)


if __name__ == "__main__":
    unittest.main()
