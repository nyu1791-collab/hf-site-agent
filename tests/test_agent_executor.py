import contextlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import agent_executor


def verified_probe(model: str) -> dict:
    return {
        "registry_changed": False,
        "provider_allow_fallbacks": False,
        "paid_fallback": False,
        "selections": {
            "CODING_WORKER": {
                "status": "ready",
                "provider": "openrouter",
                "model": model,
                "generic_router": False,
                "paid_fallback": False,
            }
        },
        "results": [{
            "requested_model": model,
            "status": "FREE_ACTIVE",
            "response_model": model,
            "usage_cost": "0",
            "fallback_used": False,
            "credits_unchanged": True,
            "provider_allow_fallbacks": False,
        }],
    }


class AgentExecutorWorkerGateTests(unittest.TestCase):
    def test_resolves_only_the_exact_probe_selected_worker(self):
        model = "vendor/coding-current:free"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "probe.json").write_text(json.dumps(verified_probe(model)), encoding="utf-8")
            with contextlib.chdir(root), patch.dict(os.environ, {"WORKER_PROBE_PATH": "probe.json"}, clear=False):
                self.assertEqual(agent_executor.load_verified_worker_model("code"), model)

    def test_manual_model_mismatch_is_rejected(self):
        model = "vendor/coding-current:free"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "probe.json").write_text(json.dumps(verified_probe(model)), encoding="utf-8")
            with contextlib.chdir(root), patch.dict(os.environ, {"WORKER_PROBE_PATH": "probe.json"}, clear=False):
                with self.assertRaises(SystemExit):
                    agent_executor.load_verified_worker_model("code", "vendor/other:free")


if __name__ == "__main__":
    unittest.main()
