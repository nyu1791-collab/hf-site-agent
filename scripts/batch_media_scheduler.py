#!/usr/bin/env python3
"""Bounded job-level scheduler for independent media jobs.

The scheduler supplies concurrency, resource admission, per-job leases,
stale-write protection, and failure-class retry bounds. It deliberately does
not implement a media editor itself: clip/FFmpeg handlers plug in below this
layer and retain the canonical four-stage clipping and long-form contracts.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

BACKPRESSURE_STATES = {"NORMAL", "DEGRADED", "PAUSED"}
RETRYABLE_FAILURES = {"TRANSIENT_PROVIDER"}


class MediaJobFailure(RuntimeError):
    def __init__(self, failure_class: str, message: str):
        super().__init__(message)
        self.failure_class = str(failure_class)


class StaleWriteError(MediaJobFailure):
    def __init__(self, message: str = "manifest base hash changed"):
        super().__init__("STALE_WRITE", message)


class LeaseBusyError(MediaJobFailure):
    def __init__(self, message: str = "job lease is held by another owner"):
        super().__init__("STALE_WRITE", message)


@dataclass(frozen=True)
class ResourceVector:
    cpu_slots: int = 1
    memory_mb: int = 512
    disk_mb: int = 512
    provider_slots: int = 0

    def validate(self) -> "ResourceVector":
        for name, value in self.__dict__.items():
            if int(value) < 0:
                raise ValueError(f"{name} must be non-negative")
        return self

    def fits(self, other: "ResourceVector") -> bool:
        return all(getattr(self, k) <= getattr(other, k) for k in self.__dict__)

    def subtract(self, other: "ResourceVector") -> "ResourceVector":
        return ResourceVector(**{k: getattr(self, k) - getattr(other, k) for k in self.__dict__})


@dataclass(frozen=True)
class MediaJob:
    job_id: str
    input_ref: str
    input_sha256: str
    rights_verified: bool
    demand: ResourceVector = field(default_factory=ResourceVector)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> "MediaJob":
        if not self.job_id or any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for ch in self.job_id):
            raise ValueError("invalid job_id")
        if len(self.input_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in self.input_sha256):
            raise ValueError("input_sha256 must be lowercase sha256")
        self.demand.validate()
        return self


@dataclass(frozen=True)
class BatchPolicy:
    max_parallel_jobs: int = 3
    degraded_parallel_jobs: int = 1
    max_transient_retries: int = 2

    def validate(self) -> "BatchPolicy":
        if not 1 <= self.max_parallel_jobs <= 3:
            raise ValueError("max_parallel_jobs must be 1..3")
        if not 0 <= self.degraded_parallel_jobs <= self.max_parallel_jobs:
            raise ValueError("degraded_parallel_jobs out of range")
        if not 0 <= self.max_transient_retries <= 8:
            raise ValueError("max_transient_retries out of range")
        return self


@dataclass
class JobResult:
    job_id: str
    status: str
    attempts: int
    result: Any = None
    failure_class: str | None = None
    detail: str | None = None


class FileLeaseManager:
    """Small file-backed exclusive lease suitable for one runner workspace."""

    def __init__(self, root: Path, ttl_seconds: int = 300):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = int(ttl_seconds)
        if self.ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")

    def _path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.lease.json"

    def acquire(self, job_id: str, owner: str) -> str:
        path = self._path(job_id)
        now = time.time()
        token = uuid.uuid4().hex
        payload = {"job_id": job_id, "owner": owner, "token": token, "created_at": now, "expires_at": now + self.ttl_seconds}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        for _ in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(encoded)
                    fh.flush()
                    os.fsync(fh.fileno())
                return token
            except FileExistsError:
                try:
                    existing = json.loads(path.read_text(encoding="utf-8"))
                    expired = float(existing.get("expires_at") or 0) <= now
                except Exception:
                    expired = False
                if not expired:
                    raise LeaseBusyError()
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        raise LeaseBusyError("could not acquire expired lease safely")

    def release(self, job_id: str, token: str) -> bool:
        path = self._path(job_id)
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False
        if existing.get("token") != token:
            return False
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False


def effective_parallelism(policy: BatchPolicy, state: str, capacity: ResourceVector) -> int:
    policy.validate()
    capacity.validate()
    normalized = str(state).upper()
    if normalized not in BACKPRESSURE_STATES:
        raise ValueError(f"unknown backpressure state: {state}")
    if normalized == "PAUSED":
        return 0
    ceiling = policy.max_parallel_jobs if normalized == "NORMAL" else policy.degraded_parallel_jobs
    return max(0, min(ceiling, int(capacity.cpu_slots)))


def admit_wave(jobs: Iterable[MediaJob], *, policy: BatchPolicy, state: str, capacity: ResourceVector) -> tuple[list[MediaJob], list[MediaJob], list[JobResult]]:
    """Admit one bounded wave; return admitted, queued, and immediately blocked."""
    limit = effective_parallelism(policy, state, capacity)
    remaining = capacity
    admitted: list[MediaJob] = []
    queued: list[MediaJob] = []
    blocked: list[JobResult] = []
    for job in jobs:
        try:
            job.validate()
        except Exception as exc:
            blocked.append(JobResult(job_id=getattr(job, "job_id", "unknown"), status="FAILED", attempts=0, failure_class="INVALID_JOB", detail=str(exc)))
            continue
        if not job.rights_verified:
            blocked.append(JobResult(job_id=job.job_id, status="BLOCKED_RIGHTS", attempts=0, failure_class="RIGHTS_BLOCK", detail="rights_verified=false"))
            continue
        if len(admitted) >= limit:
            queued.append(job)
            continue
        if not job.demand.fits(remaining):
            queued.append(job)
            continue
        admitted.append(job)
        remaining = remaining.subtract(job.demand)
    return admitted, queued, blocked


def _run_one(job: MediaJob, handler: Callable[[MediaJob], Any], *, max_transient_retries: int) -> JobResult:
    attempt = 0
    while True:
        attempt += 1
        try:
            return JobResult(job_id=job.job_id, status="READY", attempts=attempt, result=handler(job))
        except MediaJobFailure as exc:
            if exc.failure_class in RETRYABLE_FAILURES and attempt <= max_transient_retries:
                continue
            return JobResult(job_id=job.job_id, status="FAILED", attempts=attempt, failure_class=exc.failure_class, detail=str(exc))
        except Exception as exc:
            return JobResult(job_id=job.job_id, status="FAILED", attempts=attempt, failure_class="DETERMINISTIC_MEDIA", detail=str(exc))


def run_batch(jobs: Iterable[MediaJob], handler: Callable[[MediaJob], Any], *, policy: BatchPolicy | None = None, state: str = "NORMAL", capacity: ResourceVector | None = None) -> list[JobResult]:
    """Run independent jobs in bounded waves; later jobs queue rather than overspawn."""
    policy = (policy or BatchPolicy()).validate()
    capacity = (capacity or ResourceVector(cpu_slots=3, memory_mb=6144, disk_mb=12288, provider_slots=3)).validate()
    job_list = list(jobs)
    pending = list(job_list)
    results: list[JobResult] = []
    seen: set[str] = set()
    for job in job_list:
        if job.job_id in seen:
            raise ValueError(f"duplicate job_id: {job.job_id}")
        seen.add(job.job_id)

    while pending:
        admitted, queued, blocked = admit_wave(pending, policy=policy, state=state, capacity=capacity)
        results.extend(blocked)
        if not admitted:
            failure = "RESOURCE_EXHAUSTED" if state != "PAUSED" else "CANCELLED"
            status = "BLOCKED_RESOURCE" if state != "PAUSED" else "CANCELLED"
            results.extend(JobResult(job_id=j.job_id, status=status, attempts=0, failure_class=failure, detail="no admission capacity") for j in queued)
            break
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(admitted)) as pool:
            future_by_id = {pool.submit(_run_one, job, handler, max_transient_retries=policy.max_transient_retries): job.job_id for job in admitted}
            for future in concurrent.futures.as_completed(future_by_id):
                results.append(future.result())
        pending = queued

    order = {job.job_id: i for i, job in enumerate(job_list)}
    results.sort(key=lambda item: order.get(item.job_id, 10**9))
    return results


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256_or_empty(path: Path) -> str:
    try:
        return _sha256_bytes(Path(path).read_bytes())
    except FileNotFoundError:
        return _sha256_bytes(b"")


def atomic_compare_and_swap_json(path: Path, *, expected_sha256: str, payload: Mapping[str, Any]) -> str:
    """Atomically replace one manifest only when its base hash still matches.

    Callers must hold the job-scoped lease while committing. The second hash
    check rejects a stale writer that lost the race after preparing its temp file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    current_hash = file_sha256_or_empty(path)
    if current_hash != expected_sha256:
        raise StaleWriteError(f"expected={expected_sha256} actual={current_hash}")
    encoded = (json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        if file_sha256_or_empty(path) != expected_sha256:
            raise StaleWriteError("manifest changed during CAS commit")
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass
    return _sha256_bytes(encoded)


__all__ = ["BatchPolicy", "FileLeaseManager", "JobResult", "LeaseBusyError", "MediaJob", "MediaJobFailure", "ResourceVector", "StaleWriteError", "admit_wave", "atomic_compare_and_swap_json", "effective_parallelism", "file_sha256_or_empty", "run_batch"]
