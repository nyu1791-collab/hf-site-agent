import contextlib
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
            "qwen-model": {
                "ok": True,
                "status": "completed",
                "response": planner_payload,
                "attempts": 1,
                "provider_status": 200,
                "valid": False,
            },
            "deepseek-model": {
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
                "qwen-model",
                "system",
                "prompt",
                "deepseek-model",
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
                "qwen-model",
                "system",
                "prompt",
                "deepseek-model",
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
            result = agent_delegation.call_agent("deepseek-model", "system", "prompt")
        self.assertTrue(result["ok"])
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["response"], payload)


class FreeModelPreflightTests(unittest.TestCase):
    def test_resolves_same_family_zero_priced_models_and_writes_outputs(self):
        entries = [
            {"id": "qwen/fresh:free", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "deepseek/fresh:free", "pricing": {"prompt": "0", "completion": "0"}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            output_file = Path(directory) / "github_output"
            packet_file = Path(directory) / "packet.json"
            env = {
                "PLANNER_MODEL": "qwen/expired:free",
                "CRITIC_MODEL": "deepseek/expired:free",
                "GITHUB_OUTPUT": str(output_file),
                "COMMANDER_PACKET_PATH": "packet.json",
            }
            with contextlib.chdir(directory), patch.dict(os.environ, env, clear=False), patch.object(preflight, "_catalog", return_value=entries):
                self.assertEqual(preflight.main(), 0)
            output = output_file.read_text(encoding="utf-8")
            self.assertIn("ready=true\n", output)
            self.assertIn("planner_model=qwen/fresh:free\n", output)
            self.assertIn("critic_model=deepseek/fresh:free\n", output)
            self.assertFalse(packet_file.exists())

    def test_rejects_cross_family_requested_model_even_when_free(self):
        entries = [
            {"id": "qwen/fresh:free", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "deepseek/fresh:free", "pricing": {"prompt": "0", "completion": "0"}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            output_file = Path(directory) / "github_output"
            packet_file = Path(directory) / "packet.json"
            env = {
                "PLANNER_MODEL": "deepseek/fresh:free",
                "CRITIC_MODEL": "deepseek/fresh:free",
                "GITHUB_OUTPUT": str(output_file),
                "COMMANDER_PACKET_PATH": "packet.json",
            }
            with contextlib.chdir(directory), patch.dict(os.environ, env, clear=False), patch.object(preflight, "_catalog", return_value=entries):
                self.assertEqual(preflight.main(), 0)
            packet = __import__("json").loads(packet_file.read_text(encoding="utf-8"))
            self.assertEqual(packet["status"], "blocked")
            self.assertEqual(packet["details"]["planner"], "requested_model_wrong_family_or_generic")
            self.assertEqual(packet["model_calls"], 0)

    def test_blocks_without_free_family_candidate_and_writes_valid_json(self):
        entries = [
            {"id": "qwen/paid", "pricing": {"prompt": "0.1", "completion": "0.2"}},
            {"id": "deepseek/paid", "pricing": {"prompt": "0.1", "completion": "0.2"}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            output_file = Path(directory) / "github_output"
            packet_file = Path(directory) / "packet.json"
            env = {
                "PLANNER_MODEL": "qwen/expired:free",
                "CRITIC_MODEL": "deepseek/expired:free",
                "GITHUB_OUTPUT": str(output_file),
                "COMMANDER_PACKET_PATH": "packet.json",
            }
            with contextlib.chdir(directory), patch.dict(os.environ, env, clear=False), patch.object(preflight, "_catalog", return_value=entries):
                self.assertEqual(preflight.main(), 0)
            output = output_file.read_text(encoding="utf-8")
            self.assertIn("ready=false\n", output)
            packet = __import__("json").loads(packet_file.read_text(encoding="utf-8"))
            self.assertEqual(packet["status"], "blocked")
            self.assertEqual(packet["model_calls"], 0)
            self.assertFalse(packet["execution_allowed"])


if __name__ == "__main__":
    unittest.main()
