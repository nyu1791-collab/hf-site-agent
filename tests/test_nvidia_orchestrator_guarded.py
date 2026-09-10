import tempfile
from pathlib import Path
import unittest

from scripts.mission_scheduler import MissionReservationLedger
from scripts.provider_adapters import ProviderAdapterError
from scripts.run_nvidia_orchestrator_guarded import DurableNvidiaAdapter


HEAD = "a" * 40
MISSION = "nvidia-autonomous-orchestrator-123"
REQUEST = "nvidia-autonomous-orchestrator-123:rev1:abcdef12"


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


class GuardedNvidiaAdapterTests(unittest.TestCase):
    def test_success_settles_exactly_one_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ledger_at(Path(tmp) / "ledger.json")
            adapter = DurableNvidiaAdapter(FakeSuccessAdapter(), ledger, HEAD)
            result = adapter.generate(
                "nvidia/nemotron-3.5-lightning-30b-a3b",
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
                    "nvidia/nemotron-3.5-lightning-30b-a3b",
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
                "nvidia/nemotron-3.5-lightning-30b-a3b",
                [{"role": "user", "content": "x"}],
                request_id=REQUEST,
                mission_id=MISSION,
            )
            second = DurableNvidiaAdapter(FakeSuccessAdapter(), ledger_at(path), HEAD)
            with self.assertRaises(ProviderAdapterError):
                second.generate(
                    "nvidia/nemotron-3.5-lightning-30b-a3b",
                    [{"role": "user", "content": "x"}],
                    request_id=REQUEST,
                    mission_id=MISSION,
                )
            self.assertEqual(second.calls_this_carrier, 0)
            self.assertEqual(second.current_state, "SETTLED")


if __name__ == "__main__":
    unittest.main()
