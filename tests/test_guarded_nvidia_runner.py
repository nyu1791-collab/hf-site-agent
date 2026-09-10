from pathlib import Path
import tempfile
import unittest

from scripts.mission_scheduler import MissionReservationLedger
from scripts.provider_adapters import ProviderAdapterError
from scripts.run_nvidia_orchestrator_guarded import (
    CALL_TOKEN_RESERVATION,
    DurableNvidiaAdapter,
    MAX_MISSION_REQUESTS,
    MAX_MISSION_TOKEN_BUDGET,
)


HEAD = "a" * 40
MISSION = "nvidia-autonomous-orchestrator-test"
REQUEST = f"{MISSION}:rev1:abc12345"
MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"


class FakeAdapter:
    provider_id = "nvidia"
    config = {"provider_id": "nvidia"}

    def __init__(self, *, fail=False, report_usage=True):
        self.fail = fail
        self.report_usage = report_usage
        self.calls = 0

    def generate(self, model_id, messages, **options):
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated timeout")
        usage = {"prompt_tokens": 100, "completion_tokens": 20} if self.report_usage else {}
        return {"model": model_id, "text": "{}", "usage": usage}


def ledger(path):
    return MissionReservationLedger(
        path,
        provider_limits={"nvidia": {"requests": MAX_MISSION_REQUESTS, "tokens": MAX_MISSION_TOKEN_BUDGET}},
    )


def options():
    return {"request_id": REQUEST, "mission_id": MISSION, "agent_id": "lead-engineer-nvidia"}


class GuardedNvidiaRunnerTests(unittest.TestCase):
    def test_successful_call_is_reserved_then_settled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            fake = FakeAdapter()
            guarded = DurableNvidiaAdapter(fake, ledger(path), HEAD)
            result = guarded.generate(MODEL, [{"role": "user", "content": "review"}], **options())
            self.assertEqual(result["model"], MODEL)
            self.assertEqual(fake.calls, 1)
            self.assertEqual(guarded.current_state, "SETTLED")
            snapshot = ledger(path).snapshot(MISSION)
            record = snapshot["reservations"][0]
            self.assertTrue(record["dispatch_started"])
            self.assertEqual(record["state"], "settled")
            self.assertEqual(record["actual_requests"], 1)
            self.assertEqual(record["actual_tokens"], 120)

    def test_cross_run_duplicate_request_is_blocked_before_second_provider_call(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            first = FakeAdapter()
            DurableNvidiaAdapter(first, ledger(path), HEAD).generate(MODEL, [{"role": "user", "content": "review"}], **options())
            second = FakeAdapter()
            guarded = DurableNvidiaAdapter(second, ledger(path), HEAD)
            with self.assertRaises(ProviderAdapterError) as caught:
                guarded.generate(MODEL, [{"role": "user", "content": "review"}], **options())
            self.assertEqual(caught.exception.error_class, "DUPLICATE_NVIDIA_CALL_BLOCKED")
            self.assertEqual(second.calls, 0)

    def test_timeout_after_dispatch_stays_unsettled_and_consumes_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            fake = FakeAdapter(fail=True)
            guarded = DurableNvidiaAdapter(fake, ledger(path), HEAD)
            with self.assertRaises(RuntimeError):
                guarded.generate(MODEL, [{"role": "user", "content": "review"}], **options())
            self.assertEqual(guarded.current_state, "UNSETTLED")
            snapshot = ledger(path).snapshot(MISSION)
            record = snapshot["reservations"][0]
            self.assertTrue(record["dispatch_started"])
            self.assertEqual(record["state"], "unsettled")
            self.assertEqual(snapshot["unsettled_count"], 1)

    def test_unreported_usage_consumes_full_reserved_token_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            fake = FakeAdapter(report_usage=False)
            guarded = DurableNvidiaAdapter(fake, ledger(path), HEAD)
            guarded.generate(MODEL, [{"role": "user", "content": "review"}], **options())
            record = ledger(path).snapshot(MISSION)["reservations"][0]
            self.assertEqual(record["actual_tokens"], CALL_TOKEN_RESERVATION)


if __name__ == "__main__":
    unittest.main()
