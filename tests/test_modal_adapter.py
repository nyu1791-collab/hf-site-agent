from decimal import Decimal
from types import SimpleNamespace
import unittest

from scripts.modal_adapter import ModalAdapter, ModalAdapterError, normalize_error
from scripts.modal_cost_guard import ModalBillingSnapshot, ModalCostGuard, ModalJobSpec


class FakeBilling:
    def summary(self, cycle):
        return SimpleNamespace(metered_cost=Decimal("1.25"), usage_limit=Decimal("10"), credit_remaining=Decimal("8.75"), cycle=cycle)

    def rates(self):
        return [{"resource": "cpu"}]

    def report(self, start, end, *, resolution, tag_names):
        return [{"start": start, "end": end, "resolution": resolution, "tag_count": len(tag_names or [])}]


class FakeWorkspace:
    billing = FakeBilling()

    @classmethod
    def from_context(cls):
        return cls()


class FakeModal:
    __version__ = "0.test"
    Workspace = FakeWorkspace


class ModalAdapterTests(unittest.TestCase):
    def test_secret_presence_reports_booleans_only(self):
        values = ModalAdapter.secret_presence({"MODAL_TOKEN_ID": "id-value", "MODAL_TOKEN_SECRET": "secret-value"})
        self.assertEqual(values, {"modal_token_id_present": True, "modal_token_secret_present": True})
        self.assertNotIn("id-value", str(values))
        self.assertNotIn("secret-value", str(values))

    def test_default_network_is_inert(self):
        adapter = ModalAdapter()
        result = adapter.get_billing_summary("2026-09")
        self.assertEqual(result["status"], "LIVE_MODAL_DISABLED")
        self.assertIn(adapter.sdk_version()["status"], {"SDK_NOT_INSTALLED", "SDK_AVAILABLE", "VERSION_UNAVAILABLE"})

    def test_documented_billing_interfaces_are_normalized_without_raw_response(self):
        adapter = ModalAdapter(network_enabled=True, confirmation="MODAL_VALIDATION")
        adapter._modal_module = FakeModal
        self.assertEqual(adapter.probe_auth()["status"], "AUTH_OK")
        summary = adapter.get_billing_summary("2026-09")
        self.assertEqual(summary["status"], "BILLING_OK")
        self.assertEqual(summary["official_metered_cost"], "1.25")
        rates = adapter.get_billing_rates()
        self.assertEqual(rates["status"], "RATES_OK")
        report = adapter.get_billing_report("start", "end")
        self.assertEqual(report["status"], "BILLING_REPORT_OK")
        self.assertNotIn("resource", str(summary))

    def test_error_normalization_is_bounded(self):
        class Error:
            status_code = 429
            text = "secret provider response"

        normalized = normalize_error(Error())
        self.assertEqual(normalized.error_class, "RATE_LIMITED")
        self.assertFalse(normalized.retryable)
        self.assertNotIn("secret provider response", str(normalized))

    def test_compute_requires_separate_confirmation_and_cpu_executor(self):
        adapter = ModalAdapter(network_enabled=True, confirmation="MODAL_VALIDATION")
        with self.assertRaises(ModalAdapterError) as caught:
            adapter.execute_guarded(None, None, None, lambda: None)
        self.assertEqual(caught.exception.error_class, "COMPUTE_DISABLED")

    def test_guarded_cpu_executor_settles_without_retry(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            adapter = ModalAdapter(
                network_enabled=True,
                confirmation="MODAL_VALIDATION",
                compute_enabled=True,
                compute_confirmation="MODAL_COMPUTE_VALIDATION",
            )
            guard = ModalCostGuard(Path(directory) / "ledger.json", shared_store_ready=True, allow_initialize=True)
            billing = ModalBillingSnapshot("2026-09", Decimal("0"), Decimal("10"), Decimal("10"), True, True)
            job = ModalJobSpec("mission", "job", "key", "0.5")
            called = []
            result = adapter.execute_guarded(guard, billing, job, lambda: called.append(True) or {"observed_cost": "0.1"})
            self.assertEqual(result["status"], "COMPLETED")
            self.assertEqual(called, [True])
            self.assertEqual(guard.summary()["local_unsettled_usage"], "0.5")

    def test_timeout_keeps_one_unsettled_record_and_replay_does_not_execute_again(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            adapter = ModalAdapter(
                network_enabled=True,
                confirmation="MODAL_VALIDATION",
                compute_enabled=True,
                compute_confirmation="MODAL_COMPUTE_VALIDATION",
            )
            guard = ModalCostGuard(Path(directory) / "ledger.json", shared_store_ready=True, allow_initialize=True)
            billing = ModalBillingSnapshot("2026-09", Decimal("0"), Decimal("10"), Decimal("10"), True, True)
            job = ModalJobSpec("mission", "job", "key", "0.5")
            calls = []

            def timeout():
                calls.append(True)
                raise TimeoutError("provider body must not be recorded")

            with self.assertRaises(ModalAdapterError) as caught:
                adapter.execute_guarded(guard, billing, job, timeout)
            self.assertEqual(caught.exception.error_class, "NETWORK_TIMEOUT")
            self.assertEqual(len(calls), 1)
            replay = adapter.execute_guarded(guard, billing, ModalJobSpec("mission", "retry-job", "key", "0.5"), lambda: calls.append(True))
            self.assertEqual(replay["status"], "REPLAY")
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
