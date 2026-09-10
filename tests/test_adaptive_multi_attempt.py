from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.adaptive_multi_attempt import build_adaptive_executor_reviewer_callbacks
from scripts.mission_scheduler import ProviderInterrupted


class FakeBinding:
    def __init__(self, role, family):
        self.role = role
        self.model_family = family

    def validate(self):
        return None


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


if __name__ == "__main__":
    unittest.main()
