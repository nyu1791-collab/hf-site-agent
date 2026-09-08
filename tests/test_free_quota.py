import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.free_quota import FreeQuotaBlocked, FreeUsageLedger, classify_zone


class FreeQuotaTests(unittest.TestCase):
    def test_zones_leave_emergency_reserve(self):
        self.assertEqual(classify_zone(0), "GREEN")
        self.assertEqual(classify_zone(799), "GREEN")
        self.assertEqual(classify_zone(800), "YELLOW")
        self.assertEqual(classify_zone(850), "ORANGE")
        self.assertEqual(classify_zone(899), "ORANGE")
        self.assertEqual(classify_zone(900), "RED")

    def test_reservation_and_pre_send_ledger_are_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = FreeUsageLedger(Path(directory) / "ledger.json")
            reservation = ledger.reserve("MISSION-1", 2)
            self.assertEqual(reservation["remaining"], 2)
            first = ledger.before_request(
                request_id="REQ-1",
                mission_id="MISSION-1",
                agent_id="glm-general-commander",
                model="example/model:free",
            )
            self.assertTrue(first["allowed"])
            duplicate = ledger.before_request(
                request_id="REQ-1",
                mission_id="MISSION-1",
                agent_id="glm-general-commander",
                model="example/model:free",
            )
            self.assertFalse(duplicate["allowed"])
            self.assertEqual(duplicate["reason"], "duplicate_request")
            ledger.record_response("REQ-1", success=False, http_status=500)
            second = ledger.before_request(
                request_id="REQ-2",
                mission_id="MISSION-1",
                agent_id="deepseek-engineering-commander",
                model="example/model:free",
                retry=1,
            )
            self.assertTrue(second["allowed"])
            self.assertEqual(ledger.usage_count(), 2)

    def test_hard_stop_and_429_open_the_breaker_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
            now = lambda: clock[0]
            ledger = FreeUsageLedger(Path(directory) / "ledger.json", now=now, hard_stop=3, daily_cap=4, max_rpm=15)
            ledger.reserve("MISSION-1", 1)
            result = ledger.before_request(
                request_id="REQ-429",
                mission_id="MISSION-1",
                agent_id="worker",
                model="example/model:free",
            )
            self.assertTrue(result["allowed"])
            ledger.mark_429("REQ-429")
            with self.assertRaises(FreeQuotaBlocked) as blocked:
                ledger.before_request(
                    request_id="REQ-2",
                    mission_id="MISSION-1",
                    agent_id="worker",
                    model="example/model:free",
                )
            self.assertEqual(blocked.exception.reason, "free_endpoint_429")

        with tempfile.TemporaryDirectory() as directory:
            ledger = FreeUsageLedger(Path(directory) / "ledger.json", hard_stop=3, daily_cap=4, max_rpm=15)
            ledger.reserve("MISSION-2", 3)
            for index in range(3):
                result = ledger.before_request(
                    request_id=f"REQ-{index}",
                    mission_id="MISSION-2",
                    agent_id="worker",
                    model="example/model:free",
                )
                self.assertTrue(result["allowed"])
            with self.assertRaises(FreeQuotaBlocked) as blocked:
                ledger.before_request(
                    request_id="REQ-3",
                    mission_id="MISSION-2",
                    agent_id="worker",
                    model="example/model:free",
                )
            self.assertEqual(blocked.exception.reason, "free_daily_safety_limit")
            self.assertEqual(ledger.summary()["status"], "PAUSED_FREE_QUOTA")

    def test_shared_rpm_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
            ledger = FreeUsageLedger(Path(directory) / "ledger.json", now=lambda: clock[0], max_rpm=2)
            ledger.reserve("MISSION-1", 2)
            for index in range(2):
                ledger.before_request(
                    request_id=f"REQ-{index}",
                    mission_id="MISSION-1",
                    agent_id="worker",
                    model="example/model:free",
                )
            with self.assertRaises(FreeQuotaBlocked) as blocked:
                ledger.before_request(
                    request_id="REQ-2",
                    mission_id="MISSION-1",
                    agent_id="worker",
                    model="example/model:free",
                )
            self.assertEqual(blocked.exception.status, "QUEUED_FREE_QUOTA")

    def test_next_utc_day_requires_one_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            clock = [datetime(2026, 1, 1, 23, 59, tzinfo=timezone.utc)]
            ledger = FreeUsageLedger(Path(directory) / "ledger.json", now=lambda: clock[0])
            self.assertTrue(ledger.probe_allowed())
            ledger.record_probe(success=False, http_status=429)
            self.assertFalse(ledger.probe_allowed())
            clock[0] = clock[0] + timedelta(minutes=2)
            self.assertTrue(ledger.probe_allowed())
            ledger.record_probe(success=True, http_status=200)
            self.assertFalse(ledger.probe_allowed())
            self.assertEqual(ledger.summary()["circuit_breaker"], "CLOSED")

    def test_non_free_and_generic_router_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = FreeUsageLedger(Path(directory) / "ledger.json")
            with self.assertRaises(FreeQuotaBlocked):
                ledger.before_request(
                    request_id="REQ-PAID",
                    mission_id="MISSION-1",
                    agent_id="worker",
                    model="example/model",
                )
            with self.assertRaises(FreeQuotaBlocked):
                ledger.before_request(
                    request_id="REQ-GENERIC",
                    mission_id="MISSION-1",
                    agent_id="worker",
                    model="openrouter/free",
                )


if __name__ == "__main__":
    unittest.main()
