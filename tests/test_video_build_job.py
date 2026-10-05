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
            completed_at = job.state["completed_at"]
            job.complete()
            self.assertEqual(job.state["completed_at"], completed_at)
            self.assertNotIn("error_type", job.state)
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
            job.checkpoint("PREPARATION", [audio, timing, presentation, visual], dependency=builder._checkpoint_dependencies()["PREPARATION"])
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

    def test_voice_checkpoint_survives_visual_failure_without_engine_access(self):
        from scripts import build_gemini4_argon_cloud_video as builder
        from scripts.gemini_video_director import save_json
        import hashlib
        self.output.mkdir()
        mission = self.root / "mission.json"
        save_json(mission, {"title": "Topic", "content_run_id": "one"})
        digest = hashlib.sha256(mission.read_bytes()).hexdigest()
        audio = self.output / "audio.wav"
        audio.write_bytes(b"prepared")
        timing = self.output / "voice-timing.json"
        save_json(timing, {"total_duration": 600, "records": []})
        args = SimpleNamespace(mission=mission, output_dir=self.output, cache_root=self.cache,
                               content_run_id="one", voicevox_url="http://127.0.0.1:50021")
        with build_job(self.output, self.cache, "one", digest) as job:
            job.checkpoint("VOICE_AUDIO", [audio, timing], dependency=builder._checkpoint_dependencies()["VOICE_AUDIO"])
            with mock.patch.object(builder, "_mission_for_voice", return_value={"content_run_id": "one"}), \
                 mock.patch.object(builder, "_synthesize", side_effect=AssertionError("no engine access")), \
                 mock.patch.object(builder, "_prepare_visuals", side_effect=RuntimeError("visual failure")):
                with self.assertRaisesRegex(RuntimeError, "visual failure"):
                    builder._build(args, job)
            self.assertTrue(job.reusable("VOICE_AUDIO"))

    def test_encode_checkpoint_resumes_delivery_without_encoder_or_preparation(self):
        from scripts import build_gemini4_argon_cloud_video as builder
        from scripts.gemini_video_director import save_json
        import hashlib
        self.output.mkdir()
        mission = self.root / "mission.json"
        save_json(mission, {"title": "Topic", "content_run_id": "one"})
        digest = hashlib.sha256(mission.read_bytes()).hexdigest()
        output = self.output / "Gemini4_Argon_landscape.mp4"
        output.write_bytes(b"finished encode")
        args = SimpleNamespace(mission=mission, output_dir=self.output, content_run_id="one")
        probe = {"format": {"duration": "600"}, "streams": [
            {"codec_type": "video", "width": 1280, "height": 720}, {"codec_type": "audio"}]}
        with build_job(self.output, self.cache, "one", digest) as job:
            job.checkpoint("FINAL_ENCODE", [output])
            with mock.patch.object(builder.subprocess, "run", side_effect=AssertionError("no encoder")), \
                 mock.patch.object(builder, "_mission_for_voice", side_effect=AssertionError("no preparation")), \
                 mock.patch.object(builder.subprocess, "check_output", return_value=json.dumps(probe)) as ffprobe, \
                 mock.patch("builtins.print"):
                self.assertEqual(builder._build(args, job), 0)
                self.assertEqual(builder._build(args, job), 0)
                ffprobe.assert_called_once()
        completion = json.loads((self.output / "completion.json").read_text())
        self.assertTrue(completion["recovered_after_encode"])
        self.assertEqual(completion["final_video_encode_count"], 1)

    def test_official_image_cache_reuses_verified_bytes_across_jobs(self):
        from scripts import build_gemini4_argon_cloud_video as builder
        from PIL import Image
        import io
        data = io.BytesIO()
        Image.new("RGB", (10, 10), "white").save(data, format="PNG")
        first = self.root / "first.png"
        second = self.root / "second.png"
        with mock.patch.object(builder.urllib.request, "urlopen", return_value=io.BytesIO(data.getvalue())) as request:
            self.assertTrue(builder._download_official("https://example.com/official.png", first, self.cache))
            self.assertTrue(builder._download_official("https://example.com/official.png", second, self.cache))
            request.assert_called_once()
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_dependency_change_invalidates_only_affected_checkpoint(self):
        with build_job(self.output, self.cache, "one", "hash") as job:
            audio = self.output / "audio.wav"
            audio.write_bytes(b"audio")
            job.checkpoint("VOICE_AUDIO", [audio], dependency="voice-v1")
            job.checkpoint("PREPARATION", [audio], dependency="visual-v1")
            self.assertTrue(job.reusable("VOICE_AUDIO", dependency="voice-v1"))
            self.assertFalse(job.reusable("PREPARATION", dependency="visual-v2"))
            self.assertFalse(job.reusable("VOICE_AUDIO", dependency="voice-v2"))

    def test_failed_state_write_does_not_mask_original_failure(self):
        with self.assertRaisesRegex(ValueError, "original failure"):
            with mock.patch("scripts.video_build_job.save_json", side_effect=OSError("disk full")):
                with build_job(self.output, self.cache, "one", "hash"):
                    raise ValueError("original failure")

    def test_preflight_cli_does_not_register_a_job_or_call_builder(self):
        import io
        from contextlib import redirect_stdout
        from scripts import build_gemini4_argon_cloud_video as builder
        mission = self.root / "mission.json"
        mission.write_text(json.dumps({"scenes": [{"dialogue": [{"text": "台詞"}]}]}))
        args = ["builder", "--preflight-only", "--mission", str(mission), "--content-run-id", "one",
                "--shell-root", str(self.root), "--output-dir", str(self.output), "--cache-root", str(self.cache)]
        out = io.StringIO()
        with mock.patch("sys.argv", args), redirect_stdout(out), \
             mock.patch.object(builder, "_preflight_inputs") as preflight, \
             mock.patch.object(builder, "_build", side_effect=AssertionError("no production")):
            self.assertEqual(builder.main(), 0)
        preflight.assert_called_once()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.cache.exists())
        self.assertEqual(json.loads(out.getvalue())["script_characters"], 2)

    def test_unknown_visual_scene_is_rejected_before_asset_or_voice_work(self):
        from scripts import build_gemini4_argon_cloud_video as builder
        with self.assertRaisesRegex(ValueError, "unsupported scene"):
            builder._preflight_inputs({"scenes": [{"scene_id": "unconfigured"}]}, SimpleNamespace())


if __name__ == "__main__":
    unittest.main()
