from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from scripts.batch_media_scheduler import BatchPolicy, FileLeaseManager, LeaseBusyError, MediaJob, MediaJobFailure, ResourceVector, StaleWriteError, admit_wave, atomic_compare_and_swap_json, effective_parallelism, file_sha256_or_empty, run_batch, run_batch_with_leases


def job(n: int, *, rights: bool = True, demand: ResourceVector | None = None) -> MediaJob:
    digest = hashlib.sha256(f"input-{n}".encode()).hexdigest()
    return MediaJob(job_id=f"job-{n}", input_ref=f"input-{n}.mp4", input_sha256=digest, rights_verified=rights, demand=demand or ResourceVector(cpu_slots=1, memory_mb=256, disk_mb=256, provider_slots=0))


class BatchMediaSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.policy = BatchPolicy(max_parallel_jobs=3, degraded_parallel_jobs=1, max_transient_retries=2)
        self.capacity = ResourceVector(cpu_slots=3, memory_mb=2048, disk_mb=2048, provider_slots=3)

    def test_backpressure_parallelism(self):
        self.assertEqual(effective_parallelism(self.policy, "NORMAL", self.capacity), 3)
        self.assertEqual(effective_parallelism(self.policy, "DEGRADED", self.capacity), 1)
        self.assertEqual(effective_parallelism(self.policy, "PAUSED", self.capacity), 0)

    def test_three_admitted_fourth_queued(self):
        admitted, queued, blocked = admit_wave([job(1), job(2), job(3), job(4)], policy=self.policy, state="NORMAL", capacity=self.capacity)
        self.assertEqual([j.job_id for j in admitted], ["job-1", "job-2", "job-3"])
        self.assertEqual([j.job_id for j in queued], ["job-4"])
        self.assertEqual(blocked, [])

    def test_rights_block_isolated(self):
        admitted, queued, blocked = admit_wave([job(1, rights=False), job(2)], policy=self.policy, state="NORMAL", capacity=self.capacity)
        self.assertEqual([j.job_id for j in admitted], ["job-2"])
        self.assertEqual(queued, [])
        self.assertEqual(blocked[0].failure_class, "RIGHTS_BLOCK")

    def test_resource_shortage_does_not_partial_start(self):
        huge = job(1, demand=ResourceVector(cpu_slots=4, memory_mb=256, disk_mb=256, provider_slots=0))
        admitted, queued, blocked = admit_wave([huge], policy=self.policy, state="NORMAL", capacity=self.capacity)
        self.assertEqual(admitted, [])
        self.assertEqual([j.job_id for j in queued], ["job-1"])
        self.assertEqual(blocked, [])
        result = run_batch([huge], lambda _: "should-not-run", policy=self.policy, capacity=self.capacity)
        self.assertEqual(result[0].status, "BLOCKED_RESOURCE")
        self.assertEqual(result[0].attempts, 0)

    def test_run_batch_never_exceeds_three_and_processes_fourth_in_next_wave(self):
        active = 0
        peak = 0
        lock = threading.Lock()
        starts: list[str] = []

        def handler(j: MediaJob):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                starts.append(j.job_id)
            time.sleep(0.03)
            with lock:
                active -= 1
            return j.job_id

        results = run_batch([job(1), job(2), job(3), job(4)], handler, policy=self.policy, capacity=self.capacity)
        self.assertEqual(peak, 3)
        self.assertEqual([r.status for r in results], ["READY"] * 4)
        self.assertEqual(set(starts), {"job-1", "job-2", "job-3", "job-4"})

    def test_transient_retry_is_bounded_and_deterministic_error_is_not_retried(self):
        transient_calls = 0
        deterministic_calls = 0

        def transient(_: MediaJob):
            nonlocal transient_calls
            transient_calls += 1
            if transient_calls < 3:
                raise MediaJobFailure("TRANSIENT_PROVIDER", "temporary")
            return "ok"

        out = run_batch([job(1)], transient, policy=self.policy, capacity=self.capacity)
        self.assertEqual(out[0].status, "READY")
        self.assertEqual(out[0].attempts, 3)

        def deterministic(_: MediaJob):
            nonlocal deterministic_calls
            deterministic_calls += 1
            raise MediaJobFailure("DETERMINISTIC_MEDIA", "bad input")

        out = run_batch([job(2)], deterministic, policy=self.policy, capacity=self.capacity)
        self.assertEqual(out[0].status, "FAILED")
        self.assertEqual(out[0].attempts, 1)
        self.assertEqual(deterministic_calls, 1)

    def test_file_lease_exclusive_and_token_bound_release(self):
        with tempfile.TemporaryDirectory() as td:
            mgr = FileLeaseManager(Path(td), ttl_seconds=60)
            token = mgr.acquire("job-1", "runner-a")
            with self.assertRaises(LeaseBusyError):
                mgr.acquire("job-1", "runner-b")
            self.assertFalse(mgr.release("job-1", "wrong-token"))
            self.assertTrue(mgr.release("job-1", token))
            self.assertTrue(mgr.acquire("job-1", "runner-b"))

    def test_mutating_path_acquires_and_releases_lease(self):
        with tempfile.TemporaryDirectory() as td:
            mgr = FileLeaseManager(Path(td), ttl_seconds=60)
            seen_lease = []

            def handler(j: MediaJob):
                seen_lease.append(mgr._path(j.job_id).exists())
                return "ok"

            out = run_batch_with_leases([job(1)], handler, lease_manager=mgr, policy=self.policy, capacity=self.capacity)
            self.assertEqual(out[0].status, "READY")
            self.assertEqual(seen_lease, [True])
            self.assertFalse(mgr._path("job-1").exists())
            token = mgr.acquire("job-1", "after-success")
            self.assertTrue(mgr.release("job-1", token))

    def test_mutating_path_releases_lease_after_handler_failure(self):
        with tempfile.TemporaryDirectory() as td:
            mgr = FileLeaseManager(Path(td), ttl_seconds=60)

            def handler(_: MediaJob):
                raise MediaJobFailure("DETERMINISTIC_MEDIA", "bad media")

            out = run_batch_with_leases([job(1)], handler, lease_manager=mgr, policy=self.policy, capacity=self.capacity)
            self.assertEqual(out[0].status, "FAILED")
            self.assertEqual(out[0].failure_class, "DETERMINISTIC_MEDIA")
            self.assertFalse(mgr._path("job-1").exists())

    def test_mutating_path_blocks_when_external_lease_is_held(self):
        with tempfile.TemporaryDirectory() as td:
            mgr = FileLeaseManager(Path(td), ttl_seconds=60)
            token = mgr.acquire("job-1", "external-owner")
            calls = 0

            def handler(_: MediaJob):
                nonlocal calls
                calls += 1
                return "should-not-run"

            out = run_batch_with_leases([job(1)], handler, lease_manager=mgr, policy=self.policy, capacity=self.capacity)
            self.assertEqual(out[0].status, "FAILED")
            self.assertEqual(out[0].failure_class, "STALE_WRITE")
            self.assertEqual(calls, 0)
            self.assertTrue(mgr.release("job-1", token))

    def test_compare_and_swap_rejects_stale_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "manifest.json"
            empty_hash = file_sha256_or_empty(path)
            first_hash = atomic_compare_and_swap_json(path, expected_sha256=empty_hash, payload={"version": 1})
            self.assertEqual(first_hash, file_sha256_or_empty(path))
            with self.assertRaises(StaleWriteError):
                atomic_compare_and_swap_json(path, expected_sha256=empty_hash, payload={"version": 2})
            self.assertEqual(json.loads(path.read_text())["version"], 1)

    def test_generator_input_preserves_result_order(self):
        jobs = (job(n) for n in range(1, 5))
        results = run_batch(jobs, lambda j: j.job_id, policy=self.policy, capacity=self.capacity)
        self.assertEqual([r.job_id for r in results], ["job-1", "job-2", "job-3", "job-4"])

    def test_ai_free_handler_is_valid_execution_path(self):
        results = run_batch([job(1), job(2)], lambda j: {"output": f"{j.job_id}.mp4", "ai_calls": 0}, policy=self.policy, capacity=self.capacity)
        self.assertTrue(all(r.status == "READY" for r in results))
        self.assertTrue(all(r.result["ai_calls"] == 0 for r in results))


if __name__ == "__main__":
    unittest.main()
