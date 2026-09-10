import json
from types import SimpleNamespace
import unittest

from scripts.execution_scope import ExecutionPolicy
from scripts.live_staging_runner import LiveAgentBinding, LiveCallMetrics
from scripts.mission_scheduler import ProviderInterrupted
from scripts.provider_adapters import ProviderAdapterError
from scripts.resilient_live_call import (
    RECOVERY_MAX_OUTPUT_TOKENS,
    RECOVERY_REPOSITORY_CONTEXT_CHARS,
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
    def unavailable(status=503):
        return ProviderAdapterError(
            "TEMPORARY_PROVIDER_ERROR",
            http_status=status,
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
        self.assertEqual(metrics.snapshot()["external_model_calls"], 2)
        self.assertEqual(len(adapter.calls), 2)
        self.assertNotEqual(
            adapter.calls[0]["options"]["request_id"],
            adapter.calls[1]["options"]["request_id"],
        )
        self.assertEqual(adapter.calls[0]["model"], adapter.calls[1]["model"])

    def test_recovery_request_compacts_repository_context_and_output_budget(self):
        adapter = SequenceAdapter([self.unavailable(500), ok_response()])
        oversized = "x" * 12000
        # _call_model serializes this context into the user JSON. Place large
        # text where the focused prompt contract carries it after monkeypatching
        # is unnecessary: a user JSON is produced by the real prompt function.
        context = {
            "phase": "EXECUTE",
            "candidate": {
                "repository_context": {
                    "source_head": "abc",
                    "read_only": True,
                    "fixed_allowlist": True,
                    "files": {"a.py": oversized, "b.py": oversized, "c.py": oversized},
                }
            },
        }
        self.invoke(adapter, context=context)

        self.assertEqual(len(adapter.calls), 2)
        second = adapter.calls[1]
        self.assertLessEqual(second["options"]["max_tokens"], RECOVERY_MAX_OUTPUT_TOKENS)
        self.assertLessEqual(
            sum(len(str(message.get("content") or "")) for message in second["messages"] if isinstance(message, dict)),
            40000,
        )

    def test_payload_compactor_has_bounded_repository_budget(self):
        from scripts.resilient_live_call import _compact_repository_payload

        compact = _compact_repository_payload({
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
        })
        files = compact["repository_context"]["files"]
        self.assertLessEqual(sum(len(value) for value in files.values()), RECOVERY_REPOSITORY_CONTEXT_CHARS)
        self.assertTrue(compact["repository_context"]["recovery_compacted"])
        self.assertEqual(compact["mission_id"], "M1")

    def test_two_explicit_503s_stop_without_a_third_call_and_are_settleable(self):
        adapter = SequenceAdapter([self.unavailable(), self.unavailable()])
        metrics = LiveCallMetrics()
        metrics.configure_budgets({"google": 24}, {"google": 81920})

        with self.assertRaises(ProviderInterrupted) as caught:
            call_model_with_bounded_recovery(
                self.binding(adapter), task(), {"phase": "EXECUTE"},
                metrics=metrics, instruction="Return JSON only.",
            )

        self.assertEqual(len(adapter.calls), 2)
        self.assertEqual(caught.exception.actual_requests, 2)
        self.assertEqual(caught.exception.actual_tokens, 0)
        self.assertIn("HTTP_503", str(caught.exception))
        self.assertEqual(metrics.snapshot()["external_model_calls"], 2)

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


if __name__ == "__main__":
    unittest.main()
