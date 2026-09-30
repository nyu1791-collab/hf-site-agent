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
