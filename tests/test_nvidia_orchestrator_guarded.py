import tempfile
from pathlib import Path
import unittest

from scripts.mission_scheduler import MissionReservationLedger
from scripts.provider_adapters import ProviderAdapterError
from scripts.run_nvidia_orchestrator_guarded import (
    DurableNvidiaAdapter,
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


if __name__ == "__main__":
    unittest.main()
