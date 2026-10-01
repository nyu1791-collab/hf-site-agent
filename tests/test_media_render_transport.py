import hashlib
import gzip
import json
import os
import sqlite3
import tarfile
import tempfile
import http.client
import threading
import unittest
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import media_news_pipeline, media_render_transport as transport
from scripts import media_render_worker as worker


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "a" * 43
SOURCE_ID = "b" * 64
SHELL_HASH = "c" * 64
FONT_HASH = "d" * 64


def _asset_environment():
    return {
        "MEDIA_RENDER_SHARED_TOKEN": TOKEN,
        "MEDIA_RENDER_EXPECTED_SHELL_SHA256": SHELL_HASH,
        "MEDIA_RENDER_EXPECTED_FONT_SHA256": FONT_HASH,
    }


def _write_audio(path: Path, duration: float = 1.0) -> None:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(b"\0\0" * int(48000 * duration))


class MediaRenderTransportTests(unittest.TestCase):
    def test_endpoint_is_numeric_loopback_only(self):
        self.assertEqual(transport._checked_loopback_url("http://127.0.0.1:18765/v1/render"), (18765, "/v1/render"))
        for url in (
            "https://127.0.0.1:18765/v1/render",
            "http://localhost:18765/v1/render",
            "http://127.0.0.1.nip.io:18765/v1/render",
            "http://192.0.2.1:18765/v1/render",
            "http://user@127.0.0.1:18765/v1/render",
            "http://127.0.0.1:18765/v1/render?next=public",
            "http://127.0.0.1:18765/",
        ):
            with self.subTest(url=url), self.assertRaises(transport.RenderTransportError):
                transport._checked_loopback_url(url)

    def test_request_contains_only_selected_rights_cleared_assets(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _asset_environment()):
            package = Path(td) / SOURCE_ID
            images = package / "images"
            images.mkdir(parents=True)
            _write_audio(package / "audio.wav")
            timing = package / "render-timing.json"
            timing.write_text(json.dumps({"total_duration": 1}), encoding="utf-8")
            selected = images / "selected.png"
            selected.write_bytes(b"approved image")
            unselected = images / "candidate.jpg"
            unselected.write_bytes(b"not selected")
            digest = hashlib.sha256(selected.read_bytes()).hexdigest()
            asset = {
                "id": "selected-1", "file": str(selected), "sha256": digest,
                "downloaded": True, "selected_for_render": True, "rights_verified": True,
                "rights_basis": "official media reuse permission", "rights_evidence_url": "https://openai.com/policy",
                "credit": "OpenAI",
            }
            presentation = package / "presentation.json"
            presentation.write_text(json.dumps({
                "title": "A title", "source_url": "https://openai.com/news/example",
                "source_credit": "Official article", "voice_credit": "VOICEVOX",
                "media_region_only": True,
                "visuals": [{"id": "selected-1", "file": str(selected), "media_region_only": True}],
            }), encoding="utf-8")
            archive_path = package / "request.tar.gz"
            manifest = transport.create_request_archive(
                package=package, source_id=SOURCE_ID, presentation_path=presentation,
                timing_path=timing, assets=[asset], duration_seconds=1, output_path=archive_path,
            )
            self.assertEqual(len(manifest["files"]), 4)
            self.assertEqual(len(manifest["visuals"]), 1)
            self.assertTrue(manifest["visuals"][0]["rights_verified"])
            with tarfile.open(archive_path, "r:gz") as archive:
                names = set(archive.getnames())
                self.assertEqual(names, {"manifest.json", "audio.wav", "timing.json", "presentation.json", f"images/{digest}.png"})
                packed_manifest = json.load(archive.extractfile("manifest.json"))
                self.assertNotIn("candidate.jpg", json.dumps(packed_manifest))
                self.assertEqual(archive.extractfile(f"images/{digest}.png").read(), b"approved image")
            linked_images = package / "linked-images"
            linked_images.symlink_to(images, target_is_directory=True)
            with self.assertRaisesRegex(transport.RenderTransportError, "symbolic link"):
                transport._regular_package_file(package, linked_images / "selected.png")

    def test_request_requires_rights_evidence_and_pinned_worker_assets(self):
        with tempfile.TemporaryDirectory() as td:
            package = Path(td) / SOURCE_ID
            (package / "images").mkdir(parents=True)
            image = package / "images" / "x.png"
            image.write_bytes(b"visual")
            _write_audio(package / "audio.wav")
            timing = package / "timing.json"
            timing.write_text("{}", encoding="utf-8")
            presentation = package / "presentation.json"
            presentation.write_text(json.dumps({"visuals": [{"id": "x", "file": str(image)}]}), encoding="utf-8")
            asset = {"id": "x", "file": str(image), "sha256": hashlib.sha256(b"visual").hexdigest(),
                     "downloaded": True, "selected_for_render": True, "rights_verified": True,
                     "rights_basis": "permission", "rights_evidence_url": "javascript:bad", "credit": "credit"}
            with patch.dict(os.environ, _asset_environment()):
                with self.assertRaisesRegex(transport.RenderTransportError, "rights-cleared"):
                    transport.create_request_archive(package=package, source_id=SOURCE_ID,
                        presentation_path=presentation, timing_path=timing, assets=[asset],
                        duration_seconds=1, output_path=package / "request.tar.gz")
            with patch.dict(os.environ, {"MEDIA_RENDER_SHARED_TOKEN": TOKEN}, clear=False):
                os.environ.pop("MEDIA_RENDER_EXPECTED_SHELL_SHA256", None)
                with self.assertRaisesRegex(transport.RenderTransportError, "fingerprints"):
                    transport._expected_worker_asset_hashes()

    def test_worker_accepts_coordinator_archive_and_checks_each_payload_hash(self):
        env = {
            **_asset_environment(),
            "MEDIA_RENDER_SHELL_SHA256": SHELL_HASH,
            "MEDIA_RENDER_FONT_SHA256": FONT_HASH,
        }
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, env):
            package = Path(td) / SOURCE_ID
            (package / "images").mkdir(parents=True)
            _write_audio(package / "audio.wav")
            timing = package / "render-timing.json"
            timing.write_text(json.dumps({"total_duration": 1}), encoding="utf-8")
            image = package / "images" / "selected.webp"
            image.write_bytes(b"selected-image")
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            presentation = package / "presentation.json"
            presentation.write_text(json.dumps({
                "visuals": [{"id": "v1", "file": str(image), "media_region_only": True}],
            }), encoding="utf-8")
            asset = {
                "id": "v1", "file": str(image), "sha256": digest, "downloaded": True,
                "selected_for_render": True, "rights_verified": True, "rights_basis": "cleared",
                "rights_evidence_url": "https://openai.com/legal", "credit": "OpenAI",
            }
            archive = package / "request.tar.gz"
            transport.create_request_archive(
                package=package, source_id=SOURCE_ID, presentation_path=presentation,
                timing_path=timing, assets=[asset], duration_seconds=1, output_path=archive,
            )
            manifest, extracted = worker._extract_request(archive, package / "worker-input")
            self.assertEqual(manifest["source_id"], SOURCE_ID)
            with wave.open(str(extracted["audio.wav"]), "rb") as audio:
                self.assertEqual((audio.getnframes(), audio.getframerate()), (48000, 48000))
            self.assertEqual(extracted[f"images/{digest}.webp"].read_bytes(), b"selected-image")

    def test_bounded_gzip_decompression_stops_expansion(self):
        with tempfile.TemporaryDirectory() as td:
            compressed = Path(td) / "input.gz"
            expanded = Path(td) / "output.tar"
            with gzip.open(compressed, "wb") as stream:
                stream.write(b"x" * (2 * 1024 * 1024))
            with self.assertRaisesRegex(transport.RenderTransportError, "expands beyond"):
                transport._bounded_decompress(compressed, expanded, 1024 * 1024)

    def test_worker_manifest_rejects_traversal_and_shell_symlink(self):
        files = {
            name: {"sha256": hashlib.sha256(name.encode()).hexdigest(), "bytes": 1}
            for name in ("audio.wav", "timing.json", "presentation.json", "../escape")
        }
        manifest = {
            "protocol": transport.PROTOCOL, "request_id": str(uuid.uuid4()),
            "source_id": SOURCE_ID, "duration_seconds": 1, "public_publish_enabled": False,
            "files": files,
        }
        with self.assertRaisesRegex(worker.WorkerJobError, "UNSAFE_FILE_MANIFEST"):
            worker._parse_manifest(json.dumps(manifest).encode())
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "real").mkdir()
            (root / "linked").symlink_to(root / "real", target_is_directory=True)
            with self.assertRaises(ValueError):
                worker._tree_sha256(root / "linked")

    def test_worker_rejects_wrong_bearer_token(self):
        class FakeServer:
            shared_token = TOKEN

        handler = object.__new__(worker.RenderHandler)
        handler.headers = {"Authorization": "Bearer " + "z" * 43}
        handler.server = FakeServer()
        self.assertFalse(handler._authorized())
        handler.headers = {"Authorization": "Bearer " + TOKEN}
        self.assertTrue(handler._authorized())
        handler.headers = {"Authorization": "Bearer café"}
        self.assertFalse(handler._authorized())

    def test_loopback_http_health_route_authenticates_and_closes_connections(self):
        server = worker.SingleRequestHTTPServer(("127.0.0.1", 0), worker.RenderHandler)
        server.readiness = {"protocol": transport.PROTOCOL, "status": "READY"}
        server.paths = {}
        server.shared_token = TOKEN
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            self.assertEqual(server.server_address[0], "127.0.0.1")
            for token, expected_status in ((TOKEN, 200), ("z" * 43, 401)):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                connection.request("GET", "/healthz", headers={"Authorization": f"Bearer {token}"})
                response = connection.getresponse()
                payload = json.loads(response.read())
                self.assertEqual(response.status, expected_status)
                if expected_status == 200:
                    self.assertEqual(payload["protocol"], transport.PROTOCOL)
                else:
                    self.assertEqual(payload["error_code"], "UNAUTHORIZED")
                connection.close()
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_remote_render_is_explicit_and_never_falls_back_to_local_renderer(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, _asset_environment()):
            package = Path(td) / SOURCE_ID
            package.mkdir()
            request_id = str(uuid.uuid4())
            manifest = {"request_id": request_id, "worker_code_sha256": {"renderer": "x"}}
            def make_result(_archive, dest, **_kwargs):
                video = dest / "final.mp4"
                video.write_bytes(b"verified-mock-video")
                return {"video_path": video, "report": {"source_id": SOURCE_ID},
                        "manifest": {"video_sha256": hashlib.sha256(b"verified-mock-video").hexdigest()}}
            def fake_send(_port, _token, _archive, response, **_kwargs):
                response.write_bytes(b"fake-response")
            with patch.object(transport, "_read_health", return_value={"status": "READY"}), \
                 patch.object(transport, "_validate_health"), \
                 patch.object(transport, "create_request_archive", return_value=manifest), \
                 patch.object(transport, "_send_archive", side_effect=fake_send), \
                 patch.object(transport, "_extract_response", side_effect=make_result), \
                 patch.object(transport, "_verify_video", return_value={"duration_seconds": 1, "streams": ["audio", "video"], "bytes": 20}), \
                 patch.object(transport.subprocess, "run", side_effect=AssertionError("local ffmpeg fallback")):
                result = transport.dispatch_remote_render(
                    package=package, source_id=SOURCE_ID, presentation_path=package / "presentation.json",
                    timing_path=package / "timing.json", assets=[], duration_seconds=1,
                )
            self.assertEqual(result["status"], "READY_TO_PUBLISH")
            self.assertEqual(result["render_route"], "SSH_REVERSE_TUNNEL")
            self.assertTrue((package / "final.mp4").is_file())
            self.assertTrue((package / "remote-render-report.json").is_file())
            self.assertFalse(result["public_publish_enabled"])

    def test_pipeline_advances_state_only_after_remote_result(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            package = root / SOURCE_ID
            package.mkdir()
            (package / "image-candidates.json").write_text("{}", encoding="utf-8")
            (package / "mission.json").write_text(json.dumps({
                "source_id": SOURCE_ID, "title": "Title", "source_url": "https://openai.com/news/x",
                "scenes": [{"scene_id": "scene-1"}],
            }), encoding="utf-8")
            (package / "timing.json").write_text(json.dumps({
                "total_duration": 1, "voicevox_credit": ["VOICEVOX"],
                "records": [{"scene_id": "scene-1", "start": 0, "end": 1, "caption_text": "line"}],
            }), encoding="utf-8")
            assets = [
                {"id": "visual-1", "file": str(package / "images" / "a.png"), "credit": "Credit A", "url": "https://openai.com/a.png"},
                {"id": "visual-2", "file": str(package / "images" / "b.png"), "credit": "Credit B", "url": "https://openai.com/b.png"},
            ]
            connection = sqlite3.connect(":memory:")
            connection.row_factory = sqlite3.Row
            connection.execute("CREATE TABLE source_inbox (source_id TEXT PRIMARY KEY, state TEXT, updated_at REAL)")
            connection.execute("INSERT INTO source_inbox VALUES (?, 'ASSET_REVIEW_REQUIRED', 0)", (SOURCE_ID,))
            args = SimpleNamespace(workspace=root, package=package, remote_render=True, worker_url=None, shell=None, font=None)
            with patch.object(media_news_pipeline, "_validate_existing_package", return_value=package), \
                 patch.object(media_news_pipeline, "select_render_assets", return_value=assets), \
                 patch.object(media_news_pipeline, "dispatch_remote_render", return_value={"status": "READY_TO_PUBLISH", "public_publish_enabled": False}) as dispatch, \
                 patch.object(media_news_pipeline.subprocess, "run", side_effect=AssertionError("local fallback")):
                result = media_news_pipeline._render_package(args, connection)
            self.assertEqual(result["status"], "READY_TO_PUBLISH")
            self.assertEqual(dispatch.call_count, 1)
            self.assertEqual(connection.execute("SELECT state FROM source_inbox").fetchone()[0], "READY_TO_PUBLISH")
            connection.execute("UPDATE source_inbox SET state='ASSET_REVIEW_REQUIRED'")
            with patch.object(media_news_pipeline, "_validate_existing_package", return_value=package), \
                 patch.object(media_news_pipeline, "select_render_assets", return_value=assets), \
                 patch.object(media_news_pipeline, "dispatch_remote_render", side_effect=transport.RenderTransportError("offline")), \
                 patch.object(media_news_pipeline.subprocess, "run", side_effect=AssertionError("local fallback")):
                with self.assertRaises(transport.RenderTransportError):
                    media_news_pipeline._render_package(args, connection)
            self.assertEqual(connection.execute("SELECT state FROM source_inbox").fetchone()[0], "ASSET_REVIEW_REQUIRED")
            connection.close()


if __name__ == "__main__":
    unittest.main()
