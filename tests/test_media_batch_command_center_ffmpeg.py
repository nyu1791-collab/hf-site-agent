from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.media_batch_command_center import SCHEMA_VERSION, execute, sha256_file


FFMPEG_READY = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@unittest.skipUnless(FFMPEG_READY, "ffmpeg/ffprobe required")
class MediaBatchCommandCenterFfmpegTests(unittest.TestCase):
    def test_three_real_clips_finish_with_machine_gate(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            jobs = []
            for index, frequency in enumerate((440, 550, 660), start=1):
                source = root / f"source-{index}.mp4"
                subprocess.run([
                    shutil.which("ffmpeg") or "ffmpeg",
                    "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=30:duration=1.2",
                    "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=1.2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", "48000", "-ac", "2",
                    "-shortest", str(source),
                ], check=True, timeout=60)
                jobs.append({
                    "job_id": f"clip-{index}",
                    "input_path": source.name,
                    "input_sha256": sha256_file(source),
                    "rights_verified": True,
                    "start_seconds": 0.1,
                    "duration_seconds": 0.8,
                    "output_name": f"clip-{index}.mp4",
                    "layout": "vertical-fit",
                    "resource_demand": {
                        "cpu_slots": 1,
                        "memory_mb": 256,
                        "disk_mb": 256,
                        "provider_slots": 0
                    }
                })

            manifest_path = root / "batch.lock.json"
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "output_dir": "out",
                "jobs": jobs,
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with mock.patch("scripts.media_batch_command_center.os.cpu_count", return_value=3):
                report = execute(manifest_path, manifest, root / "out", max_parallel=3, state="NORMAL")

            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["ready_count"], 3)
            self.assertEqual(report["effective_cpu_slots"], 3)
            self.assertEqual(report["paid_calls"], 0)
            self.assertEqual(report["external_video_saas_calls"], 0)
            for index in range(1, 4):
                self.assertTrue((root / "out" / f"clip-{index}.mp4").is_file())
                sidecar = json.loads((root / "out" / f"clip-{index}.mp4.manifest.json").read_text())
                self.assertEqual(sidecar["contract"]["video"]["width"], 1080)
                self.assertEqual(sidecar["contract"]["video"]["height"], 1920)
                self.assertEqual(sidecar["contract"]["audio"]["sample_rate"], 48000)


if __name__ == "__main__":
    unittest.main()
