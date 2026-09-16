import unittest

from scripts import run_nvidia_media_org_audit as media_audit
from scripts import run_nvidia_worker_expansion_compact as compact


class NvidiaMediaOrganizationAuditTests(unittest.TestCase):
    def test_configure_replaces_wide_scope_with_media_only_scope(self):
        original_files = compact.FOCUSED_FILES
        original_additional = compact.ADDITIONAL_FILES
        original_markers = dict(compact.ADDITIONAL_MARKERS)
        original_objective = compact.COMPACT_OBJECTIVE
        try:
            media_audit.configure()
            self.assertEqual(tuple(compact.FOCUSED_FILES), media_audit.MEDIA_FILES)
            self.assertEqual(tuple(compact.ADDITIONAL_FILES), media_audit.MEDIA_FILES)
            self.assertEqual(set(compact.ADDITIONAL_MARKERS), set(media_audit.MEDIA_MARKERS))
            self.assertIn("ONLY the AI Army Media & Monetization Corps", compact.COMPACT_OBJECTIVE)
            self.assertNotIn("scripts/independent_agent_runtime.py", compact.FOCUSED_FILES)
            self.assertNotIn("scripts/independent_agent_scheduler.py", compact.FOCUSED_FILES)
            self.assertNotIn("scripts/provider_adapters.py", compact.FOCUSED_FILES)
        finally:
            compact.FOCUSED_FILES = original_files
            compact.ADDITIONAL_FILES = original_additional
            compact.ADDITIONAL_MARKERS = original_markers
            compact.COMPACT_OBJECTIVE = original_objective


if __name__ == "__main__":
    unittest.main()
