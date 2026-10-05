import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

from scripts.video_build_job import build_job


class VideoBuildJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "out"
        self.cache = self.root / "shared-cache"

    def test_second_job_cannot_compete_for_same_worker(self):
        with build_job(self.output, self.cache, "one", "hash"):
            with self.assertRaisesRegex(RuntimeError, "already active"):
                with build_job(self.root / "another", self.cache, "two", "hash"):
                    self.fail("second heavy worker entered")

    def test_same_output_is_locked_even_with_different_cache_roots(self):
        with build_job(self.output, self.cache, "one", "hash"):
            with self.assertRaisesRegex(RuntimeError, "already active"):
                with build_job(self.output, self.root / "another-cache", "one", "hash"):
                    self.fail("second writer entered")

    def test_failed_admission_keeps_preparation_for_resume(self):
        with self.assertRaisesRegex(RuntimeError, "admission failed"):
            with build_job(self.output, self.cache, "one", "hash") as job:
                job.stage("VOICEVOX")
                audio = self.output / "audio.wav"
                audio.write_bytes(b"verified prepared audio")
                job.checkpoint("PREPARATION", [audio])
                job.stage("CONTENT_ADMISSION")
                raise RuntimeError("admission failed")
        state = json.loads((self.output / "STATE.json").read_text())
        self.assertEqual(state["error_stage"], "CONTENT_ADMISSION")
        with build_job(self.output, self.cache, "one", "hash") as job:
            self.assertTrue(job.reusable("PREPARATION"))
            job.complete()
        self.assertEqual(json.loads((self.output / "STATE.json").read_text())["state"], "READY_TO_PUBLISH_INTERNAL_ONLY")

    def test_changed_input_is_rejected_before_work(self):
        with build_job(self.output, self.cache, "one", "hash") as job:
            job.stage("REGISTERED")
        for identity, digest in [("two", "hash"), ("one", "changed")]:
            with self.assertRaisesRegex(RuntimeError, "another content run or mission"):
                with build_job(self.output, self.cache, identity, digest):
                    self.fail("changed input entered")

    def test_corrupt_preparation_is_not_reused(self):
        with build_job(self.output, self.cache, "one", "hash") as job:
            audio = self.output / "audio.wav"
            audio.write_bytes(b"good")
            job.checkpoint("PREPARATION", [audio])
            audio.write_bytes(b"bad")
            self.assertFalse(job.reusable("PREPARATION"))

    def test_builder_resumes_preparation_without_voice_or_download(self):
        from scripts import build_gemini4_argon_cloud_video as builder
        from scripts.gemini_video_director import save_json
        import hashlib
        self.output.mkdir()
        mission = self.root / "mission.json"
        save_json(mission, {"title": "Topic", "content_run_id": "one"})
        digest = hashlib.sha256(mission.read_bytes()).hexdigest()
        audio = self.output / "audio.wav"
        audio.write_bytes(b"prepared")
        visual = self.output / "visual.png"
        visual.write_bytes(b"prepared visual")
        timing = self.output / "timing.json"
        presentation = self.output / "presentation.json"
        save_json(timing, {"total_duration": 600})
        save_json(presentation, {"visuals": [{"file": str(visual)}]})
        args = SimpleNamespace(mission=mission, output_dir=self.output, cache_root=self.cache,
                               content_run_id="one", voicevox_url="http://127.0.0.1:50021")
        with build_job(self.output, self.cache, "one", digest) as job:
            job.checkpoint("PREPARATION", [audio, timing, presentation, visual])
            with mock.patch.object(builder, "_mission_for_voice", return_value={"content_run_id": "one"}), \
                 mock.patch.object(builder, "_synthesize", side_effect=AssertionError("no synthesis")), \
                 mock.patch.object(builder, "_prepare_visuals", side_effect=AssertionError("no download")), \
                 mock.patch.object(builder.subprocess, "run", side_effect=RuntimeError("admission stopped")):
                with self.assertRaisesRegex(RuntimeError, "admission stopped"):
                    builder._build(args, job)

    def test_pcm_duration_needs_no_ffprobe_process(self):
        import wave
        from scripts.synthesize_longform_voicevox import duration
        path = self.root / "voice.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\0" * 48000 * 4)
        with mock.patch("subprocess.check_output", side_effect=AssertionError("no ffprobe")):
            self.assertEqual(duration(path), 1.0)


if __name__ == "__main__":
    unittest.main()
