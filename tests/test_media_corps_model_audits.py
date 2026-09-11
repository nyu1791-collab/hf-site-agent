import unittest

from scripts import deepseek_media_corps_audit as deepseek_media
from scripts import deepseek_organization_audit as deepseek_base
from scripts import run_nvidia_media_corps_audit as nvidia_media
from scripts import run_nvidia_worker_expansion_compact as compact


class MediaCorpsModelAuditTests(unittest.TestCase):
    def test_deepseek_media_audit_adds_media_surface(self):
        original_context = dict(deepseek_base.AUDIT_CONTEXT)
        original_prompt = deepseek_base._system_prompt
        original_wrapped = getattr(deepseek_base, "_media_corps_prompt_wrapped", False)
        try:
            if hasattr(deepseek_base, "_media_corps_prompt_wrapped"):
                delattr(deepseek_base, "_media_corps_prompt_wrapped")
            deepseek_media.configure()
            self.assertIn("config/media_agent_corps.json", deepseek_base.AUDIT_CONTEXT)
            self.assertIn("scripts/media_agent_router.py", deepseek_base.AUDIT_CONTEXT)
            prompt = deepseek_base._system_prompt("ctx", ["scripts/media_agent_router.py"])
            self.assertIn("monetization media corps", prompt)
            self.assertIn("Preserve human approval for publication", prompt)
        finally:
            deepseek_base.AUDIT_CONTEXT = original_context
            deepseek_base._system_prompt = original_prompt
            if original_wrapped:
                deepseek_base._media_corps_prompt_wrapped = True
            elif hasattr(deepseek_base, "_media_corps_prompt_wrapped"):
                delattr(deepseek_base, "_media_corps_prompt_wrapped")

    def test_nvidia_media_audit_adds_media_surface(self):
        original_files = compact.FOCUSED_FILES
        original_markers = dict(compact.ADDITIONAL_MARKERS)
        original_objective = compact.COMPACT_OBJECTIVE
        try:
            nvidia_media.configure()
            for path in (
                "config/media_agent_corps.json",
                "scripts/media_agent_router.py",
                "tests/test_media_agent_router.py",
            ):
                self.assertIn(path, compact.FOCUSED_FILES)
                self.assertIn(path, compact.ADDITIONAL_MARKERS)
            self.assertIn("monetization media sub-corps", compact.COMPACT_OBJECTIVE)
            self.assertIn("Keep publish human-approved", compact.COMPACT_OBJECTIVE)
        finally:
            compact.FOCUSED_FILES = original_files
            compact.ADDITIONAL_FILES = original_files
            compact.ADDITIONAL_MARKERS = original_markers
            compact.COMPACT_OBJECTIVE = original_objective


if __name__ == "__main__":
    unittest.main()
