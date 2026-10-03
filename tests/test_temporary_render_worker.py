import http.client
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from scripts import media_render_worker as worker, media_render_transport as transport
from scripts import media_render_e2e as e2e
from scripts import setup_temporary_gcp_render_worker as setup


class TemporaryWorkerTests(unittest.TestCase):
    def test_initial_auth_is_not_provisioned_without_explicit_user_flag(self):
        from unittest.mock import Mock
        coordinator, remote_worker = Mock(), Mock()
        with patch.object(setup.secrets, "token_urlsafe") as token:
            with self.assertRaisesRegex(RuntimeError, "explicit --provision-render-auth"):
                setup.setup(coordinator, remote_worker, "a" * 40, False)
            coordinator.ssh.assert_not_called()
            remote_worker.ssh.assert_not_called()
            token.assert_not_called()

    def test_sudo_failure_prevents_secret_creation_and_upload(self):
        from unittest.mock import Mock
        coordinator, remote_worker = Mock(), Mock()
        coordinator.ssh.side_effect = RuntimeError("sudo unavailable")
        with patch.object(setup.secrets, "token_urlsafe") as token:
            with self.assertRaisesRegex(RuntimeError, "sudo unavailable"):
                setup.setup(coordinator, remote_worker, "a" * 40, True)
            coordinator.put.assert_not_called()
            remote_worker.put.assert_not_called()
            token.assert_not_called()

    def test_lock_excludes_another_writer_and_drain_preserves_existing_results(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = root / ("response-" + str(uuid.uuid4()) + ".tar.gz")
            result.write_bytes(b"preserved")
            with worker.job_lock(root):
                with self.assertRaisesRegex(worker.WorkerJobError, "WORKER_BUSY"):
                    with worker.job_lock(root):
                        pass
                state = worker.set_draining(root, True)
                self.assertTrue(state["active_render"])
                self.assertFalse(state["safe_to_stop"])
            state = worker.lifecycle_status(root)
            self.assertTrue(state["safe_to_stop"])
            self.assertEqual(state["retained_results"], 1)
            self.assertEqual(result.read_bytes(), b"preserved")
            with self.assertRaisesRegex(worker.WorkerJobError, "WORKER_DRAINING"), patch.object(worker, "_run_render_job") as render:
                worker.run_render_job(root / "request.tar.gz", {"status": "READY"}, {"work_dir": root})
            render.assert_not_called()
            self.assertFalse(worker.set_draining(root, False)["draining"])

    def test_completed_result_survives_server_restart_and_is_authenticated(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            request_id = str(uuid.uuid4())
            ledger = worker._reserve_request_id(root, request_id, "a" * 64)
            worker._complete_request_id(ledger, request_id, "a" * 64, "b" * 64)
            archive = root / f"response-{request_id}.tar.gz"
            archive.write_bytes(b"durable-result")
            for _ in range(2):
                server = worker.SingleRequestHTTPServer(("127.0.0.1", 0), worker.RenderHandler)
                server.shared_token = "c" * 43
                server.paths = {"work_dir": root}
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    for token, expected in (("wrong", 401), ("c" * 43, 200)):
                        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                        connection.request("GET", "/v1/results/" + request_id, headers={"Authorization": "Bearer " + token})
                        response = connection.getresponse()
                        payload = response.read()
                        self.assertEqual(response.status, expected)
                        if expected == 200:
                            self.assertEqual(payload, b"durable-result")
                        connection.close()
                finally:
                    server.shutdown()
                    thread.join(2)
                    server.server_close()
                self.assertTrue(archive.is_file())
            with self.assertRaisesRegex(worker.WorkerJobError, "REQUEST_ID_REPLAY"):
                worker._reserve_request_id(root, request_id, "a" * 64)

    def test_renderer_timeout_terminates_the_process_group(self):
        with patch.object(worker.subprocess, "Popen") as popen, patch.object(worker.os, "killpg") as kill:
            process = popen.return_value
            process.pid = 12345
            process.wait.side_effect = [worker.subprocess.TimeoutExpired("render", 1), 0]
            with self.assertRaisesRegex(worker.WorkerJobError, "RENDER_TIMEOUT"):
                worker._run_renderer(["renderer"], {}, timeout=1)
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            kill.assert_called_once_with(12345, worker.signal.SIGTERM)

    def test_disconnected_render_recovers_by_get_without_duplicate_post(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"MEDIA_RENDER_SHARED_TOKEN": "t" * 43}):
            package = Path(td)
            request_id = str(uuid.uuid4())
            source_id = "s" * 64
            manifest = {"request_id": request_id, "source_id": source_id, "worker_code_sha256": {"worker": "w"}}
            def create(**kwargs):
                kwargs["output_path"].write_bytes(b"fixed-request")
                return manifest
            def extract(_archive, directory, **_kwargs):
                video = directory / "final.mp4"
                video.write_bytes(b"verified-video")
                return {"video_path": video, "report": {"source_id": source_id}}
            inputs = dict(package=package, source_id=source_id, presentation_path=package / "presentation.json", timing_path=package / "timing.json", assets=[], duration_seconds=1)
            with patch.object(transport, "_read_health", return_value={"status": "READY"}), patch.object(transport, "_validate_health"), patch.object(transport, "create_request_archive", side_effect=create) as create_mock, patch.object(transport, "_send_archive", side_effect=transport.RenderTransportError("connection lost")) as post, patch.object(transport, "_fetch_archive", side_effect=lambda _p, _t, _id, path: path.write_bytes(b"retained-result")) as get, patch.object(transport, "_extract_response", side_effect=extract), patch.object(transport, "_verify_video", return_value={"duration_seconds": 1, "streams": ["audio", "video"], "bytes": 14}):
                with self.assertRaisesRegex(transport.RenderTransportError, "connection lost"):
                    transport.dispatch_remote_render(**inputs)
                result = transport.dispatch_remote_render(**inputs)
                self.assertEqual(result["status"], "READY_TO_PUBLISH")
                self.assertEqual(post.call_count, 1)
                self.assertEqual(create_mock.call_count, 1)
                get.assert_called_once()
                self.assertEqual(json.loads((package / "render-request.json").read_text())["state"], "VERIFIED")

    def test_worker_off_keeps_test_queue_and_never_calls_paid_source_processing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            db = root / "queue.sqlite3"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE existing_jobs (id INTEGER PRIMARY KEY, state TEXT)")
            conn.execute("INSERT INTO existing_jobs VALUES (1, 'waiting')")
            conn.commit()
            conn.close()
            inputs = {"source_id": "a" * 64, "duration_seconds": 1}
            with patch.object(e2e, "prepare_package", return_value=(root, inputs)), patch.object(e2e, "dispatch_remote_render", side_effect=transport.RenderTransportError("worker offline")), patch("scripts.media_news_pipeline.process_source", side_effect=AssertionError("paid source stage must not run")):
                result = e2e.run_e2e(db, root, "first")
            self.assertEqual(result["queue_status"], "waiting")
            self.assertEqual(result["paid_llm_requests"], 0)
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT * FROM existing_jobs").fetchall(), [(1, "waiting")])
                self.assertEqual(conn.execute("SELECT status FROM render_e2e_jobs").fetchone()[0], "waiting")


if __name__ == "__main__":
    unittest.main()
