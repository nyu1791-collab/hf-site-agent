import contextlib
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import agent_delegation
from scripts import preflight_openrouter_models as preflight


class CommanderDelegationTests(unittest.TestCase):
    def test_parallel_calls_keep_partial_success(self):
        planner_payload = {"summary": "案", "work_orders": [], "requires_commander_approval": True}
        results = {
            "general-model": {
                "ok": True,
                "status": "completed",
                "response": planner_payload,
                "attempts": 1,
                "provider_status": 200,
                "valid": False,
            },
            "engineering-model": {
                "ok": False,
                "status": "failed",
                "error_code": "model_not_found",
                "error": "Requested model was not found.",
                "provider_status": 404,
                "attempts": 1,
                "valid": False,
            },
        }

        def fake_call(model, system, prompt):
            return results[model]

        with patch.object(agent_delegation, "call_agent", side_effect=fake_call):
            planner, critic = agent_delegation.run_parallel_commanders(
                "general-model",
                "system",
                "prompt",
                "engineering-model",
                "system",
                "prompt",
            )

        self.assertTrue(planner["ok"])
        self.assertTrue(planner["valid"])
        self.assertFalse(critic["ok"])
        self.assertEqual(critic["error_code"], "model_not_found")

    def test_invalid_model_output_is_not_promoted(self):
        result = {
            "ok": True,
            "status": "completed",
            "response": {"text": "not a commander packet"},
            "attempts": 1,
            "provider_status": 200,
            "valid": False,
        }
        with patch.object(agent_delegation, "call_agent", return_value=result):
            planner, critic = agent_delegation.run_parallel_commanders(
                "general-model",
                "system",
                "prompt",
                "engineering-model",
                "system",
                "prompt",
            )
        self.assertFalse(planner["ok"])
        self.assertEqual(planner["error_code"], "output_missing_commander_fields")
        self.assertFalse(critic["ok"])

    def test_transient_error_has_one_bounded_retry(self):
        payload = {"verdict": "ok", "tests": [], "requires_commander_approval": True}
        with patch.object(
            agent_delegation,
            "_call_once",
            side_effect=[
                agent_delegation.AgentCallError("timeout", "temporary", status=408, retryable=True),
                payload,
            ],
        ):
            result = agent_delegation.call_agent("engineering-model", "system", "prompt")
        self.assertTrue(result["ok"])
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["response"], payload)


class FreeModelPreflightTests(unittest.TestCase):
    def _active_registry(self):
        from scripts.model_registry import load_registry

        registry = copy.deepcopy(load_registry())
        registry["roles"]["ROLE_GENERAL_COMMANDER"]["active"] = True
        registry["roles"]["ROLE_ENGINEERING_COMMANDER"]["active"] = True
        return registry

    def _env(self, output_file):
        return {
            "GENERAL_COMMANDER_MODEL": "",
            "ENGINEERING_COMMANDER_MODEL": "",
            "GITHUB_OUTPUT": str(output_file),
            "COMMANDER_PACKET_PATH": "packet.json",
        }

    def test_resolves_same_role_zero_priced_models_and_writes_outputs(self):
        entries = [
            {"id": "z-ai/glm-5.3-flash:free", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "deepseek/deepseek-v4-flash:free", "pricing": {"prompt": "0", "completion": "0"}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            output_file = Path(directory) / "github_output"
            packet_file = Path(directory) / "packet.json"
            with contextlib.chdir(directory), patch.dict(os.environ, self._env(output_file), clear=True), patch.object(
                preflight, "_catalog", return_value=entries
            ), patch.object(preflight, "load_registry", return_value=self._active_registry()):
                self.assertEqual(preflight.main(), 0)
            output = output_file.read_text(encoding="utf-8")
            self.assertIn("ready=true\n", output)
            self.assertIn("general_model=z-ai/glm-5.3-flash:free\n", output)
            self.assertIn("engineering_model=deepseek/deepseek-v4-flash:free\n", output)
            packet = json.loads(packet_file.read_text(encoding="utf-8"))
            self.assertFalse(packet["execution_allowed"])
            self.assertEqual(packet["model_calls"], 0)

    def test_rejects_cross_role_requested_model_even_when_free(self):
        entries = [
            {"id": "thinkingmachines/inkling:free", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "poolside/laguna-s-2.1:free", "pricing": {"prompt": "0", "completion": "0"}},
        ]
        env = self._env(Path("github_output"))
        env["GENERAL_COMMANDER_MODEL"] = "deepseek/deepseek-v4-flash:free"
        with tempfile.TemporaryDirectory() as directory:
            output_file = Path(directory) / "github_output"
            env["GITHUB_OUTPUT"] = str(output_file)
            with contextlib.chdir(directory), patch.dict(os.environ, env, clear=True), patch.object(
                preflight, "_catalog", return_value=entries
            ), patch.object(preflight, "load_registry", return_value=self._active_registry()):
                self.assertEqual(preflight.main(), 0)
            packet = json.loads(Path("packet.json").read_text(encoding="utf-8"))
            self.assertEqual(packet["status"], "blocked")
            self.assertEqual(packet["details"]["ROLE_GENERAL_COMMANDER"]["reason"], "requested_model_not_allowed_for_role")
            self.assertEqual(packet["model_calls"], 0)

    def test_blocks_without_free_role_candidate_and_writes_valid_json(self):
        entries = [
            {"id": "z-ai/glm-5.3-flash", "pricing": {"prompt": "0.1", "completion": "0.2"}},
            {"id": "deepseek/deepseek-v4-flash-0731", "pricing": {"prompt": "0.1", "completion": "0.2"}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            output_file = Path(directory) / "github_output"
            with contextlib.chdir(directory), patch.dict(os.environ, self._env(output_file), clear=True), patch.object(
                preflight, "_catalog", return_value=entries
            ), patch.object(preflight, "load_registry", return_value=self._active_registry()):
                self.assertEqual(preflight.main(), 0)
            output = output_file.read_text(encoding="utf-8")
            self.assertIn("ready=false\n", output)
            packet = json.loads(Path("packet.json").read_text(encoding="utf-8"))
            self.assertEqual(packet["status"], "blocked")
            self.assertEqual(packet["model_calls"], 0)
            self.assertFalse(packet["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
