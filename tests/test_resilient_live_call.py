import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.execution_scope import ExecutionPolicy
from scripts.live_staging_runner import LiveAgentBinding, LiveCallMetrics
from scripts.mission_scheduler import ProviderInterrupted
from scripts.provider_adapters import ProviderAdapterError
from scripts.resilient_live_call import (
    FINAL_RECOVERY_MAX_OUTPUT_TOKENS,
    FINAL_RECOVERY_REPOSITORY_CONTEXT_CHARS,
    MAX_CONCLUSIVE_5XX_RECOVERIES,
    RECOVERY_MAX_OUTPUT_TOKENS,
    RECOVERY_REPOSITORY_CONTEXT_CHARS,
    _compact_recovery_messages,
    call_model_with_bounded_recovery,
)


MODEL = "gemini-3.8-flash"


def policy():
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
    )


def task():
    return SimpleNamespace(
        mission_id="RECOVERY-MISSION",
        task_id="RECOVERY-TASK",
        owner_corps="GOOGLE",
        role="ORCHESTRATION_BUGFIX_PROJECT",
        required_capabilities=("structured_output",),
        metadata={},
    )


def ok_response():
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
        self.calls.append({"model": model_id, "messages": messages, "options": dict(options)})
        event = self.events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event


class ResilientLiveCallTests(unittest.TestCase):
    def setUp(self):
        # Production uses short bounded backoff; unit tests verify the decision
        # without paying wall-clock delay.
        self.sleep_patch = patch("scripts.resilient_live_call.time.sleep")
        self.mock_sleep = self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

    def binding(self, adapter):
        return LiveAgentBinding("EXECUTOR", "google", MODEL, "GEMINI", adapter, policy())

    def invoke(self, adapter, *, context=None):
        metrics = LiveCallMetrics()
        metrics.configure_budgets({"google": 24}, {"google": 81920})
        result = call_model_with_bounded_recovery(
            self.binding(adapter),
            task(),
            context or {"phase": "EXECUTE", "iteration_count": 1, "revision_count": 0},
            metrics=metrics,
            instruction="Return JSON only.",
        )
        return result, metrics

    @staticmethod
    def unavailable(status=503, retry_after_seconds=None):
        return ProviderAdapterError(
            "TEMPORARY_PROVIDER_ERROR",
            http_status=status,
            retry_after_seconds=retry_after_seconds,
            retryable=True,
        )

    def test_one_explicit_503_gets_one_fresh_recovery_attempt(self):
        adapter = SequenceAdapter([self.unavailable(), ok_response()])
        result, metrics = self.invoke(adapter)

        self.assertEqual(result["summary"], "recovered")
        self.assertEqual(result["requests_used"], 2)
        self.assertEqual(result["provider_attempts"], 2)
        self.assertEqual(result["conclusive_5xx_recovery_count"], 1)
        self.assertTrue(result["recovery_context_compacted"])
        self.assertEqual(result["recovery_level_reached"], 1)
        self.assertEqual(metrics.snapshot()["external_model_calls"], 2)
        self.assertEqual(len(adapter.calls), 2)
        self.assertNotEqual(
            adapter.calls[0]["options"]["request_id"],
            adapter.calls[1]["options"]["request_id"],
        )
        self.assertEqual(adapter.calls[0]["model"], adapter.calls[1]["model"])
        self.assertLessEqual(adapter.calls[1]["options"]["max_tokens"], RECOVERY_MAX_OUTPUT_TOKENS)
        self.assertEqual(self.mock_sleep.call_count, 1)

    def test_two_500s_can_recover_on_third_progressively_smaller_attempt(self):
        adapter = SequenceAdapter([self.unavailable(500), self.unavailable(500), ok_response()])
        result, metrics = self.invoke(adapter)

        self.assertEqual(result["summary"], "recovered")
        self.assertEqual(result["requests_used"], 3)
        self.assertEqual(result["provider_attempts"], 3)
        self.assertEqual(result["conclusive_5xx_recovery_count"], 2)
        self.assertEqual(result["recovery_level_reached"], 2)
        self.assertEqual(metrics.snapshot()["external_model_calls"], 3)
        self.assertEqual(len(adapter.calls), 3)
        self.assertLessEqual(adapter.calls[1]["options"]["max_tokens"], RECOVERY_MAX_OUTPUT_TOKENS)
        self.assertLessEqual(adapter.calls[2]["options"]["max_tokens"], FINAL_RECOVERY_MAX_OUTPUT_TOKENS)
        self.assertLess(
            len(adapter.calls[2]["messages"][1]["content"]),
            len(adapter.calls[1]["messages"][1]["content"]),
        )
        self.assertEqual(self.mock_sleep.call_count, 2)

    def test_recovery_message_compactor_reduces_real_focused_payload_shape(self):
        oversized = "x" * 12000
        payload = {
            "mission_id": "M1",
            "task_id": "T1",
            "task_metadata": {"bound_objective": "preserve this objective"},
            "repository_context": {
                "source_head": "abc",
                "read_only": True,
                "fixed_allowlist": True,
                "files": {"a.py": oversized, "b.py": oversized, "c.py": oversized},
            },
        }
        messages = [
            {"role": "system", "content": "Return JSON only."},
            {"role": "user", "content": json.dumps(payload)},
        ]
        compacted = _compact_recovery_messages(messages)
        final_compacted = _compact_recovery_messages(messages, recovery_number=2)
        self.assertLess(len(compacted[1]["content"]), len(messages[1]["content"]))
        self.assertLess(len(final_compacted[1]["content"]), len(compacted[1]["content"]))
        parsed = json.loads(compacted[1]["content"])
        final_parsed = json.loads(final_compacted[1]["content"])
        self.assertEqual(parsed["mission_id"], "M1")
        self.assertEqual(parsed["task_metadata"]["bound_objective"], "preserve this objective")
        self.assertTrue(parsed["repository_context"]["recovery_compacted"])
        self.assertEqual(parsed["transport_recovery"]["reason"], "CONCLUSIVE_TEMPORARY_5XX")
        self.assertEqual(final_parsed["transport_recovery"]["recovery_number"], 2)

    def test_payload_compactor_has_bounded_repository_budget(self):
        from scripts.resilient_live_call import _compact_repository_payload

        payload = {
            "mission_id": "M1",
            "repository_context": {
                "source_head": "abc",
                "read_only": True,
                "fixed_allowlist": True,
                "files": {
                    "a.py": "a" * 9000,
                    "b.py": "b" * 9000,
                    "c.py": "c" * 9000,
                },
            },
        }
        compact = _compact_repository_payload(payload)
        final_compact = _compact_repository_payload(payload, recovery_number=2)
        files = compact["repository_context"]["files"]
        final_files = final_compact["repository_context"]["files"]
        self.assertLessEqual(sum(len(value) for value in files.values()), RECOVERY_REPOSITORY_CONTEXT_CHARS)
        self.assertLessEqual(sum(len(value) for value in final_files.values()), FINAL_RECOVERY_REPOSITORY_CONTEXT_CHARS)
        self.assertTrue(compact["repository_context"]["recovery_compacted"])
        self.assertEqual(compact["mission_id"], "M1")

    def test_three_explicit_503s_stop_without_a_fourth_call_and_are_settleable(self):
        adapter = SequenceAdapter([self.unavailable(), self.unavailable(), self.unavailable()])
        metrics = LiveCallMetrics()
        metrics.configure_budgets({"google": 24}, {"google": 81920})

        with self.assertRaises(ProviderInterrupted) as caught:
            call_model_with_bounded_recovery(
                self.binding(adapter), task(), {"phase": "EXECUTE"},
                metrics=metrics, instruction="Return JSON only.",
            )

        self.assertEqual(MAX_CONCLUSIVE_5XX_RECOVERIES, 2)
        self.assertEqual(len(adapter.calls), 3)
        self.assertEqual(caught.exception.actual_requests, 3)
        self.assertEqual(caught.exception.actual_tokens, 0)
        self.assertIn("HTTP_503", str(caught.exception))
        self.assertEqual(metrics.snapshot()["external_model_calls"], 3)
        self.assertEqual(self.mock_sleep.call_count, 2)

    def test_ambiguous_timeout_is_never_replayed(self):
        adapter = SequenceAdapter([
            ProviderAdapterError("NETWORK_TIMEOUT", retryable=True),
            ok_response(),
        ])
        metrics = LiveCallMetrics()
        metrics.configure_budgets({"google": 24}, {"google": 81920})

        with self.assertRaises(ProviderInterrupted) as caught:
            call_model_with_bounded_recovery(
                self.binding(adapter), task(), {"phase": "EXECUTE"},
                metrics=metrics, instruction="Return JSON only.",
            )

        self.assertEqual(len(adapter.calls), 1)
        self.assertIsNone(caught.exception.actual_requests)
        self.assertIsNone(caught.exception.actual_tokens)
        self.assertEqual(self.mock_sleep.call_count, 0)

    def test_503_then_ambiguous_timeout_stops_after_two_calls_unsettled(self):
        adapter = SequenceAdapter([
            self.unavailable(),
            ProviderAdapterError("NETWORK_TIMEOUT", retryable=True),
        ])
        metrics = LiveCallMetrics()
        metrics.configure_budgets({"google": 24}, {"google": 81920})

        with self.assertRaises(ProviderInterrupted) as caught:
            call_model_with_bounded_recovery(
                self.binding(adapter), task(), {"phase": "EXECUTE"},
                metrics=metrics, instruction="Return JSON only.",
            )

        self.assertEqual(len(adapter.calls), 2)
        self.assertIsNone(caught.exception.actual_requests)
        self.assertEqual(metrics.snapshot()["external_model_calls"], 1)
        self.assertEqual(self.mock_sleep.call_count, 1)

    def test_rate_limit_is_conclusive_but_not_auto_retried(self):
        adapter = SequenceAdapter([
            ProviderAdapterError("RATE_LIMITED", http_status=429, retry_after_seconds=2, retryable=False),
            ok_response(),
        ])
        metrics = LiveCallMetrics()
        metrics.configure_budgets({"google": 24}, {"google": 81920})

        with self.assertRaises(ProviderInterrupted) as caught:
            call_model_with_bounded_recovery(
                self.binding(adapter), task(), {"phase": "EXECUTE"},
                metrics=metrics, instruction="Return JSON only.",
            )

        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(caught.exception.actual_requests, 1)
        self.assertEqual(caught.exception.actual_tokens, 0)
        self.assertEqual(self.mock_sleep.call_count, 0)


if __name__ == "__main__":
    unittest.main()
