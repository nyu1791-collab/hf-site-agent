import unittest

from scripts import run_nvidia_independent_org_audit as audit
from scripts import run_nvidia_worker_expansion_compact as compact


class NvidiaIndependentOrganizationAuditTests(unittest.TestCase):
    def test_configure_adds_current_independent_agent_surface(self):
        original_files = compact.FOCUSED_FILES
        original_markers = dict(compact.ADDITIONAL_MARKERS)
        original_objective = compact.COMPACT_OBJECTIVE
        try:
            audit.configure()
            for path in (
                "scripts/independent_agent_runtime.py",
                "scripts/independent_agent_scheduler.py",
                "scripts/low_latency_agent_fabric.py",
                "scripts/replaceable_agent_scheduler_v2.py",
                "scripts/global_agent_role_optimizer.py",
            ):
                self.assertIn(path, compact.FOCUSED_FILES)
                self.assertIn(path, compact.ADDITIONAL_MARKERS)
            self.assertIn("Do not re-centralize ordinary agent decisions", compact.COMPACT_OBJECTIVE)
        finally:
            compact.FOCUSED_FILES = original_files
            compact.ADDITIONAL_FILES = original_files
            compact.ADDITIONAL_MARKERS = original_markers
            compact.COMPACT_OBJECTIVE = original_objective


if __name__ == "__main__":
    unittest.main()
