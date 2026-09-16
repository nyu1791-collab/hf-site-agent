import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.execution_scope import ExecutionPolicy
from scripts.live_staging_runner import LiveAgentBinding, LiveCallMetrics
from scripts.mission_scheduler import ProviderInterrupted
from scripts.provider_adapters import ProviderAdapterError
from scripts.resilient_live_call import (
    MAX_PRIMARY_RATE_LIMIT_RECOVERIES,
    MAX_TRAILING_RATE_LIMIT_RECOVERIES,
    PRIMARY_RATE_LIMIT_BACKOFF_SECONDS,
    TRAILING_RATE_LIMIT_BACKOFF_SECONDS,
    call_model_with_bounded_recovery,
)

MODEL = "gemini-3.8-flash"


def _policy():
    return ExecutionPolicy(
        scope="STAGING",
        provider_id="google",
        model_id=MODEL,
        model_family="GEMINI",
        technically_ready=True,
        staging_approved=True,
        exact_model_verified=True,
        endpoint_verified=True,
        auth_verified=True,
        capability_verified=True,
        circuit_closed=True,
        staging_free_route_allowed=True,
        paid_fallback=False,
        account_zero_cost_verified=False,
    )


def _task():
    return SimpleNamespace(
        mission_id="BACKPRESSURE-MISSION",
        task_id="BACKPRESSURE-TASK",
        owner_corps="GOOGLE",
        role="ORCHESTRATION_BUGFIX_PROJECT",
        required_capabilities=("structured_output",),
        metadata={},
    )


def _ok():
    return {
        "model": MODEL,
        "text": json.dumps({
            "summary": "recovered",
            "proposal": "minimal patch",
            "files_affected": ["fixture.py"],
            "tests": ["unit"],
            "risks": [],
            "next_action": "VALIDATE",
        }),
        "usage": {"promptTokenCount": 5, "candidatesTokenCount": 7},
    }


class SequenceAdapter:
    provider_id = "google"
    config = {"provider_id": "google", "enabled": False, "activation_approved": False}

    def __init__(self, events):
        self.events = list(events)
        self.calls = []

    def generate(self, model_id, messages, **options):
        self.calls.append({"model": model_id, "options": dict(options)})
        event = self.events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event


class FocusedSequenceAdapter(SequenceAdapter):
    # Test seam for the explicit marker on FocusedGoogleNativeAdapter.
    focused_commander_transport = True


def _unavailable():
    return ProviderAdapterError(
        "TEMPORARY_PROVIDER_ERROR",
        http_status=503,
        retryable=True,
    )


def _limited():
    return ProviderAdapterError(
        "RATE_LIMITED",
        http_status=429,
        retryable=False,
    )


class GoogleBackpressureRecoveryTests(unittest.TestCase):
    def _binding(self, adapter):
        return LiveAgentBinding("EXECUTOR", "google", MODEL, "GEMINI", adapter, _policy())

    def _metrics(self):
        metrics = LiveCallMetrics()
        metrics.configure_budgets({"google": 24}, {"google": 81920})
        return metrics

    @patch("scripts.resilient_live_call.time.sleep")
    def test_observed_503_503_429_429_can_make_one_final_fresh_attempt(self, sleep):
        adapter = SequenceAdapter([_unavailable(), _unavailable(), _limited(), _limited(), _ok()])
        metrics = self._metrics()
        result = call_model_with_bounded_recovery(
            self._binding(adapter),
            _task(),
            {"phase": "EXECUTE"},
            metrics=metrics,
            instruction="Return JSON only.",
        )
        self.assertEqual(result["summary"], "recovered")
        self.assertEqual(result["provider_attempts"], 5)
        self.assertEqual(result["trailing_rate_limit_recovery_count"], 1)
        self.assertEqual(MAX_TRAILING_RATE_LIMIT_RECOVERIES, 1)
        self.assertEqual(len(adapter.calls), 5)
        self.assertEqual(metrics.snapshot()["external_model_calls"], 5)
        self.assertEqual(sleep.call_count, 4)
        self.assertGreaterEqual(sleep.call_args_list[-1].args[0], TRAILING_RATE_LIMIT_BACKOFF_SECONDS)
        request_ids = [call["options"]["request_id"] for call in adapter.calls]
        self.assertEqual(len(request_ids), len(set(request_ids)))

    @patch("scripts.resilient_live_call.time.sleep")
    def test_generic_first_call_429_remains_fail_closed(self, sleep):
        adapter = SequenceAdapter([_limited(), _ok()])
        metrics = self._metrics()
        with self.assertRaises(ProviderInterrupted) as caught:
            call_model_with_bounded_recovery(
                self._binding(adapter),
                _task(),
                {"phase": "EXECUTE"},
                metrics=metrics,
                instruction="Return JSON only.",
            )
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(caught.exception.actual_requests, 1)
        self.assertEqual(caught.exception.actual_tokens, 0)
        sleep.assert_not_called()

    @patch("scripts.resilient_live_call.time.sleep")
    def test_focused_free_route_first_429_gets_two_bounded_cooldowns(self, sleep):
        adapter = FocusedSequenceAdapter([_limited(), _limited(), _ok()])
        metrics = self._metrics()
        result = call_model_with_bounded_recovery(
            self._binding(adapter),
            _task(),
            {"phase": "EXECUTE"},
            metrics=metrics,
            instruction="Return JSON only.",
        )
        self.assertEqual(result["summary"], "recovered")
        self.assertEqual(result["provider_attempts"], 3)
        self.assertEqual(result["primary_rate_limit_recovery_count"], 2)
        self.assertEqual(MAX_PRIMARY_RATE_LIMIT_RECOVERIES, 2)
        self.assertEqual(len(adapter.calls), 3)
        self.assertEqual(metrics.snapshot()["external_model_calls"], 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(sleep.call_args_list[0].args[0], PRIMARY_RATE_LIMIT_BACKOFF_SECONDS[0])
        self.assertEqual(sleep.call_args_list[1].args[0], PRIMARY_RATE_LIMIT_BACKOFF_SECONDS[1])
        request_ids = [call["options"]["request_id"] for call in adapter.calls]
        self.assertEqual(len(request_ids), len(set(request_ids)))


if __name__ == "__main__":
    unittest.main()
