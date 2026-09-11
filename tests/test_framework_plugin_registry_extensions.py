from __future__ import annotations

import unittest

from scripts.framework_adapter_layer import load_config as load_adapter_config
from scripts.framework_plugin_registry import load_config as load_plugin_config, probe_plugin


class FrameworkPluginRegistryExtensionTests(unittest.TestCase):
    def test_microsoft_agent_framework_is_registered_in_both_layers(self):
        adapter = load_adapter_config()["adapters"]["MICROSOFT_AGENT_FRAMEWORK"]
        plugin = load_plugin_config()["plugins"]["MICROSOFT_AGENT_FRAMEWORK"]
        self.assertEqual(adapter["package"], "agent-framework")
        self.assertEqual(plugin["distribution"], "agent-framework")
        self.assertEqual(plugin["module"], "agent_framework")
        self.assertIn("Agent", plugin["required_symbols"])
        self.assertTrue(adapter["model_route_free_verification_required"])
        self.assertFalse(load_adapter_config()["policy"]["generic_paid_fallback"])

    def test_probe_never_installs_or_calls_network(self):
        report = probe_plugin("MICROSOFT_AGENT_FRAMEWORK")
        self.assertFalse(report["network_called"])
        self.assertFalse(report["package_installed_by_probe"])
        if not report["installed"]:
            self.assertIn("framework_installed", report["blockers"])


if __name__ == "__main__":
    unittest.main()
