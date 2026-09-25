from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.batch_media_scheduler import MediaJob, MediaJobFailure, ResourceVector
from scripts.media_batch_command_center import (
    SCHEMA_VERSION,
    build_plan,
    lock_manifest,
    render_clip,
    sha256_file,
    validate_output_contract,
)


def valid_probe(duration: float = 3.0) -> dict:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "width": 1080,
                "height": 1920,
                "avg_frame_rate": "30/1",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
            },
        ],
        "format": {"duration": str(duration)},
    }


class MediaBatchCommandCenterTests(unittest.TestCase):
    def test_lock_manifest_freezes_exact_input_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.mp4"
            source.write_bytes(b"media-source")
            manifest = root / "batch.json"
            manifest.write_text(json.dumps({
                "schema_version": SCHEMA_VERSION,
                "jobs": [{
                    "job_id": "clip-a",
                    "input_path": "source.mp4",
                    "rights_verified": True,
                    "start_seconds": 0,
                    "duration_seconds": 3,
                    "output_name": "clip-a.mp4",
                }],
            }), encoding="utf-8")
            locked_path = root / "batch.lock.json"
            locked = lock_manifest(manifest, locked_path)
            self.assertEqual(locked["jobs"][0]["input_sha256"], hashlib.sha256(b"media-source").hexdigest())
            self.assertEqual(json.loads(locked_path.read_text())["jobs"][0]["input_sha256"], sha256_file(source))

    def test_plan_blocks_unverified_rights_without_blocking_other_job(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.mp4"
            source.write_bytes(b"same-source")
            digest = sha256_file(source)
            manifest_path = root / "batch.lock.json"
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "jobs": [
                    {
                        "job_id": "allowed",
                        "input_path": "source.mp4",
                        "input_sha256": digest,
                        "rights_verified": True,
                        "start_seconds": 0,
                        "duration_seconds": 3,
                        "output_name": "allowed.mp4",
                    },
                    {
                        "job_id": "blocked",
                        "input_path": "source.mp4",
                        "input_sha256": digest,
                        "rights_verified": False,
                        "start_seconds": 0,
                        "duration_seconds": 3,
                        "output_name": "blocked.mp4",
                    },
                ],
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            plan = build_plan(manifest_path, manifest, root / "out", max_parallel=3)
            self.assertEqual(plan["status"], "BLOCKED")
            self.assertEqual(plan["runnable"], ["allowed"])
            self.assertEqual(plan["rights_blocked"], ["blocked"])

    def test_output_contract_accepts_short_form_standard(self):
        contract = validate_output_contract(valid_probe(3.02), layout="vertical-fit", requested_duration=3.0)
        self.assertEqual(contract["video"]["width"], 1080)
        self.assertEqual(contract["video"]["height"], 1920)
        self.assertEqual(contract["audio"]["sample_rate"], 48000)

    def test_output_contract_rejects_wrong_codec(self):
        probe = valid_probe()
        probe["streams"][0]["codec_name"] = "vp9"
        with self.assertRaises(MediaJobFailure):
            validate_output_contract(probe, layout="vertical-fit", requested_duration=3.0)

    def test_render_rolls_back_previous_good_video_if_manifest_commit_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            output = root / "clip.mp4"
            output.write_bytes(b"previous-good")
            job = MediaJob(
                job_id="clip-a",
                input_ref=str(source),
                input_sha256=sha256_file(source),
                rights_verified=True,
                demand=ResourceVector(cpu_slots=1, memory_mb=256, disk_mb=256, provider_slots=0),
                metadata={
                    "source": str(source),
                    "output_dir": str(root),
                    "output_name": "clip.mp4",
                    "start_seconds": 0.0,
                    "duration_seconds": 3.0,
                    "layout": "vertical-fit",
                },
            )

            def fake_run(cmd, timeout=900):
                Path(cmd[-1]).write_bytes(b"new-validated")
                return 0, ""

            source_probe = {"streams": [{"codec_type": "video"}, {"codec_type": "audio"}], "format": {"duration": "8"}}
            with mock.patch("scripts.media_batch_command_center._run_capture", side_effect=fake_run), \
                 mock.patch("scripts.media_batch_command_center._probe", side_effect=[source_probe, valid_probe(3.0)]), \
                 mock.patch("scripts.media_batch_command_center.atomic_write_json", side_effect=OSError("disk error")):
                with self.assertRaises(OSError):
                    render_clip(job, ffmpeg="ffmpeg", ffprobe="ffprobe")
            self.assertEqual(output.read_bytes(), b"previous-good")


if __name__ == "__main__":
    unittest.main()
