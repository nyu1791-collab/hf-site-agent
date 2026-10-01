from __future__ import annotations

import json
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from scripts.durable_media_runner import (
    DurableRunnerError,
    _safe_env,
    _terminate_process_group,
    claim_job,
    connect,
    enqueue_job,
    heartbeat,
    init_db,
    recover_expired,
    validate_payload,
)


def make_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    workspace.mkdir()
    source = workspace / "source.mp4"
    source.write_bytes(b"video")
    manifest = workspace / "job.json"
    manifest.write_text(json.dumps({
        "schema_version": "media-batch-command-v1",
        "output_dir": "out",
        "jobs": [{
            "job_id": "j1",
            "input_path": "source.mp4",
            "input_sha256": "0" * 64,
            "rights_verified": True,
            "duration_seconds": 1,
            "output_name": "out.mp4",
        }],
    }), encoding="utf-8")
    return workspace


class DurableMediaRunnerTests(unittest.TestCase):
    def test_enqueue_dedupes_and_claims_with_hashed_lease(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = make_workspace(root)
            conn = connect(root / "queue.sqlite3")
            init_db(conn)
            first = enqueue_job(conn, workspace=workspace, kind="MEDIA_BATCH_RUN", source_id="feed:1", payload={"manifest_path": "job.json"})
            second = enqueue_job(conn, workspace=workspace, kind="MEDIA_BATCH_RUN", source_id="feed:1", payload={"manifest_path": "job.json"})
            self.assertEqual(first["status"], "ENQUEUED")
            self.assertEqual(second["status"], "DEDUPED")
            claimed = claim_job(conn, lease_seconds=60)
            self.assertIsNotNone(claimed)
            row = conn.execute("SELECT lease_token_hash FROM jobs WHERE id=?", (claimed.job_id,)).fetchone()
            self.assertNotEqual(row["lease_token_hash"], claimed.lease_token)
            self.assertTrue(heartbeat(conn, claimed, lease_seconds=60))

    def test_enqueue_uses_immutable_manifest_snapshot_and_changed_input_is_new_job(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = make_workspace(root)
            conn = connect(root / "queue.sqlite3")
            init_db(conn)
            first = enqueue_job(conn, workspace=workspace, kind="MEDIA_BATCH_RUN", source_id="same", payload={"manifest_path": "job.json"})
            row = conn.execute("SELECT payload_json FROM jobs WHERE id=?", (first["job_id"],)).fetchone()
            frozen = json.loads(row["payload_json"])
            original = workspace / "job.json"
            original.write_text(original.read_text().replace('"duration_seconds": 1', '"duration_seconds": 2'), encoding="utf-8")
            second = enqueue_job(conn, workspace=workspace, kind="MEDIA_BATCH_RUN", source_id="same", payload={"manifest_path": "job.json"})
            self.assertEqual(second["status"], "ENQUEUED")
            frozen_manifest = workspace / frozen["manifest_path"]
            self.assertEqual(json.loads(frozen_manifest.read_text())["jobs"][0]["duration_seconds"], 1)
            self.assertEqual(frozen["manifest_sha256"], __import__("hashlib").sha256(frozen_manifest.read_bytes()).hexdigest())

    def test_nested_manifest_paths_match_renderer_and_reject_symlink_escape(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = make_workspace(root)
            nested = workspace / "nested"
            nested.mkdir()
            source = nested / "source.mp4"
            source.write_bytes(b"nested")
            manifest = nested / "job.json"
            manifest.write_text(json.dumps({
                "schema_version": "media-batch-command-v1",
                "jobs": [{"job_id": "nested", "input_path": "source.mp4", "input_sha256": "0" * 64,
                          "rights_verified": True, "duration_seconds": 1, "output_name": "nested.mp4"}],
                "output_dir": "out",
            }))
            outside = root / "outside"
            outside.mkdir()
            (nested / "out").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(DurableRunnerError):
                validate_payload("MEDIA_BATCH_RUN", {"manifest_path": "nested/job.json"}, workspace)

    def test_expired_lease_cannot_heartbeat_complete_or_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = make_workspace(root)
            conn = connect(root / "queue.sqlite3")
            init_db(conn)
            enqueue_job(conn, workspace=workspace, kind="MEDIA_BATCH_RUN", source_id="expired", payload={"manifest_path": "job.json"})
            claimed = claim_job(conn, lease_seconds=60)
            conn.execute("UPDATE jobs SET lease_expires_at=? WHERE id=?", (time.time() - 1, claimed.job_id))
            self.assertFalse(heartbeat(conn, claimed, lease_seconds=60))
            with self.assertRaises(DurableRunnerError):
                from scripts.durable_media_runner import _complete
                _complete(conn, claimed, {"status": "PASS"})
            with self.assertRaises(DurableRunnerError):
                from scripts.durable_media_runner import _fail
                _fail(conn, claimed, "late", retryable=False)
            self.assertEqual(conn.execute("SELECT state FROM jobs").fetchone()["state"], "RUNNING")

    def test_invalid_input_isolated_and_marks_job_failed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = make_workspace(root)
            conn = connect(root / "queue.sqlite3")
            init_db(conn)
            enqueue_job(conn, workspace=workspace, kind="MEDIA_BATCH_RUN", source_id="deleted", payload={"manifest_path": "job.json"})
            claimed = claim_job(conn, lease_seconds=60)
            (workspace / "job.json").unlink()
            result = __import__("scripts.durable_media_runner", fromlist=["execute_claimed"]).execute_claimed(
                conn, claimed, workspace=workspace, lease_seconds=60, timeout_seconds=10)
            self.assertEqual(result["status"], "FAILED_PERMANENT")
            self.assertEqual(conn.execute("SELECT state FROM jobs").fetchone()["state"], "FAILED_PERMANENT")

    def test_legacy_pending_job_is_pinned_when_claimed(self):
        from scripts.durable_media_runner import execute_claimed
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = make_workspace(root)
            conn = connect(root / "queue.sqlite3")
            init_db(conn)
            # Simulate a valid row queued by the pre-snapshot runner.
            payload = {"manifest_path": "job.json", "max_parallel": 3, "state": "NORMAL"}
            conn.execute(
                """INSERT INTO jobs(kind,dedupe_key,source_id,payload_json,state,available_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                ("MEDIA_BATCH_RUN", "legacy", "legacy-source", json.dumps(payload), "PENDING",
                 time.time(), time.time(), time.time()),
            )
            claimed = claim_job(conn, lease_seconds=60)
            class SuccessfulHandler:
                pid = 5432
                returncode = 0
                def __init__(self, *args, **kwargs):
                    kwargs["stdout"].write('{"status":"PASS"}\n')
                def poll(self): return 0
            with mock.patch("scripts.durable_media_runner.subprocess.Popen", SuccessfulHandler):
                result = execute_claimed(conn, claimed, workspace=workspace, lease_seconds=60, timeout_seconds=10)
            self.assertEqual(result["status"], "READY_TO_PUBLISH")
            stored = json.loads(conn.execute("SELECT payload_json FROM jobs WHERE id=?", (claimed.job_id,)).fetchone()["payload_json"])
            self.assertTrue(stored["manifest_sha256"])
            self.assertTrue((workspace / stored["manifest_path"]).is_file())

    def test_runner_lock_blocks_second_process_for_workspace(self):
        from scripts.durable_media_runner import runner_lock
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td) / "workspace"
            workspace.mkdir()
            with runner_lock(workspace):
                with self.assertRaises(DurableRunnerError):
                    with runner_lock(workspace):
                        pass

    def test_handler_starts_command_center_as_a_module(self):
        from scripts.durable_media_runner import _handler_command
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = make_workspace(root)
            conn = connect(root / "queue.sqlite3")
            init_db(conn)
            enqueue_job(conn, workspace=workspace, kind="MEDIA_BATCH_RUN", source_id="cmd", payload={"manifest_path": "job.json"})
            claimed = claim_job(conn, lease_seconds=60)
            command = _handler_command(claimed, workspace)
            self.assertEqual(command[1:3], ["-m", "scripts.media_batch_command_center"])
            result = subprocess.run([command[0], "-m", "scripts.media_batch_command_center", "--help"],
                                    cwd=Path(__file__).resolve().parents[1], env=_safe_env(),
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_expired_lease_recovers_without_duplicate_job(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = make_workspace(root)
            conn = connect(root / "queue.sqlite3")
            init_db(conn)
            enqueue_job(conn, workspace=workspace, kind="MEDIA_BATCH_RUN", source_id="feed:2", payload={"manifest_path": "job.json"}, max_attempts=2)
            claimed = claim_job(conn, lease_seconds=60)
            conn.execute("UPDATE jobs SET lease_expires_at=? WHERE id=?", (time.time() - 1, claimed.job_id))
            self.assertEqual(recover_expired(conn), 1)
            rows = conn.execute("SELECT id,state FROM jobs").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["state"], "PENDING")

    def test_rejects_workspace_escape_and_secret_or_command_fields(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = make_workspace(root)
            outside = root / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            with self.assertRaises(DurableRunnerError):
                validate_payload("MEDIA_BATCH_RUN", {"manifest_path": str(outside)}, workspace)
            with self.assertRaises(DurableRunnerError):
                validate_payload("MEDIA_BATCH_RUN", {"manifest_path": "job.json", "api_key": "x"}, workspace)
            with self.assertRaises(DurableRunnerError):
                validate_payload("MEDIA_BATCH_RUN", {"manifest_path": "job.json", "command": "rm -rf /"}, workspace)

    def test_manifest_input_cannot_escape_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = make_workspace(root)
            outside = root / "outside.mp4"
            outside.write_bytes(b"x")
            bad = workspace / "bad.json"
            bad.write_text(json.dumps({"jobs": [{"input_path": "../outside.mp4"}]}), encoding="utf-8")
            with self.assertRaises(DurableRunnerError):
                validate_payload("MEDIA_BATCH_RUN", {"manifest_path": "bad.json"}, workspace)

    def test_max_parallel_is_hard_capped_at_three(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = make_workspace(Path(td))
            with self.assertRaises(DurableRunnerError):
                validate_payload("MEDIA_BATCH_RUN", {"manifest_path": "job.json", "max_parallel": 4}, workspace)

    def test_safe_environment_does_not_forward_api_credentials(self):
        with mock.patch.dict("os.environ", {"PATH": "/bin", "OPENROUTER_API_KEY": "secret", "DEEPSEEK_API_KEY": "secret"}, clear=True):
            env = _safe_env()
        self.assertEqual(env.get("PATH"), "/bin")
        self.assertNotIn("OPENROUTER_API_KEY", env)
        self.assertNotIn("DEEPSEEK_API_KEY", env)

    def test_process_group_is_killed_if_graceful_stop_times_out(self):
        class FakeProcess:
            pid = 4321

            def __init__(self):
                self.waits = 0

            def poll(self):
                return None

            def wait(self, timeout=None):
                self.waits += 1
                if self.waits == 1:
                    raise subprocess.TimeoutExpired(cmd="handler", timeout=timeout)
                return 0

            def terminate(self):
                raise AssertionError("direct terminate should not be used when killpg works")

            def kill(self):
                raise AssertionError("direct kill should not be used when killpg works")

        process = FakeProcess()
        with mock.patch("scripts.durable_media_runner.os.killpg") as killpg:
            _terminate_process_group(process, grace_seconds=0.01)
        self.assertEqual(
            killpg.call_args_list,
            [mock.call(4321, signal.SIGTERM), mock.call(4321, signal.SIGKILL)],
        )
        self.assertEqual(process.waits, 2)

    def test_process_group_cleanup_runs_if_session_leader_already_exited(self):
        class ExitedProcess:
            pid = 4321
            def poll(self): return 0
            def wait(self, timeout=None): return 0
        with mock.patch("scripts.durable_media_runner.os.killpg") as killpg:
            _terminate_process_group(ExitedProcess(), grace_seconds=0.01)
        self.assertEqual(killpg.call_args_list,
                         [mock.call(4321, signal.SIGTERM), mock.call(4321, signal.SIGKILL)])

    def test_speed_policy_keeps_only_fatal_gates_and_no_five_agent_chain(self):
        root = Path(__file__).resolve().parents[1]
        policy = json.loads((root / "config/media_automation_fast_path.json").read_text(encoding="utf-8"))
        self.assertEqual(policy["status"], "ENFORCED_SPEED_FIRST_STANDARD")
        self.assertIn("FIVE_ALWAYS_ON_AI_AGENTS", policy["intentionally_not_adopted"])
        self.assertEqual(policy["extracted_pipeline"]["supervisor"]["default"], "MACHINE_GATE_ONLY")
        self.assertFalse(policy["extracted_pipeline"]["analytics_and_feedback"]["critical_path"])

    def test_second_runner_cannot_claim_same_active_job(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = make_workspace(root)
            conn = connect(root / "queue.sqlite3")
            init_db(conn)
            enqueue_job(conn, workspace=workspace, kind="MEDIA_BATCH_RUN", source_id="feed:3", payload={"manifest_path": "job.json"})
            first = claim_job(conn, lease_seconds=60)
            self.assertIsNotNone(first)
            self.assertIsNone(claim_job(conn, lease_seconds=60))


if __name__ == "__main__":
    unittest.main()
