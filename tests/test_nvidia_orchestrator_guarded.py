import json
import os
import tempfile
from pathlib import Path
import unittest

from scripts.mission_scheduler import MissionReservationLedger
from scripts.provider_adapters import ProviderAdapterError
from scripts.run_nvidia_orchestrator_guarded import (
    DurableNvidiaAdapter,
    _current_recovery_context,
    _latest_provider_diagnostic,
    _promote_deferred_nvidia_admission,
)


HEAD = "a" * 40
MISSION = "nvidia-autonomous-orchestrator-123"
REQUEST = "nvidia-autonomous-orchestrator-123:rev1:abcdef12"
MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"


class FakeSuccessAdapter:
    provider_id = "nvidia"
    config = {}

    def generate(self, model_id, messages, **options):
        return {
            "text": "{}",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }


class FakeTimeoutAdapter:
    provider_id = "nvidia"
    config = {}

    def generate(self, model_id, messages, **options):
        raise TimeoutError("simulated timeout")


def ledger_at(path):
    return MissionReservationLedger(
        path,
        provider_limits={"nvidia": {"requests": 8, "tokens": 8 * 32768}},
    )


def deferred_probe():
    return {
        "providers": [{
            "provider": "nvidia",
            "model": MODEL,
            "status": "PROBE_DEFERRED_TO_AGENT",
            "probe_mode": "DIRECT_AGENT_LIVENESS",
            "direct_agent_admission": True,
            "selected_route": "FREE_ENDPOINT",
            "staging_only": True,
            "paid_fallback": False,
            "automatic_model_fallback": False,
            "generic_paid_router_disabled": True,
            "model_calls": 0,
            "request_hard_limit": 0,
        }]
    }


def secure_evidence(*, paid_transition=False):
    return {
        "providers": {
            "nvidia": {
                "models": {
                    MODEL: {
                        "model_verified": True,
                        "auth_verified": True,
                        "endpoint_verified": True,
                        "current": True,
                        "free_access_type": "FREE_ENDPOINT",
                        "free_route_selected": True,
                        "limited_staging_probe_allowed": True,
                        "limited_staging_probe_blockers": [],
                        "paid_fallback_possible": False,
                        "paid_transition_possible": paid_transition,
                        "pricing_metadata": {
                            "exact_model_verified": True,
                            "fixed_free_endpoint": True,
                            "free_endpoint_available": True,
                            "free_price_verified": True,
                            "paid_fallback_disabled": True,
                            "selected_route": "FREE_ENDPOINT",
                        },
                    }
                }
            }
        }
    }


class GuardedNvidiaAdapterTests(unittest.TestCase):
    def test_success_settles_exactly_one_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ledger_at(Path(tmp) / "ledger.json")
            adapter = DurableNvidiaAdapter(FakeSuccessAdapter(), ledger, HEAD)
            result = adapter.generate(
                MODEL,
                [{"role": "user", "content": "x"}],
                request_id=REQUEST,
                mission_id=MISSION,
            )
            self.assertEqual(result["usage"]["completion_tokens"], 20)
            self.assertEqual(adapter.calls_this_carrier, 1)
            self.assertEqual(adapter.current_state, "SETTLED")
            snapshot = ledger.snapshot(MISSION)
            self.assertEqual(len(snapshot["reservations"]), 1)
            self.assertEqual(snapshot["reservations"][0]["state"], "settled")
            self.assertEqual(snapshot["reservations"][0]["actual_requests"], 1)

    def test_timeout_is_unsettled_not_zero_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ledger_at(Path(tmp) / "ledger.json")
            adapter = DurableNvidiaAdapter(FakeTimeoutAdapter(), ledger, HEAD)
            with self.assertRaises(TimeoutError):
                adapter.generate(
                    MODEL,
                    [{"role": "user", "content": "x"}],
                    request_id=REQUEST,
                    mission_id=MISSION,
                )
            self.assertEqual(adapter.calls_this_carrier, 1)
            self.assertEqual(adapter.current_state, "UNSETTLED")
            snapshot = ledger.snapshot(MISSION)
            self.assertEqual(snapshot["unsettled_count"], 1)
            self.assertTrue(snapshot["reservations"][0]["dispatch_started"])

    def test_duplicate_request_id_is_blocked_before_second_provider_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            first = DurableNvidiaAdapter(FakeSuccessAdapter(), ledger_at(path), HEAD)
            first.generate(
                MODEL,
                [{"role": "user", "content": "x"}],
                request_id=REQUEST,
                mission_id=MISSION,
            )
            second = DurableNvidiaAdapter(FakeSuccessAdapter(), ledger_at(path), HEAD)
            with self.assertRaises(ProviderAdapterError):
                second.generate(
                    MODEL,
                    [{"role": "user", "content": "x"}],
                    request_id=REQUEST,
                    mission_id=MISSION,
                )
            self.assertEqual(second.calls_this_carrier, 0)
            self.assertEqual(second.current_state, "SETTLED")

    def test_verified_deferred_admission_is_bridged_without_persisted_probe_call(self):
        original = deferred_probe()
        result = _promote_deferred_nvidia_admission(original, secure_evidence())
        row = result["providers"][0]
        self.assertEqual(row["status"], "PROBE_OK")
        self.assertEqual(row["source_status"], "PROBE_DEFERRED_TO_AGENT")
        self.assertEqual(row["compatibility_admission"], "FIRST_REAL_AGENT_CALL_IS_LIVENESS")
        self.assertTrue(result["nvidia_deferred_admission_bridged"])
        self.assertEqual(original["providers"][0]["status"], "PROBE_DEFERRED_TO_AGENT")

    def test_deferred_admission_never_bridges_known_paid_transition(self):
        original = deferred_probe()
        result = _promote_deferred_nvidia_admission(original, secure_evidence(paid_transition=True))
        self.assertEqual(result["providers"][0]["status"], "PROBE_DEFERRED_TO_AGENT")
        self.assertNotIn("nvidia_deferred_admission_bridged", result)

    def test_deferred_admission_never_bridges_wrong_route(self):
        original = deferred_probe()
        original["providers"][0]["selected_route"] = "PARTNER_ENDPOINT"
        result = _promote_deferred_nvidia_admission(original, secure_evidence())
        self.assertEqual(result["providers"][0]["status"], "PROBE_DEFERRED_TO_AGENT")
        self.assertNotIn("nvidia_deferred_admission_bridged", result)

    def test_current_recovery_context_exposes_safe_daily_quota_class(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            (artifacts / "ai_army_coordination.json").write_text(
                json.dumps({"state": "GOOGLE_PROVIDER_DEGRADED_NVIDIA_LEAD", "next_action": "RUN_AT_MOST_ONE_GUARDED_NVIDIA_LEAD_CALL"}),
                encoding="utf-8",
            )
            (artifacts / "live_staging_report.json").write_text(
                json.dumps({
                    "status": "blocked",
                    "runtime": {"stop_reason": "PROVIDER_INTERRUPTED", "revision_count": 0},
                    "budget": {"requests_used": 1, "unsettled_requests": 0},
                    "live_staging": {"providers": {"google": 1}, "executor_provider": "google", "reviewer_provider": "nvidia"},
                }),
                encoding="utf-8",
            )
            (artifacts / "google_staging_readiness.json").write_text(
                json.dumps({"readiness_mode": "PROBE_DEFERRED_RECOVERY", "deferred_recovery_ready": True}),
                encoding="utf-8",
            )
            (artifacts / "provider_interruptions.jsonl").write_text(
                json.dumps({
                    "provider": "google",
                    "model": "gemini-3.8-flash",
                    "error_class": "GOOGLE_DAILY_QUOTA_EXHAUSTED",
                    "http_status": 429,
                    "retry_after_seconds": None,
                    "retryable": False,
                    "raw_response_retained": False,
                    "ignored_secret": "must-not-propagate",
                }) + "\n",
                encoding="utf-8",
            )
            previous = Path.cwd()
            try:
                os.chdir(root)
                diagnostic = _latest_provider_diagnostic("google")
                context = _current_recovery_context()
            finally:
                os.chdir(previous)
            self.assertEqual(diagnostic["error_class"], "GOOGLE_DAILY_QUOTA_EXHAUSTED")
            self.assertEqual(diagnostic["http_status"], 429)
            self.assertNotIn("ignored_secret", diagnostic)
            self.assertTrue(context["google_daily_quota_exhausted"])
            self.assertFalse(context["google_quota_limit_zero"])
            self.assertEqual(context["google_readiness_mode"], "PROBE_DEFERRED_RECOVERY")
            self.assertEqual(context["google_provider_diagnostic"]["model"], "gemini-3.8-flash")

    def test_unknown_diagnostic_class_is_redacted_before_nvidia_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            (artifacts / "provider_interruptions.jsonl").write_text(
                json.dumps({"provider": "google", "model": "gemini-3.8-flash", "error_class": "UNTRUSTED_LONG_DETAIL"}) + "\n",
                encoding="utf-8",
            )
            previous = Path.cwd()
            try:
                os.chdir(root)
                diagnostic = _latest_provider_diagnostic("google")
            finally:
                os.chdir(previous)
            self.assertEqual(diagnostic["error_class"], "OTHER_REDACTED_PROVIDER_ERROR")


if __name__ == "__main__":
    unittest.main()
