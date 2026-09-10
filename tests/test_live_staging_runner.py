import json
from pathlib import Path
import tempfile
import unittest

from scripts.execution_scope import ExecutionPolicy
from scripts.live_staging_runner import (
    LiveAgentBinding,
    build_minimal_staging_plan,
    build_nvidia_limited_bootstrap_plan,
    run_live_staging_mission,
    run_nvidia_limited_bootstrap_mission,
)
from scripts.provider_registry import load_provider_registry


def staging_policy(provider, model, family):
    return ExecutionPolicy(
        scope="STAGING",
        provider_id=provider,
        model_id=model,
        model_family=family,
        technically_ready=True,
        staging_approved=True,
        exact_model_verified=True,
        endpoint_verified=True,
        auth_verified=True,
        capability_verified=True,
        free_verified=True,
        cost_safe=True,
        quota_safe=True,
        circuit_closed=True,
    )


class FakeLiveAdapter:
    def __init__(self, provider_id, config, responses):
        self.provider_id = provider_id
        self.config = config
        self.responses = list(responses)
        self.calls = []

    def generate(self, model_id, messages, **options):
        self.calls.append({"model": model_id, "options": options, "messages": messages})
        response = self.responses.pop(0) if self.responses else {"summary": "default", "decision": "PASS"}
        return {
            "model": model_id,
            "text": json.dumps(response, separators=(",", ":")),
            "usage": {"prompt_tokens": 10, "completion_tokens": 12, "cost": "0"},
        }


class LiveStagingRunnerTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_provider_registry()

    def make_bindings(self, *, reviewer_responses=None, executor_responses=None, same_family=False):
        executor_provider = "groq"
        reviewer_provider = "nvidia"
        executor_model = "qwen/qwen3.8-27b"
        reviewer_model = "deepseek-ai/deepseek-v4-flash-0731"
        executor_family = "Qwen"
        reviewer_family = executor_family if same_family else "DeepSeek"
        executor = FakeLiveAdapter(executor_provider, self.registry["providers"][executor_provider], executor_responses or [
            {"summary": "proposal", "proposal": {"files_affected": ["fixture.py"], "tests": ["fixture test"]}, "risks": []}
        ])
        reviewer = FakeLiveAdapter(reviewer_provider, self.registry["providers"][reviewer_provider], reviewer_responses or [
            {"decision": "PASS", "summary": "independent review passed", "findings": [], "required_changes": []}
        ])
        return (
            LiveAgentBinding("EXECUTOR", executor_provider, executor_model, executor_family, executor, staging_policy(executor_provider, executor_model, executor_family)),
            LiveAgentBinding("REVIEWER", reviewer_provider, reviewer_model, reviewer_family, reviewer, staging_policy(reviewer_provider, reviewer_model, reviewer_family)),
        )

    def run_mission(self, executor, reviewer, *, mission_id="LIVE-STAGING-TEST"):
        with tempfile.TemporaryDirectory() as directory:
            plan = build_minimal_staging_plan(mission_id=mission_id, executor=executor)
            return run_live_staging_mission(
                plan,
                executor,
                reviewer,
                ledger_path=Path(directory) / "ledger.json",
                checkpoint_root=Path(directory) / "checkpoints",
                network_enabled=True,
            )

    def test_executor_reviewer_loop_completes_and_auto_continues(self):
        executor, reviewer = self.make_bindings()
        report = self.run_mission(executor, reviewer)
        self.assertEqual(report["status"], "completed")
        self.assertTrue(report["live_staging"]["operational"])
        self.assertEqual(report["live_staging"]["external_model_calls"], 2)
        self.assertEqual(report["live_staging"]["live_model_family_count"], 2)
        self.assertEqual(report["live_staging"]["provider_tokens"], {"groq": 22, "nvidia": 22})
        self.assertTrue(report["live_staging"]["family_separation_pass"])
        self.assertTrue(report["safety"]["zero_cost_all_live_calls"])
        self.assertEqual(len(executor.adapter.calls), 1)
        self.assertEqual(len(reviewer.adapter.calls), 1)
        self.assertTrue(all(call["options"]["require_zero_cost"] for call in executor.adapter.calls + reviewer.adapter.calls))
        self.assertTrue(all(call["options"]["execution_policy"].scope == "STAGING" for call in executor.adapter.calls + reviewer.adapter.calls))

    def test_review_failure_returns_to_executor_and_re_review_passes(self):
        executor, reviewer = self.make_bindings(
            executor_responses=[
                {"summary": "first proposal", "proposal": {"files_affected": ["fixture.py"]}},
                {"summary": "revised proposal", "proposal": {"files_affected": ["fixture.py"], "tests": ["fixed"]}},
            ],
            reviewer_responses=[
                {"decision": "FAIL", "summary": "missing test", "failure_signature": "MISSING_TEST", "required_changes": ["add test"]},
                {"decision": "PASS", "summary": "re-review passed", "findings": []},
            ],
        )
        report = self.run_mission(executor, reviewer, mission_id="LIVE-STAGING-REVISION")
        self.assertEqual(report["status"], "completed")
        self.assertEqual(len(executor.adapter.calls), 2)
        self.assertEqual(len(reviewer.adapter.calls), 2)
        task_report = report["tasks"]["LIVE-TASK-1"]["result"]["autonomous"]
        self.assertEqual(task_report["revision_count"], 1)

    def test_same_model_family_is_blocked_before_network(self):
        executor, reviewer = self.make_bindings(same_family=True)
        report = self.run_mission(executor, reviewer, mission_id="LIVE-STAGING-FAMILY-BLOCK")
        self.assertEqual(report["status"], "blocked")
        self.assertIn("REVIEWER_MODEL_FAMILY_MUST_DIFFER", report["stop_reason"])
        self.assertEqual(executor.adapter.calls, [])
        self.assertEqual(reviewer.adapter.calls, [])

    def test_network_is_not_implicitly_enabled(self):
        executor, reviewer = self.make_bindings()
        with tempfile.TemporaryDirectory() as directory:
            plan = build_minimal_staging_plan(mission_id="LIVE-STAGING-NETWORK-BLOCK", executor=executor)
            report = run_live_staging_mission(
                plan,
                executor,
                reviewer,
                ledger_path=Path(directory) / "ledger.json",
                checkpoint_root=Path(directory) / "checkpoints",
                network_enabled=False,
            )
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["stop_reason"], "NETWORK_NOT_EXPLICITLY_ENABLED")
        self.assertEqual(executor.adapter.calls, [])

    def test_nvidia_limited_bootstrap_is_one_external_call_and_local_review(self):
        model = "deepseek-ai/deepseek-v4-flash-0731"
        adapter = FakeLiveAdapter("nvidia", self.registry["providers"]["nvidia"], [
            {"summary": "google adapter proposal", "proposal": {"files_affected": ["scripts/google.py"]}, "risks": []}
        ])
        policy = ExecutionPolicy(
            scope="STAGING",
            provider_id="nvidia",
            model_id=model,
            model_family="DeepSeek",
            staging_approved=True,
            exact_model_verified=True,
            endpoint_verified=True,
            auth_verified=True,
            circuit_closed=True,
            staging_free_route_allowed=True,
            account_zero_cost_verified=False,
            limited_staging=True,
            limited_operation="BOOTSTRAP_PROPOSAL",
        )
        binding = LiveAgentBinding("EXECUTOR", "nvidia", model, "DeepSeek", adapter, policy)
        with tempfile.TemporaryDirectory() as directory:
            plan = build_nvidia_limited_bootstrap_plan(
                mission_id="NVIDIA-LIMITED-BOOTSTRAP-TEST",
                request_budget=1,
                token_budget=2_048,
            )
            report = run_nvidia_limited_bootstrap_mission(
                plan,
                binding,
                ledger_path=Path(directory) / "ledger.json",
                checkpoint_root=Path(directory) / "checkpoints",
                network_enabled=True,
            )
            ledger = json.loads((Path(directory) / "ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "completed")
        self.assertTrue(report["live_staging"]["operational"])
        self.assertEqual(report["live_staging"]["external_model_calls"], 1)
        self.assertEqual(report["live_staging"]["live_agent_count"], 1)
        self.assertEqual(report["live_staging"]["live_model_family_count"], 1)
        self.assertFalse(report["live_staging"]["two_agent"])
        self.assertTrue(report["nvidia_bootstrap"]["proposal_generated"])
        self.assertTrue(report["nvidia_bootstrap"]["local_integrator_review"])
        self.assertFalse(report["safety"]["account_specific_zero_cost_proven"])
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(adapter.calls[0]["options"]["max_tokens"], 256)
        self.assertEqual(adapter.calls[0]["options"]["reasoning_effort"], "none")
        self.assertTrue(adapter.calls[0]["options"]["execution_policy"].limited_staging)
        self.assertTrue(all(item["state"] == "settled" for item in ledger["reservations"].values()))


if __name__ == "__main__":
    unittest.main()
