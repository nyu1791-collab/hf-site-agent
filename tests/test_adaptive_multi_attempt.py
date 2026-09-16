from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.adaptive_multi_attempt import build_adaptive_executor_reviewer_callbacks
from scripts.execution_scope import ExecutionPolicy
from scripts.live_staging_runner import LiveAgentBinding
from scripts.mission_scheduler import ProviderInterrupted


class FakeBinding:
    def __init__(self, role, family):
        self.role = role
        self.model_family = family

    def validate(self):
        return None


class FakeRealAdapter:
    provider_id = "google"
    config = {"provider_id": "google", "enabled": False, "activation_approved": False}


class AdaptiveMultiAttemptTests(unittest.TestCase):
    def task(self):
        return SimpleNamespace(
            role="repository implementation",
            risk_level="MEDIUM",
            complexity_level=2,
            metadata={"objective": "implement worker routing"},
        )

    def test_important_best_of_two_is_serial_and_excludes_selected_from_alternatives(self):
        executor = FakeBinding("EXECUTOR", "GEMINI")
        reviewer = FakeBinding("REVIEWER", "NEMOTRON")
        outputs = [
            {
                "summary": "weaker",
                "proposal": "small",
                "tests": [],
                "risks": [],
                "files_affected": ["a.py"],
                "requests_used": 1,
                "input_tokens": 10,
                "output_tokens": 20,
            },
            {
                "summary": "stronger",
                "proposal": "complete",
                "tests": ["test_a"],
                "risks": ["quota"],
                "files_affected": ["a.py"],
                "requests_used": 1,
                "input_tokens": 11,
                "output_tokens": 21,
            },
        ]
        with patch("scripts.adaptive_multi_attempt.live_runner._call_model", side_effect=outputs) as call:
            callbacks = build_adaptive_executor_reviewer_callbacks(executor, reviewer)
            result = callbacks.executor(self.task(), {"phase": "EXECUTE"})
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["summary"], "stronger")
        self.assertEqual(result["attempt_execution_mode"], "SERIAL_SAME_PROVIDER")
        self.assertEqual(result["requests_used"], 2)
        self.assertEqual(result["alternative_attempt_summaries"], ["weaker"])
        self.assertFalse(result["optional_attempt_interrupted_after_valid_result"])
        self.assertEqual(result["independent_attempts_profile_requested"], 2)

    def test_provider_interruption_stops_before_another_independent_attempt(self):
        executor = FakeBinding("EXECUTOR", "GEMINI")
        reviewer = FakeBinding("REVIEWER", "NEMOTRON")
        with patch(
            "scripts.adaptive_multi_attempt.live_runner._call_model",
            side_effect=ProviderInterrupted("google:PROVIDER_CALL_FAILED"),
        ) as call:
            callbacks = build_adaptive_executor_reviewer_callbacks(executor, reviewer)
            with self.assertRaises(ProviderInterrupted):
                callbacks.executor(self.task(), {"phase": "EXECUTE"})
        self.assertEqual(call.call_count, 1)

    def test_later_interruption_keeps_completed_attempt_and_suppresses_more_calls(self):
        executor = FakeBinding("EXECUTOR", "GEMINI")
        reviewer = FakeBinding("REVIEWER", "NEMOTRON")
        first = {
            "summary": "usable first result",
            "proposal": "complete enough",
            "tests": ["test_a"],
            "risks": [],
            "files_affected": ["a.py"],
            "requests_used": 1,
            "input_tokens": 10,
            "output_tokens": 20,
        }
        with patch(
            "scripts.adaptive_multi_attempt.live_runner._call_model",
            side_effect=[first, ProviderInterrupted("google:NETWORK_TIMEOUT")],
        ) as call:
            callbacks = build_adaptive_executor_reviewer_callbacks(executor, reviewer)
            result = callbacks.executor(self.task(), {"phase": "EXECUTE"})
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result["summary"], "usable first result")
        self.assertTrue(result["optional_attempt_interrupted_after_valid_result"])
        self.assertTrue(result["further_attempts_suppressed"])
        self.assertIn("NETWORK_TIMEOUT", result["optional_attempt_interruption"])
        self.assertEqual(result["requests_used"], 1)

    def test_transport_recovery_success_suppresses_optional_best_of_n(self):
        executor = FakeBinding("EXECUTOR", "GEMINI")
        reviewer = FakeBinding("REVIEWER", "NEMOTRON")
        recovered = {
            "summary": "usable recovered result",
            "proposal": "complete enough",
            "tests": ["test_a"],
            "risks": [],
            "files_affected": ["a.py"],
            "requests_used": 3,
            "input_tokens": 10,
            "output_tokens": 20,
            "provider_attempts": 3,
            "conclusive_5xx_recovery_count": 1,
            "post_5xx_rate_limit_recovery_count": 1,
        }
        with patch("scripts.adaptive_multi_attempt.live_runner._call_model", return_value=recovered) as call:
            callbacks = build_adaptive_executor_reviewer_callbacks(executor, reviewer)
            result = callbacks.executor(self.task(), {"phase": "EXECUTE"})
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result["summary"], "usable recovered result")
        self.assertTrue(result["optional_attempts_suppressed_after_transport_recovery"])
        self.assertTrue(result["further_attempts_suppressed"])

    def test_real_google_unknown_account_free_route_uses_one_primary_attempt(self):
        policy = ExecutionPolicy(
            scope="STAGING",
            provider_id="google",
            model_id="gemini-3.8-flash",
            model_family="GEMINI",
            technically_ready=True,
            staging_approved=True,
            exact_model_verified=True,
            endpoint_verified=True,
            auth_verified=True,
            capability_verified=True,
            circuit_closed=True,
            staging_free_route_allowed=True,
            account_zero_cost_verified=False,
            paid_fallback=False,
        )
        executor = LiveAgentBinding(
            "EXECUTOR", "google", "gemini-3.8-flash", "GEMINI", FakeRealAdapter(), policy
        )
        reviewer = FakeBinding("REVIEWER", "NEMOTRON")
        output = {
            "summary": "single primary",
            "proposal": "complete",
            "tests": ["test_a"],
            "risks": [],
            "files_affected": ["a.py"],
            "requests_used": 1,
            "input_tokens": 10,
            "output_tokens": 20,
        }
        with patch("scripts.adaptive_multi_attempt.call_model_with_bounded_recovery", return_value=output) as call:
            callbacks = build_adaptive_executor_reviewer_callbacks(executor, reviewer)
            result = callbacks.executor(self.task(), {"phase": "EXECUTE"})
        self.assertEqual(call.call_count, 1)
        self.assertTrue(result["google_free_route_single_primary"])
        self.assertEqual(result["independent_attempts_requested"], 1)
        self.assertEqual(result["independent_attempts_profile_requested"], 2)


if __name__ == "__main__":
    unittest.main()
