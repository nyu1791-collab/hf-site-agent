from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest

from scripts.provider_controls import (
    CircuitState,
    ProviderQuotaLedger,
    QuotaGuardError,
    ledger_from_registry,
    quota_zone,
)
from scripts.provider_registry import load_provider_registry


class ProviderControlsTests(unittest.TestCase):
    def test_provider_ledgers_are_separate_namespaces(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provider-usage.json"
            google = ProviderQuotaLedger("google", path, daily_limit=10, hard_stop=9, rpm_limit=10)
            groq = ProviderQuotaLedger("groq", path, daily_limit=10, hard_stop=9, rpm_limit=10)
            google.reserve(request_id="g-1", mission_id="m-g", agent_id="a-g", model="google-model")
            groq.reserve(request_id="q-1", mission_id="m-q", agent_id="a-q", model="groq-model")
            self.assertEqual(google.summary()["used"], 1)
            self.assertEqual(groq.summary()["used"], 1)
            self.assertEqual(set(google.summary()) & {"provider_id"}, {"provider_id"})

    def test_unknown_quota_is_a_stop_condition(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = ProviderQuotaLedger("nvidia", Path(directory) / "usage.json", daily_limit=None, rpm_limit=None)
            with self.assertRaises(QuotaGuardError) as caught:
                ledger.reserve(request_id="n-1", mission_id="m", agent_id="a", model="model")
            self.assertEqual(caught.exception.reason, "QUOTA_UNKNOWN")

    def test_red_zone_blocks_new_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = ProviderQuotaLedger("openrouter", Path(directory) / "usage.json", daily_limit=20, hard_stop=19, rpm_limit=20)
            for index in range(18):
                ledger.reserve(request_id=f"r-{index}", mission_id="m", agent_id="a", model="model")
                ledger.record_result(f"r-{index}", success=True, http_status=200)
            self.assertEqual(ledger.summary()["zone"], "ORANGE")
            # The prospective request would cross 95%, so the guard blocks it
            # before a RED-zone request can be sent.
            with self.assertRaises(QuotaGuardError):
                ledger.reserve(request_id="r-18", mission_id="m", agent_id="a", model="model")

    def test_429_opens_only_one_provider_circuit_and_no_retry_is_performed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            openrouter = ProviderQuotaLedger("openrouter", path, daily_limit=10, hard_stop=9, rpm_limit=10)
            groq = ProviderQuotaLedger("groq", path, daily_limit=10, hard_stop=9, rpm_limit=10)
            openrouter.reserve(request_id="or-1", mission_id="m", agent_id="a", model="worker")
            openrouter.record_result("or-1", success=False, http_status=429, retry=0, error_class="RATE_LIMITED", retry_after_seconds=7)
            self.assertEqual(openrouter.summary()["circuit_state"], CircuitState.OPEN)
            with self.assertRaises(QuotaGuardError):
                openrouter.reserve(request_id="or-2", mission_id="m", agent_id="a", model="worker")
            groq.reserve(request_id="gr-1", mission_id="m", agent_id="a", model="commander")
            self.assertEqual(groq.summary()["circuit_state"], CircuitState.CLOSED)

    def test_half_open_requires_probe_then_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = ProviderQuotaLedger("google", Path(directory) / "usage.json", daily_limit=10, hard_stop=9, rpm_limit=10)
            ledger.circuit.open("RATE_LIMITED")
            ledger.circuit.half_open()
            with self.assertRaises(QuotaGuardError):
                ledger.reserve(request_id="g-1", mission_id="m", agent_id="a", model="model")
            ledger.recover_after_probe(True)
            self.assertEqual(ledger.summary()["circuit_state"], CircuitState.CLOSED)

    def test_quota_zone_thresholds(self):
        self.assertEqual(quota_zone(79, 100), "GREEN")
        self.assertEqual(quota_zone(80, 100), "YELLOW")
        self.assertEqual(quota_zone(90, 100), "ORANGE")
        self.assertEqual(quota_zone(95, 100), "RED")
        self.assertEqual(quota_zone(0, None), "UNKNOWN")

    def test_registry_factory_keeps_openrouter_limits_provider_specific(self):
        registry = load_provider_registry()
        with tempfile.TemporaryDirectory() as directory:
            openrouter = ledger_from_registry("openrouter", registry, Path(directory) / "usage.json")
            self.assertEqual(openrouter.daily_limit, 1000)
            self.assertEqual(openrouter.hard_stop, 900)
            self.assertEqual(openrouter.rpm_limit, 15)
            google = ledger_from_registry("google", registry, Path(directory) / "usage.json")
            with self.assertRaises(QuotaGuardError) as caught:
                google.reserve(request_id="g-1", mission_id="m", agent_id="a", model="model")
            self.assertEqual(caught.exception.reason, "QUOTA_UNKNOWN")

    def test_separate_process_instances_reserve_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.json"
            ledgers = [
                ProviderQuotaLedger("groq", path, daily_limit=2, hard_stop=1, rpm_limit=10),
                ProviderQuotaLedger("groq", path, daily_limit=2, hard_stop=1, rpm_limit=10),
            ]
            gate = threading.Barrier(2)

            def reserve(index):
                gate.wait()
                try:
                    ledgers[index].reserve(
                        request_id=f"race-{index}", mission_id="mission-race",
                        agent_id="agent-race", model="model",
                    )
                    return "reserved"
                except QuotaGuardError as error:
                    return error.reason

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(reserve, (0, 1)))
            self.assertEqual(results.count("reserved"), 1)
            self.assertEqual(results.count("QUOTA_GUARD_BLOCKED"), 1)
            self.assertTrue(ledgers[0].durability_status()["cross_process_atomic"])
            self.assertFalse(ledgers[0].durability_status()["cross_runner_durable"])


if __name__ == "__main__":
    unittest.main()
