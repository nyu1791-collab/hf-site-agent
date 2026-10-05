import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import deepseek_organization_audit as audit


class DeepSeekOrganizationAuditTests(unittest.TestCase):
    def test_repository_context_is_bounded_and_contains_current_agent_surface(self):
        text, files = audit.build_repository_context()
        self.assertLessEqual(len(text), audit.MAX_CONTEXT_CHARS)
        self.assertIn("scripts/independent_agent_runtime.py", files)
        self.assertIn("scripts/independent_agent_scheduler.py", files)
        self.assertIn("scripts/low_latency_agent_fabric.py", files)
        self.assertIn("config/replaceable_agent_organization.json", files)

    def test_dry_run_never_calls_network(self):
        with patch.object(audit.ds_base, "_request_json", side_effect=AssertionError("network called")):
            report = audit.run_audit(network=False, confirm="", api_key="")
        self.assertEqual(report["status"], "AUDIT_DRY_RUN")
        self.assertEqual(report["model_calls"], 0)
        self.assertFalse(report["generic_paid_fallback"])
        self.assertFalse(report["repository_write"])

    def test_live_requires_exact_confirmation_before_network(self):
        with patch.object(audit.ds_base, "_request_json", side_effect=AssertionError("network called")):
            report = audit.run_audit(network=True, confirm="wrong", api_key="secret")
        self.assertEqual(report["status"], "AUDIT_BLOCKED")
        self.assertEqual(report["stop_reason"], "EXPLICIT_CONFIRMATION_REQUIRED")
        self.assertEqual(report["model_calls"], 0)

    def test_validation_rejects_out_of_scope_patch_path(self):
        candidate = {
            "status": "ok",
            "executive_summary": "summary",
            "findings": [{"severity": "HIGH", "area": "routing", "evidence": "x", "impact": "y", "recommendation": "z"}],
            "patch_candidates": [{"path": "scripts/not_provided.py", "symbol": "x", "change": "y", "rationale": "z"}],
            "tests": ["test"],
            "priority_plan": ["fix"],
            "confidence": 0.9,
        }
        report = audit.validate_audit(candidate, ["scripts/independent_agent_runtime.py"])
        self.assertFalse(report["valid"])
        self.assertFalse(report["path_validation"])
        self.assertEqual(report["invalid_paths"], ["scripts/not_provided.py"])

    def test_valid_structured_audit_passes(self):
        candidate = {
            "status": "ok",
            "executive_summary": "summary",
            "findings": [{"severity": "MEDIUM", "area": "handoff", "evidence": "x", "impact": "y", "recommendation": "z"}],
            "patch_candidates": [{"path": "scripts/independent_agent_runtime.py", "symbol": "execution_context", "change": "small", "rationale": "evidence"}],
            "tests": ["unit"],
            "priority_plan": ["fix handoff"],
            "confidence": 0.88,
        }
        report = audit.validate_audit(candidate, ["scripts/independent_agent_runtime.py"])
        self.assertTrue(report["valid"])


if __name__ == "__main__":
    unittest.main()
