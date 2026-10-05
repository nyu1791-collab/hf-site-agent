from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.framework_plugin_registry import (
    build_adapter_evidence,
    load_config,
    probe_plugin,
)


class DummyModule:
    StateGraph = object()
    START = object()
    END = object()


class FrameworkPluginRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    @patch("scripts.framework_plugin_registry.importlib.import_module")
    @patch("scripts.framework_plugin_registry.importlib.util.find_spec")
    @patch("scripts.framework_plugin_registry.importlib.metadata.version")
    def test_langgraph_public_contract_can_pass(self, version, find_spec, import_module):
        version.return_value = "1.0.0"
        find_spec.return_value = object()
        import_module.return_value = DummyModule()
        report = probe_plugin("LANGGRAPH", config=self.config)
        self.assertTrue(report["installed"])
        self.assertTrue(report["api_contract_verified"])
        self.assertTrue(report["ready_for_health_shadow"])
        self.assertFalse(report["network_called"])
        self.assertFalse(report["package_installed_by_probe"])

    @patch("scripts.framework_plugin_registry.importlib.util.find_spec")
    def test_missing_package_fails_closed(self, find_spec):
        find_spec.return_value = None
        report = probe_plugin("CREWAI", config=self.config)
        self.assertFalse(report["installed"])
        self.assertFalse(report["api_contract_verified"])
        self.assertIn("framework_installed", report["blockers"])

    @patch("scripts.framework_plugin_registry.importlib.import_module")
    @patch("scripts.framework_plugin_registry.importlib.util.find_spec")
    def test_missing_required_symbol_blocks_contract(self, find_spec, import_module):
        find_spec.return_value = object()
        import_module.return_value = object()
        report = probe_plugin("AUTOGEN", config=self.config)
        self.assertFalse(report["api_contract_verified"])
        self.assertIn("SelectorGroupChat", report["required_symbols_missing"])

    def test_copilot_needs_connector_and_billing_safety(self):
        blocked = probe_plugin("GITHUB_COPILOT", config=self.config)
        self.assertFalse(blocked["ready_for_health_shadow"])
        self.assertIn("connector_present", blocked["blockers"])
        self.assertIn("billing_safe_verified", blocked["blockers"])

        ready = probe_plugin(
            "GITHUB_COPILOT",
            config=self.config,
            connector_present=True,
            billing_safe_verified=True,
        )
        self.assertTrue(ready["ready_for_health_shadow"])

    def test_adapter_evidence_never_enables_paid_fallback(self):
        report = {
            "installed": True,
            "api_contract_verified": True,
        }
        evidence = build_adapter_evidence(
            report,
            runtime_present=True,
            model_route_free_verified=True,
            framework_health_ready=True,
            benchmark_quality=0.91,
        )
        self.assertTrue(evidence["framework_installed"])
        self.assertTrue(evidence["api_contract_verified"])
        self.assertFalse(evidence["paid"])
        self.assertFalse(evidence["paid_fallback_enabled"])


if __name__ == "__main__":
    unittest.main()
