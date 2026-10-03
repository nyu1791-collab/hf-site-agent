"""Real FFmpeg/protocol smoke; mocked PCM fixture is not live VM/VOICEVOX evidence."""
import array
import http.client
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from unittest.mock import patch

from scripts import media_render_e2e as e2e, media_render_worker as worker, media_render_transport as transport

ROOT = Path(__file__).resolve().parents[1]
FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe") and FONT.is_file(), "local FFmpeg and DejaVu font required")
class RenderFfmpegSmoke(unittest.TestCase):
    def test_real_encode_replay_drain_and_process_restart_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            shell = e2e.materialize_shell(root / "shell")
            work = root / "worker";work.mkdir(mode=0o700)
            db = root / "queue.sqlite3"
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE existing_queue (id INTEGER PRIMARY KEY, state TEXT)")
                conn.execute("INSERT INTO existing_queue VALUES (1, 'waiting')")
            asset_env = {"MEDIA_RENDER_SHARED_TOKEN": "t" * 43, "MEDIA_RENDER_SHELL": str(shell), "MEDIA_RENDER_FONT": str(FONT),
                         "MEDIA_RENDER_SHELL_SHA256": worker._tree_sha256(shell), "MEDIA_RENDER_FONT_SHA256": transport._sha256_file(FONT),
                         "MEDIA_RENDER_EXPECTED_SHELL_SHA256": worker._tree_sha256(shell), "MEDIA_RENDER_EXPECTED_FONT_SHA256": transport._sha256_file(FONT), "MEDIA_RENDER_WORK_DIR": str(work)}
            child_env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "LANG", "TMPDIR"}}
            child_env.update(asset_env)
            def launch():
                process = subprocess.Popen([sys.executable, "-m", "scripts.media_render_worker", "--work-dir", str(work)], cwd=ROOT, env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                for _ in range(100):
                    if process.poll() is not None:
                        self.fail("worker failed to start: " + process.communicate()[1].decode())
                    try:
                        if transport.check_remote_worker()["status"] == "READY":
                            return process
                    except transport.RenderTransportError:
                        pass
                    time.sleep(0.05)
                process.terminate();process.wait(5)
                self.fail("worker startup timed out")
            def mock_voice(package, **_kwargs):
                mission = json.loads((package / "mission.json").read_text())
                # Deliberate audio stimulus to test encode/mouth amplitude; no LLM/TTS service.
                samples = array.array("h", (int(15000 * math.sin(2 * math.pi * 440 * n / 48000)) if n % 12000 < 6000 else 0 for n in range(4 * 48000)))
                with wave.open(str(package / "audio.wav"), "wb") as output:
                    output.setparams((1, 2, 48000, 0, "NONE", "not compressed"));output.writeframes(samples.tobytes())
                records = [{"id": line["id"], "scene_id": "connection", "speaker": line["speaker"], "style_id": 3 if i % 2 == 0 else 2,
                            "start": i, "end": i+1, "caption_text": line["voice_text"], "emotion": "NORMAL"} for i, line in enumerate(mission["scenes"][0]["dialogue"])]
                (package / "timing.json").write_text(json.dumps({"total_duration": 4, "records": records, "engine_version": "MOCK_PCM_TEST_ONLY"}))
            with patch.dict(os.environ, asset_env):
                process = launch()
                try:
                    with patch.object(e2e, "synthesize_voice", side_effect=mock_voice):
                        result = e2e.run_e2e(db, root, "smoke")
                    diagnostics = "\n".join(p.read_text(errors="replace")[-3000:] for p in (work / "render-logs").glob("*.log"))
                    self.assertEqual(result["queue_status"], "success", str(result) + diagnostics)
                    self.assertEqual(result["ffprobe"]["streams"], ["audio", "video"])
                    self.assertGreater(result["ffprobe"]["bytes"], 0)
                    self.assertGreater(result["ffprobe"]["duration_seconds"], 0)
                    self.assertEqual(result["paid_llm_requests"], 0)
                    package = Path(result["video"]).parent
                    checkpoint = json.loads((package / "render-request.json").read_text())
                    request_id = checkpoint["manifest"]["request_id"]
                    with self.assertRaisesRegex(transport.RenderTransportError, "REQUEST_ID_REPLAY"):
                        transport._send_archive(18765, "t" * 43, package / "render-request.tar.gz", root / "duplicate.tar.gz")
                    worker.set_draining(work, True)
                    with self.assertRaises(transport.RenderTransportError):
                        transport.check_remote_worker()
                    self.assertTrue(worker.lifecycle_status(work)["safe_to_stop"])
                    worker.set_draining(work, False)
                finally:
                    process.terminate();process.wait(10);process.communicate()
                process = launch()
                try:
                    recovered = root / "recovered.tar.gz"
                    transport._fetch_archive(18765, "t" * 43, request_id, recovered)
                    destination = root / "restored";destination.mkdir()
                    extracted = transport._extract_response(recovered, destination, request_id=request_id, source_id=checkpoint["manifest"]["source_id"])
                    self.assertEqual(transport._sha256_file(extracted["video_path"]), transport._sha256_file(Path(result["video"])))
                    with patch.object(e2e, "synthesize_voice", side_effect=AssertionError("successful audio must not be regenerated")), patch.object(e2e, "dispatch_remote_render", side_effect=AssertionError("successful render must not be repeated")):
                        cached = e2e.run_e2e(db, root, "smoke")
                    self.assertTrue(cached["reused"])
                    with sqlite3.connect(db) as conn:
                        self.assertEqual(conn.execute("SELECT * FROM existing_queue").fetchall(), [(1, "waiting")])
                    output = os.environ.get("HF_RENDER_SMOKE_OUTPUT")
                    if output:
                        target = Path(output);target.mkdir(parents=True,exist_ok=True)
                        shutil.copyfile(result["video"], target / "final.mp4")
                        (target / "verification.json").write_text(json.dumps({"scope": "LOCAL_MOCK_PCM_SMOKE_NOT_LIVE_VM_E2E", "ffprobe": result["ffprobe"], "process_restart_recovery": True, "queue_preserved": True, "paid_llm_calls": 0}, indent=2))
                    finished, errors = [], []
                    def in_flight():
                        try:
                            with patch.object(e2e, "synthesize_voice", side_effect=mock_voice):
                                finished.append(e2e.run_e2e(db, root, "graceful"))
                        except Exception as exc:
                            errors.append(exc)
                    thread = threading.Thread(target=in_flight)
                    thread.start()
                    for _ in range(100):
                        if worker.lifecycle_status(work)["active_render"]:
                            break
                        time.sleep(0.02)
                    else:
                        self.fail("second render never acquired its lock")
                    process.terminate()
                    thread.join(10)
                    process.wait(10)
                    self.assertFalse(errors)
                    self.assertFalse(thread.is_alive())
                    self.assertEqual(finished[0]["queue_status"], "success")
                finally:
                    process.terminate();process.wait(10);process.communicate()


if __name__ == "__main__":
    unittest.main()
