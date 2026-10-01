#!/usr/bin/env python3
"""Durable, speed-first queue for deterministic media execution.

The runner is intentionally thin: it persists typed jobs in SQLite, leases one
job at a time, heartbeats while an existing repository handler runs, and stops
at READY_TO_PUBLISH. It never accepts shell commands and never publishes.
"""
from __future__ import annotations

import argparse
import fcntl
from contextlib import contextmanager
import hashlib
import json
import os
import secrets
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "durable-media-runner-v1"
JOB_KIND_MEDIA_BATCH = "MEDIA_BATCH_RUN"
ALLOWED_JOB_KINDS = {JOB_KIND_MEDIA_BATCH}
SECRET_KEY_FRAGMENTS = (
    "api_key", "apikey", "token", "secret", "password", "passwd",
    "authorization", "cookie", "credential", "private_key",
)
FORBIDDEN_CONTROL_KEYS = {"command", "cmd", "argv", "shell", "executable", "binary"}
SAFE_ENV_KEYS = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP"}


class DurableRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClaimedJob:
    job_id: int
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    lease_token: str


def _now() -> float:
    return time.time()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _token_hash(token: str) -> str:
    return _sha256_text(token)


def _safe_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key in SAFE_ENV_KEYS}


def _reject_unsafe_keys(value: Any, path: str = "payload") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key).strip().lower()
            if key in FORBIDDEN_CONTROL_KEYS:
                raise DurableRunnerError(f"{path}.{raw_key}: executable control fields are forbidden")
            if any(fragment in key for fragment in SECRET_KEY_FRAGMENTS):
                raise DurableRunnerError(f"{path}.{raw_key}: secret-bearing fields are forbidden")
            _reject_unsafe_keys(item, f"{path}.{raw_key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_unsafe_keys(item, f"{path}[{index}]")


def _within(root: Path, candidate: Path) -> Path:
    root = root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise DurableRunnerError(f"path escapes workspace: {candidate}") from exc
    return resolved


def _workspace_path(workspace: Path, raw: Any, *, must_exist: bool = False) -> Path:
    text = str(raw or "").strip()
    if not text:
        raise DurableRunnerError("required workspace path is empty")
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = _within(workspace, candidate)
    if must_exist and not resolved.exists():
        raise DurableRunnerError(f"required path does not exist: {resolved}")
    return resolved


def _validate_manifest_paths(workspace: Path, manifest_path: Path) -> None:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DurableRunnerError(f"invalid media manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise DurableRunnerError("media manifest must be a JSON object")
    _reject_unsafe_keys(manifest, "manifest")
    jobs = manifest.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise DurableRunnerError("media manifest jobs must be a non-empty array")
    if len(jobs) > 10:
        raise DurableRunnerError("media manifest exceeds 10-job request cap")
    for index, row in enumerate(jobs):
        if not isinstance(row, dict):
            raise DurableRunnerError(f"manifest.jobs[{index}] must be an object")
        _workspace_path(workspace, manifest_path.parent / str(row.get("input_path") or ""), must_exist=True)
    output_dir = manifest.get("output_dir")
    if output_dir:
        _workspace_path(workspace, manifest_path.parent / str(output_dir))



def _snapshot_manifest(workspace: Path, manifest_path: Path, output_override: Any = None) -> tuple[Path, str, Path]:
    """Freeze the exact manifest and workspace paths consumed by the renderer."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DurableRunnerError("cannot snapshot invalid media manifest") from exc
    if not isinstance(manifest, dict):
        raise DurableRunnerError("media manifest must be a JSON object")
    jobs = manifest.get("jobs")
    if not isinstance(jobs, list) or not jobs or len(jobs) > 10:
        raise DurableRunnerError("media manifest jobs must contain 1..10 entries")
    for index, row in enumerate(jobs):
        if not isinstance(row, dict):
            raise DurableRunnerError(f"manifest.jobs[{index}] must be an object")
        if row.get("rights_verified") is not True:
            raise DurableRunnerError(f"manifest.jobs[{index}] rights must be verified before enqueue")
        source = _workspace_path(workspace, manifest_path.parent / str(row.get("input_path") or ""), must_exist=True)
        row["input_path"] = str(source)
        name = str(row.get("output_name") or "")
        if not name or Path(name).name != name or not name.lower().endswith(".mp4"):
            raise DurableRunnerError(f"manifest.jobs[{index}].output_name is invalid")
    raw_output = output_override or manifest.get("output_dir") or "artifacts/media-batch-command-center"
    output = _workspace_path(workspace, manifest_path.parent / str(raw_output))
    manifest["output_dir"] = str(output)
    encoded = (_canonical_json(manifest) + "\n").encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    cache = _workspace_path(workspace, ".durable-manifests")
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    snapshot = _workspace_path(workspace, cache / f"{digest}.json")
    if snapshot.exists():
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != digest:
            raise DurableRunnerError("manifest snapshot cache integrity failure")
    else:
        fd, temp_name = tempfile.mkstemp(prefix=".manifest-", dir=cache)
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, 0o600)
            try:
                os.link(temp, snapshot)
            except FileExistsError:
                if hashlib.sha256(snapshot.read_bytes()).hexdigest() != digest:
                    raise DurableRunnerError("manifest snapshot cache integrity failure")
        finally:
            temp.unlink(missing_ok=True)
    return snapshot, digest, output


def validate_payload(kind: str, payload: Mapping[str, Any], workspace: Path) -> dict[str, Any]:
    if kind not in ALLOWED_JOB_KINDS:
        raise DurableRunnerError(f"unsupported job kind: {kind}")
    if not isinstance(payload, Mapping):
        raise DurableRunnerError("payload must be an object")
    data = dict(payload)
    _reject_unsafe_keys(data)
    if kind == JOB_KIND_MEDIA_BATCH:
        allowed = {"manifest_path", "output_dir", "max_parallel", "state", "manifest_sha256"}
        extra = sorted(set(data) - allowed)
        if extra:
            raise DurableRunnerError(f"unsupported payload keys: {extra}")
        manifest = _workspace_path(workspace, data.get("manifest_path"), must_exist=True)
        digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        if data.get("manifest_sha256") and data["manifest_sha256"] != digest:
            raise DurableRunnerError("manifest changed after enqueue")
        _validate_manifest_paths(workspace, manifest)
        max_parallel = int(data.get("max_parallel", 3))
        if not 1 <= max_parallel <= 3:
            raise DurableRunnerError("max_parallel must be 1..3")
        state = str(data.get("state") or "NORMAL").upper()
        if state not in {"NORMAL", "DEGRADED"}:
            raise DurableRunnerError("state must be NORMAL or DEGRADED")
        normalized: dict[str, Any] = {
            "manifest_path": str(manifest.relative_to(workspace.resolve())),
            "manifest_sha256": digest,
            "max_parallel": max_parallel,
            "state": state,
        }
        if data.get("output_dir"):
            output_dir = _workspace_path(workspace, data["output_dir"])
            normalized["output_dir"] = str(output_dir.relative_to(workspace.resolve()))
        return normalized
    raise DurableRunnerError(f"no validator for job kind: {kind}")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path = db_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    existed = db_path.exists()
    conn = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA trusted_schema=OFF")
    if not existed:
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            dedupe_key TEXT NOT NULL UNIQUE,
            source_id TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            state TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 2,
            available_at REAL NOT NULL,
            lease_token_hash TEXT,
            lease_expires_at REAL,
            last_heartbeat_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            last_error TEXT,
            result_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_claim
        ON jobs(state, available_at, priority DESC, id ASC);
        """
    )


def enqueue_job(
    conn: sqlite3.Connection,
    *,
    workspace: Path,
    kind: str,
    source_id: str,
    payload: Mapping[str, Any],
    priority: int = 0,
    max_attempts: int = 2,
) -> dict[str, Any]:
    normalized = validate_payload(kind, payload, workspace)
    if kind == JOB_KIND_MEDIA_BATCH:
        original = _workspace_path(workspace, normalized["manifest_path"], must_exist=True)
        snapshot, digest, output = _snapshot_manifest(
            workspace, original, _workspace_path(workspace, normalized["output_dir"]) if normalized.get("output_dir") else None
        )
        normalized["manifest_path"] = str(snapshot.relative_to(workspace.resolve()))
        normalized["manifest_sha256"] = digest
        normalized["output_dir"] = str(output.relative_to(workspace.resolve()))
    source_id = str(source_id or "").strip()
    if not source_id or len(source_id) > 240:
        raise DurableRunnerError("source_id must be 1..240 characters")
    if not 1 <= int(max_attempts) <= 5:
        raise DurableRunnerError("max_attempts must be 1..5")
    dedupe_key = _sha256_text(_canonical_json({"kind": kind, "source_id": source_id, "payload": normalized}))
    now = _now()
    try:
        cur = conn.execute(
            """INSERT INTO jobs(
                kind,dedupe_key,source_id,payload_json,state,priority,attempts,max_attempts,
                available_at,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                kind, dedupe_key, source_id, _canonical_json(normalized), "PENDING",
                int(priority), 0, int(max_attempts), now, now, now,
            ),
        )
        return {"status": "ENQUEUED", "job_id": int(cur.lastrowid), "dedupe_key": dedupe_key}
    except sqlite3.IntegrityError:
        row = conn.execute("SELECT id,state FROM jobs WHERE dedupe_key=?", (dedupe_key,)).fetchone()
        return {"status": "DEDUPED", "job_id": int(row["id"]), "state": row["state"], "dedupe_key": dedupe_key}


def recover_expired(conn: sqlite3.Connection, *, now: float | None = None) -> int:
    now = _now() if now is None else float(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            "SELECT id,attempts,max_attempts FROM jobs WHERE state='RUNNING' AND lease_expires_at IS NOT NULL AND lease_expires_at<=?",
            (now,),
        ).fetchall()
        for row in rows:
            retry = int(row["attempts"]) < int(row["max_attempts"])
            conn.execute(
                """UPDATE jobs SET state=?, available_at=?, lease_token_hash=NULL,
                lease_expires_at=NULL,last_heartbeat_at=NULL,updated_at=?,last_error=? WHERE id=?""",
                (
                    "PENDING" if retry else "FAILED_PERMANENT",
                    now,
                    now,
                    "LEASE_EXPIRED_RECOVERED" if retry else "LEASE_EXPIRED_MAX_ATTEMPTS",
                    int(row["id"]),
                ),
            )
        conn.execute("COMMIT")
        return len(rows)
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def claim_job(conn: sqlite3.Connection, *, lease_seconds: int = 120) -> ClaimedJob | None:
    now = _now()
    recover_expired(conn, now=now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            """SELECT * FROM jobs WHERE state='PENDING' AND available_at<=?
            ORDER BY priority DESC,id ASC LIMIT 1""",
            (now,),
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        token = secrets.token_urlsafe(32)
        expires = now + max(30, int(lease_seconds))
        updated = conn.execute(
            """UPDATE jobs SET state='RUNNING',attempts=attempts+1,lease_token_hash=?,
            lease_expires_at=?,last_heartbeat_at=?,updated_at=?,last_error=NULL
            WHERE id=? AND state='PENDING'""",
            (_token_hash(token), expires, now, now, int(row["id"])),
        )
        if updated.rowcount != 1:
            conn.execute("ROLLBACK")
            return None
        conn.execute("COMMIT")
        return ClaimedJob(
            job_id=int(row["id"]),
            kind=str(row["kind"]),
            payload=json.loads(str(row["payload_json"])),
            attempts=int(row["attempts"]) + 1,
            max_attempts=int(row["max_attempts"]),
            lease_token=token,
        )
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise


def heartbeat(conn: sqlite3.Connection, job: ClaimedJob, *, lease_seconds: int) -> bool:
    now = _now()
    cur = conn.execute(
        """UPDATE jobs SET lease_expires_at=?,last_heartbeat_at=?,updated_at=?
        WHERE id=? AND state='RUNNING' AND lease_token_hash=? AND lease_expires_at>?""",
        (now + max(30, int(lease_seconds)), now, now, job.job_id, _token_hash(job.lease_token), now),
    )
    return cur.rowcount == 1


def _complete(conn: sqlite3.Connection, job: ClaimedJob, result: Mapping[str, Any]) -> None:
    now = _now()
    cur = conn.execute(
        """UPDATE jobs SET state='READY_TO_PUBLISH',result_json=?,lease_token_hash=NULL,
        lease_expires_at=NULL,last_heartbeat_at=NULL,updated_at=?
        WHERE id=? AND state='RUNNING' AND lease_token_hash=? AND lease_expires_at>?""",
        (_canonical_json(dict(result)), now, job.job_id, _token_hash(job.lease_token), now),
    )
    if cur.rowcount != 1:
        raise DurableRunnerError("lease lost before completion commit")


def _fail(conn: sqlite3.Connection, job: ClaimedJob, message: str, *, retryable: bool) -> str:
    now = _now()
    retry = bool(retryable and job.attempts < job.max_attempts)
    backoff = min(30.0, 0.5 * (2 ** max(0, job.attempts - 1)))
    cur = conn.execute(
        """UPDATE jobs SET state=?,available_at=?,last_error=?,lease_token_hash=NULL,
        lease_expires_at=NULL,last_heartbeat_at=NULL,updated_at=?
        WHERE id=? AND state='RUNNING' AND lease_token_hash=? AND lease_expires_at>?""",
        (
            "PENDING" if retry else "FAILED_PERMANENT",
            now + backoff if retry else now,
            str(message)[-2000:],
            now,
            job.job_id,
            _token_hash(job.lease_token),
            now,
        ),
    )
    if cur.rowcount != 1:
        raise DurableRunnerError("lease lost before failure commit")
    return "PENDING" if retry else "FAILED_PERMANENT"


def _handler_command(job: ClaimedJob, workspace: Path) -> list[str]:
    if job.kind != JOB_KIND_MEDIA_BATCH:
        raise DurableRunnerError(f"unregistered handler: {job.kind}")
    payload = validate_payload(job.kind, job.payload, workspace)
    manifest = _workspace_path(workspace, payload["manifest_path"], must_exist=True)
    cmd = [
        sys.executable,
        "-m", "scripts.media_batch_command_center",
        "run",
        "--manifest", str(manifest),
        "--max-parallel", str(int(payload["max_parallel"])),
        "--state", str(payload["state"]),
    ]
    if payload.get("output_dir"):
        cmd += ["--output-dir", str(_workspace_path(workspace, payload["output_dir"]))]
    return cmd


def _terminate_process_group(process: subprocess.Popen[str], *, grace_seconds: float = 5.0) -> None:
    """Clean children even when their session leader has already exited."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait(timeout=grace_seconds)
        return
    except OSError:
        if process.poll() is None:
            process.terminate()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    # wait() only reaps the leader; a child may ignore SIGTERM.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        if process.poll() is None:
            process.kill()
    process.wait(timeout=grace_seconds)


@contextmanager
def runner_lock(workspace: Path):
    """One runner per workspace, even if callers use different queue DBs."""
    path = _workspace_path(workspace, ".durable-runner.lock")
    with path.open("a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DurableRunnerError("runner already active for workspace") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def health_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    counts = {row["state"]: row["n"] for row in conn.execute(
        "SELECT state,COUNT(*) n FROM jobs GROUP BY state")}
    oldest = conn.execute("SELECT MIN(created_at) FROM jobs WHERE state='PENDING'").fetchone()[0]
    expired = conn.execute("SELECT COUNT(*) FROM jobs WHERE state='RUNNING' AND lease_expires_at<=?", (_now(),)).fetchone()[0]
    return {"status": "DEGRADED" if expired else "OK", "counts": counts,
            "expired_leases": expired,
            "oldest_pending_seconds": round(max(0, _now() - oldest), 3) if oldest else 0,
            "publish_enabled": False}


def execute_claimed(
    conn: sqlite3.Connection,
    job: ClaimedJob,
    *,
    workspace: Path,
    lease_seconds: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    try:
        if not job.payload.get("manifest_sha256"):
            # Pin work already queued by an earlier runner version without discarding it.
            legacy = validate_payload(job.kind, job.payload, workspace)
            manifest = _workspace_path(workspace, legacy["manifest_path"], must_exist=True)
            snapshot, digest, output = _snapshot_manifest(
                workspace, manifest,
                _workspace_path(workspace, legacy["output_dir"]) if legacy.get("output_dir") else None,
            )
            pinned = dict(legacy)
            pinned.update({"manifest_path": str(snapshot.relative_to(workspace.resolve())),
                           "manifest_sha256": digest,
                           "output_dir": str(output.relative_to(workspace.resolve()))})
            cur = conn.execute(
                "UPDATE jobs SET payload_json=?,updated_at=? WHERE id=? AND state='RUNNING' AND lease_token_hash=? AND lease_expires_at>?",
                (_canonical_json(pinned), _now(), job.job_id, _token_hash(job.lease_token), _now()),
            )
            if cur.rowcount != 1:
                return {"status": "LEASE_LOST", "job_id": job.job_id}
            job = replace(job, payload=pinned)
        cmd = _handler_command(job, workspace)
    except (DurableRunnerError, OSError, ValueError) as exc:
        try:
            state = _fail(conn, job, f"INVALID_INPUT:{type(exc).__name__}", retryable=False)
        except DurableRunnerError:
            return {"status": "LEASE_LOST", "job_id": job.job_id}
        return {"status": state, "job_id": job.job_id}
    started = time.monotonic()
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output:
        try:
            process = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                env=_safe_env(),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            state = _fail(conn, job, f"PROCESS_START:{type(exc).__name__}", retryable=True)
            return {"status": state, "job_id": job.job_id}
        heartbeat_interval = max(5.0, min(30.0, lease_seconds / 3.0))
        next_heartbeat = time.monotonic() + heartbeat_interval
        timed_out = False
        try:
            while process.poll() is None:
                now_mono = time.monotonic()
                if now_mono - started >= timeout_seconds:
                    timed_out = True
                    _terminate_process_group(process)
                    break
                if now_mono >= next_heartbeat:
                    if not heartbeat(conn, job, lease_seconds=lease_seconds):
                        _terminate_process_group(process)
                        raise DurableRunnerError("lease lost during execution")
                    next_heartbeat = now_mono + heartbeat_interval
                time.sleep(0.2)
        except DurableRunnerError as exc:
            _terminate_process_group(process)
            if "lease lost" in str(exc):
                return {"status": "LEASE_LOST", "job_id": job.job_id}
            try:
                _fail(conn, job, "EXECUTION_INTERRUPTED", retryable=True)
            except DurableRunnerError:
                pass
            raise
        except BaseException:
            _terminate_process_group(process)
            try:
                _fail(conn, job, "EXECUTION_INTERRUPTED", retryable=True)
            except DurableRunnerError:
                pass
            raise
        output.seek(0, os.SEEK_END)
        length = output.tell()
        output.seek(0)
        text = output.read(1024 * 1024).strip() if length <= 1024 * 1024 else ""
    if timed_out:
        state = _fail(conn, job, "PROCESS_TIMEOUT", retryable=True)
        return {"status": state, "job_id": job.job_id}
    if process.returncode != 0:
        retryable = process.returncode in {75, 111}
        state = _fail(conn, job, f"HANDLER_EXIT_{process.returncode}", retryable=retryable)
        return {"status": state, "job_id": job.job_id}
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        _fail(conn, job, "HANDLER_NON_JSON_OR_OVERSIZE", retryable=False)
        return {"status": "FAILED_PERMANENT", "job_id": job.job_id}
    if not isinstance(result, dict) or result.get("status") != "PASS":
        _fail(conn, job, "HANDLER_NOT_PASS", retryable=False)
        return {"status": "FAILED_PERMANENT", "job_id": job.job_id}
    _complete(conn, job, result)
    return {"status": "READY_TO_PUBLISH", "job_id": job.job_id, "elapsed_seconds": round(time.monotonic() - started, 3)}


def status_rows(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id,kind,source_id,state,priority,attempts,max_attempts,available_at,lease_expires_at,updated_at,last_error FROM jobs ORDER BY id DESC LIMIT ?",
        (max(1, min(500, int(limit))),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_job_spec(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise DurableRunnerError("job spec must be an object")
    allowed = {"kind", "source_id", "payload", "priority", "max_attempts"}
    extra = sorted(set(value) - allowed)
    if extra:
        raise DurableRunnerError(f"unsupported job spec keys: {extra}")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    enqueue = sub.add_parser("enqueue")
    enqueue.add_argument("--job", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--once", action="store_true")
    run.add_argument("--poll-seconds", type=float, default=0.2)
    run.add_argument("--lease-seconds", type=int, default=120)
    run.add_argument("--timeout-seconds", type=int, default=1800)
    status = sub.add_parser("status")
    status.add_argument("--limit", type=int, default=50)
    sub.add_parser("recover")
    sub.add_parser("health")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        raise SystemExit(f"workspace not found: {workspace}")
    conn = connect(args.db)
    init_db(conn)
    if args.command == "init":
        print(json.dumps({"status": "INITIALIZED", "schema_version": SCHEMA_VERSION, "db": str(args.db.resolve())}))
        return 0
    if args.command == "enqueue":
        spec = _load_job_spec(args.job.resolve())
        result = enqueue_job(
            conn,
            workspace=workspace,
            kind=str(spec.get("kind") or ""),
            source_id=str(spec.get("source_id") or ""),
            payload=spec.get("payload") or {},
            priority=int(spec.get("priority", 0)),
            max_attempts=int(spec.get("max_attempts", 2)),
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "status":
        print(json.dumps({"status": "OK", "jobs": status_rows(conn, args.limit)}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "health":
        report = health_summary(conn)
        print(json.dumps(report, sort_keys=True))
        return 0 if report["status"] == "OK" else 2
    if args.command == "recover":
        print(json.dumps({"status": "OK", "recovered": recover_expired(conn)}))
        return 0
    if not 0.05 <= args.poll_seconds <= 60:
        raise SystemExit("--poll-seconds must be 0.05..60")
    if not 30 <= args.lease_seconds <= 3600:
        raise SystemExit("--lease-seconds must be 30..3600")
    if not 10 <= args.timeout_seconds <= 21600:
        raise SystemExit("--timeout-seconds must be 10..21600")
    def stop(signum, frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, stop)
    try:
        with runner_lock(workspace):
            while True:
                claimed = claim_job(conn, lease_seconds=args.lease_seconds)
                if claimed is None:
                    if args.once:
                        print(json.dumps({"status": "IDLE"}))
                        return 0
                    time.sleep(args.poll_seconds)
                    continue
                result = execute_claimed(
                    conn,
                    claimed,
                    workspace=workspace,
                    lease_seconds=args.lease_seconds,
                    timeout_seconds=args.timeout_seconds,
                )
                print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
                if args.once:
                    return 0 if result["status"] == "READY_TO_PUBLISH" else 2
    except KeyboardInterrupt:
        return 0
    finally:
        signal.signal(signal.SIGTERM, previous)
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
