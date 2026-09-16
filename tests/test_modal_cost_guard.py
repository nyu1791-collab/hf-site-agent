from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from pathlib import Path
import tempfile
import unittest

from scripts.modal_cost_guard import (
    ModalBillingSnapshot,
    ModalCostGuard,
    ModalCostGuardError,
    ModalCostState,
    ModalJobSpec,
    cost_state_for_exposure,
    projected_exposure,
    utc_iso,
)


class ModalCostGuardTests(unittest.TestCase):
    def setUp(self):
        self.billing = ModalBillingSnapshot(
            billing_cycle="2026-09",
            official_metered_cost=Decimal("1.00"),
            usage_limit=Decimal("10.00"),
            credit_remaining=Decimal("9.00"),
            billing_verified=True,
            rates_verified=True,
        )

    @staticmethod
    def job(job_id="job-1", key="idem-1", *, cost="1.00", resource_type="cpu", payload=None, **kwargs):
        return ModalJobSpec(
            mission_id=kwargs.pop("mission_id", "mission-1"),
            job_id=job_id,
            idempotency_key=key,
            estimated_max_cost=cost,
            resource_type=resource_type,
            payload={} if payload is None else payload,
            **kwargs,
        )

    def guard(self, directory):
        return ModalCostGuard(
            Path(directory) / "modal-ledger.json",
            shared_store_ready=True,
            allow_initialize=True,
        )

    def test_decimal_exposure_formula(self):
        self.assertEqual(
            projected_exposure("1.10", "0.20", "0.30", "0.40", "0.05"),
            Decimal("2.05"),
        )

    def test_cost_bands_are_green_caution_soft_stop_hard_stop(self):
        thresholds = {"green": "0.70", "caution": "0.80", "soft_stop": "0.90", "hard_stop": "0.90"}
        self.assertEqual(cost_state_for_exposure("6.9", "10", thresholds), ModalCostState.GREEN)
        self.assertEqual(cost_state_for_exposure("7.0", "10", thresholds), ModalCostState.CAUTION)
        self.assertEqual(cost_state_for_exposure("8.0", "10", thresholds), ModalCostState.SOFT_STOP)
        self.assertEqual(cost_state_for_exposure("8.99", "10", thresholds), ModalCostState.SOFT_STOP)
        self.assertEqual(cost_state_for_exposure("9.0", "10", thresholds), ModalCostState.HARD_STOP)

    def test_unknown_billing_blocks_new_jobs_and_gpu_requires_known_credit(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = self.guard(directory)
            unknown = ModalBillingSnapshot("2026-09", None, Decimal("10"), None, True, True)
            job = self.job()
            result = guard.preflight(unknown, job)
            self.assertEqual(result["MODAL_COST_STATE"], ModalCostState.UNKNOWN)
            self.assertFalse(result["MODAL_NEW_JOBS_ALLOWED"])
            with self.assertRaises(ModalCostGuardError) as caught:
                guard.reserve(unknown, job)
            self.assertEqual(caught.exception.reason, "MODAL_COST_STATE_UNKNOWN")
            gpu_unknown = ModalBillingSnapshot("2026-09", Decimal("1"), Decimal("10"), None, True, True)
            with self.assertRaises(ModalCostGuardError) as caught:
                guard.reserve(gpu_unknown, self.job(job_id="gpu", key="gpu-key", resource_type="gpu", gpu_type="L40S"))
            self.assertEqual(caught.exception.reason, "GPU_BILLING_UNKNOWN")

    def test_missing_shared_ledger_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = ModalCostGuard(Path(directory) / "ledger.json", shared_store_ready=False, allow_initialize=True)
            result = guard.preflight(self.billing, self.job())
            self.assertEqual(result["MODAL_COST_STATE"], ModalCostState.UNKNOWN)
            self.assertFalse(result["MODAL_NEW_JOBS_ALLOWED"])
            with self.assertRaises(ModalCostGuardError) as caught:
                guard.reserve(self.billing, self.job())
            self.assertEqual(caught.exception.reason, "LEDGER_UNAVAILABLE")

    def test_active_reservation_and_credit_exposure_are_included_before_send(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = self.guard(directory)
            guard.reserve(self.billing, self.job(cost="1.00"))
            evaluation = guard.preflight(self.billing, self.job(job_id="next", key="next-key", cost="6.00"))
            self.assertEqual(evaluation["MODAL_COST_STATE"], ModalCostState.SOFT_STOP)
            self.assertFalse(evaluation["MODAL_NEW_JOBS_ALLOWED"])
            tight_credit = ModalBillingSnapshot("2026-09", Decimal("0"), Decimal("100"), Decimal("0.5"), True, True)
            with self.assertRaises(ModalCostGuardError) as caught:
                guard.reserve(tight_credit, self.job(job_id="credit", key="credit-key", cost="1.00"))
            self.assertEqual(caught.exception.reason, "MODAL_COST_HARD_STOP")

    def test_nan_null_negative_and_zero_are_rejected(self):
        for value in ("NaN", "Infinity", "-1", ""):
            with self.assertRaises(ModalCostGuardError):
                ModalBillingSnapshot.from_mapping({
                    "billing_cycle": "2026-09",
                    "official_metered_cost": value,
                    "usage_limit": "10",
                    "credit_remaining": "10",
                    "billing_verified": True,
                    "rates_verified": True,
                })
        for value in (None, "0", "-0.1", "NaN"):
            with self.assertRaises(ModalCostGuardError):
                self.job(cost=value)
        with self.assertRaises(ModalCostGuardError):
            self.job(cpu=0)
        with self.assertRaises(ModalCostGuardError):
            self.job(memory_mb=0)
        with self.assertRaises(ModalCostGuardError):
            self.job(resource_type="gpu", gpu_type=None)

    def test_reservation_is_atomic_and_replayable(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = self.guard(directory)
            first = guard.reserve(self.billing, self.job())
            self.assertEqual(first["status"], "RESERVED")
            replay = guard.reserve(self.billing, self.job(job_id="different-job"))
            self.assertEqual(replay["status"], "REPLAY")
            self.assertEqual(replay["job_id"], "job-1")
            with self.assertRaises(ModalCostGuardError) as caught:
                guard.reserve(self.billing, self.job(job_id="other", payload={"changed": True}))
            self.assertEqual(caught.exception.reason, "IDEMPOTENCY_CONFLICT")
            data = json.loads((Path(directory) / "modal-ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(len(data["entries"]), 1)

    def test_concurrent_reservations_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "modal-ledger.json"
            billing = ModalBillingSnapshot("2026-09", Decimal("0"), Decimal("2"), Decimal("2"), True, True)

            def reserve(index):
                guard = ModalCostGuard(path, shared_store_ready=True, allow_initialize=True)
                try:
                    return guard.reserve(billing, self.job(job_id=f"job-{index}", key=f"key-{index}", cost="1.0"))["status"]
                except ModalCostGuardError as error:
                    return error.reason

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(reserve, (1, 2)))
            self.assertEqual(results.count("RESERVED"), 1)
            self.assertEqual(results.count("MODAL_COST_HARD_STOP"), 1)

    def test_durable_store_proof_is_required_for_production_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            local = self.guard(directory)
            local.reserve(self.billing, self.job())
            unproven = ModalCostGuard(
                Path(directory) / "modal-ledger.json",
                shared_store_ready=True,
                allow_initialize=False,
            )
            status = unproven.ledger_status()
            self.assertFalse(status["ready"])
            self.assertFalse(status["cross_runner_lock_supported"])
            self.assertEqual(status["readiness_reason"], "LEDGER_DURABILITY_UNPROVEN")
            self.assertEqual(unproven.summary()["ledger_status"], "NOT_READY")
            with self.assertRaises(ModalCostGuardError) as caught:
                unproven.reserve(self.billing, self.job(job_id="new-job", key="new-key"))
            self.assertEqual(caught.exception.reason, "LEDGER_DURABILITY_UNPROVEN")

    def test_start_settle_unknown_keeps_unsettled_cost_and_restart_replays(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "modal-ledger.json"
            guard = ModalCostGuard(path, shared_store_ready=True, allow_initialize=True)
            guard.reserve(self.billing, self.job())
            guard.start("job-1", mission_id="mission-1")
            settled = guard.settle("job-1", mission_id="mission-1", success=False)
            self.assertEqual(settled["billing_status"], "UNSETTLED")
            self.assertEqual(guard.summary()["local_unsettled_usage"], "1.00")
            restarted = ModalCostGuard(
                path,
                shared_store_ready=True,
                allow_initialize=False,
                durable_store_proven=True,
            )
            replay = restarted.reserve(self.billing, self.job(job_id="retry-job"))
            self.assertEqual(replay["status"], "REPLAY")

    def test_observed_cost_reconciliation_and_mismatch_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = self.guard(directory)
            guard.reserve(self.billing, self.job(cost="1.00"))
            guard.settle("job-1", mission_id="mission-1", observed_cost="0.25")
            mismatch = guard.reconcile(self.billing, baseline_official_cost="1.00")
            self.assertEqual(mismatch["status"], "RECONCILIATION_PENDING")
            self.assertFalse(mismatch["MODAL_NEW_JOBS_ALLOWED"])
            official = ModalBillingSnapshot("2026-09", Decimal("1.25"), Decimal("10"), Decimal("8.75"), True, True)
            reconciled = guard.reconcile(official, baseline_official_cost="1.00")
            self.assertEqual(reconciled["status"], "RECONCILED")
            self.assertTrue(reconciled["MODAL_NEW_JOBS_ALLOWED"])
            self.assertEqual(guard.summary()["local_unsettled_usage"], "0")

    def test_cancellation_is_job_and_mission_scoped_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = self.guard(directory)
            mission_a = self.job(job_id="a", key="a-key", mission_id="mission-a")
            mission_b = self.job(job_id="b", key="b-key", mission_id="mission-b")
            guard.reserve(self.billing, mission_a)
            guard.reserve(self.billing, mission_b)
            self.assertEqual(guard.cancel("a", mission_id="mission-a")["status"], "CANCELLED")
            self.assertEqual(guard.cancel("a", mission_id="mission-a")["status"], "CANCELLED")
            data = json.loads((Path(directory) / "modal-ledger.json").read_text(encoding="utf-8"))
            entries = {record["job_id"]: record for record in data["entries"]}
            self.assertEqual(entries["a"]["status"], "CANCELLED")
            self.assertEqual(entries["b"]["status"], "RESERVED")
            self.assertEqual(data["reservations"]["b"]["status"], "RESERVED")

    def test_circuit_open_blocks_only_modal_and_no_retry_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = self.guard(directory)
            guard.open_circuit("RATE_LIMITED")
            with self.assertRaises(ModalCostGuardError) as caught:
                guard.reserve(self.billing, self.job())
            self.assertEqual(caught.exception.reason, "CIRCUIT_NOT_CLOSED")
            guard.half_open_circuit()
            with self.assertRaises(ModalCostGuardError):
                guard.reserve(self.billing, self.job())
            guard.close_circuit()
            result = guard.reserve(self.billing, self.job())
            self.assertEqual(result["record"]["retries"], 0)

    def test_provider_error_opens_only_modal_circuit(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = self.guard(directory)
            result = guard.record_provider_error("TEMPORARY_PROVIDER_ERROR")
            self.assertTrue(result["opened"])
            self.assertEqual(result["circuit_state"], "OPEN")
            self.assertFalse(guard.record_provider_error("MODEL_OUTPUT_INVALID")["opened"])

    def test_invalid_persisted_ledger_and_timezone_cycle_fail_closed(self):
        self.assertTrue(utc_iso().endswith("Z"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            path.write_text('{"schema_version":"wrong","provider_id":"modal"}\n', encoding="utf-8")
            guard = ModalCostGuard(path, shared_store_ready=True, allow_initialize=False)
            self.assertEqual(guard.summary()["MODAL_COST_STATE"], ModalCostState.UNKNOWN)
            with self.assertRaises(ModalCostGuardError) as caught:
                guard.reserve(self.billing, self.job())
            self.assertEqual(caught.exception.reason, "LEDGER_INVALID")


if __name__ == "__main__":
    unittest.main()
