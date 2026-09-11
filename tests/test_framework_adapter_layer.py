from __future__ import annotations

import unittest

from scripts.framework_adapter_layer import FrameworkAdapterLayer, load_config, redact_external_context
from scripts.replaceable_agent_scheduler import AgentTask


class FrameworkAdapterLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.layer = FrameworkAdapterLayer(self.config)

    @staticmethod
    def task(**metadata):
        return AgentTask(
            task_id="framework-task",
            slot="FAST_OPERATOR",
            objective="Produce a bounded structured result.",
            risk_level="LOW",
            metadata=metadata,
        )

    @staticmethod
    def native_handler(task, binding, context):
        return {
            "status": "COMPLETED",
            "summary": "native completed",
            "output": {"source": "native"},
            "quality_score": 0.9,
        }

    def test_native_remains_default_control_plane_execution(self):
        result = self.layer.execute(
            task=self.task(),
            binding={"provider": "free", "model": "native"},
            context={},
            native_handler=self.native_handler,
            evidence={},
        )
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["output"]["framework_adapter"], "NATIVE_V4")
        self.assertFalse(result["output"]["framework_provenance"]["authority_expanded"])

    def test_verified_langgraph_can_be_explicitly_selected(self):
        captured = {}

        def executor(envelope):
            captured.update(envelope)
            return {
                "status": "COMPLETED",
                "summary": "langgraph completed",
                "output": {"graph": "ok"},
                "quality_score": 0.95,
            }

        self.layer.register_executor("LANGGRAPH", executor)
        result = self.layer.execute(
            task=self.task(
                framework_preference=["LANGGRAPH"],
                framework_capabilities=["graph_workflow", "checkpoint"],
            ),
            binding={"provider": "free", "model": "worker"},
            context={"secret_token": "do-not-send", "safe": {"value": 3}},
            native_handler=self.native_handler,
            evidence={
                "LANGGRAPH": {
                    "framework_installed": True,
                    "runtime_present": True,
                    "model_route_free_verified": True,
                    "paid": False,
                    "paid_fallback_enabled": False,
                }
            },
        )
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["output"]["framework_adapter"], "LANGGRAPH")
        self.assertEqual(captured["context"]["secret_token"], "[REDACTED]")
        self.assertFalse(captured["authority"]["repository_write"])
        self.assertFalse(captured["authority"]["payment"])

    def test_unverified_external_framework_falls_back_to_native(self):
        self.layer.register_executor("AUTOGEN", lambda envelope: {"status": "COMPLETED", "summary": "unexpected"})
        result = self.layer.execute(
            task=self.task(framework_preference=["AUTOGEN"]),
            binding={"provider": "free", "model": "native"},
            context={},
            native_handler=self.native_handler,
            evidence={"AUTOGEN": {"framework_installed": True, "runtime_present": True, "model_route_free_verified": False}},
        )
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["output"]["framework_adapter"], "NATIVE_V4")

    def test_required_unverified_framework_blocks_instead_of_weakening(self):
        self.layer.register_executor("CREWAI", lambda envelope: {"status": "COMPLETED", "summary": "unexpected"})
        result = self.layer.execute(
            task=self.task(
                framework_preference=["CREWAI"],
                framework_required=True,
            ),
            binding={"provider": "free", "model": "native"},
            context={},
            native_handler=self.native_handler,
            evidence={"CREWAI": {"framework_installed": True, "runtime_present": True, "model_route_free_verified": False}},
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["error_class"], "NO_VERIFIED_FRAMEWORK_ADAPTER")

    def test_copilot_requires_billing_safety_evidence(self):
        self.layer.register_executor("GITHUB_COPILOT", lambda envelope: {"status": "COMPLETED", "summary": "unexpected"})
        selection = self.layer.select_adapter(
            self.task(framework_preference=["GITHUB_COPILOT"], framework_required=True),
            {
                "GITHUB_COPILOT": {
                    "connector_present": True,
                    "runtime_present": True,
                    "model_route_free_verified": True,
                    "billing_safe_verified": False,
                }
            },
        )
        self.assertFalse(selection.ready)
        copilot = next(row for row in selection.attempts if row["adapter_id"] == "GITHUB_COPILOT")
        self.assertIn("billing_safe_verified", copilot["failures"])

    def test_external_framework_cannot_execute_hard_boundary(self):
        self.layer.register_executor("LANGGRAPH", lambda envelope: {"status": "COMPLETED", "summary": "unexpected"})
        task = AgentTask(
            task_id="boundary-task",
            slot="FAST_OPERATOR",
            objective="Prepare a deploy plan.",
            risk_level="CRITICAL",
            boundary_action="deploy",
            metadata={"framework_preference": ["LANGGRAPH"]},
        )
        result = self.layer.execute(
            task=task,
            binding={"provider": "free", "model": "worker"},
            context={},
            native_handler=self.native_handler,
            evidence={
                "LANGGRAPH": {
                    "framework_installed": True,
                    "runtime_present": True,
                    "model_route_free_verified": True,
                }
            },
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["error_class"], "FRAMEWORK_HARD_BOUNDARY_DENIED")

    def test_redaction_is_recursive(self):
        value = redact_external_context({
            "safe": [{"password": "x", "nested": {"api_key": "y", "ok": 1}}],
            "authorization_header": "Bearer nope",
        })
        self.assertEqual(value["safe"][0]["password"], "[REDACTED]")
        self.assertEqual(value["safe"][0]["nested"]["api_key"], "[REDACTED]")
        self.assertEqual(value["authorization_header"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()
