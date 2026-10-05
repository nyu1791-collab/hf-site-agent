"""Exercise early failure and cleanup without cloud calls or video rendering."""
import io
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import wave

from scripts import media_voice_cache as cache
from scripts import synthesize_longform_voicevox as voice
from scripts.media_encoder_process import encoder_process, interrupt_encoder


class MediaFailureBoundaryTests(unittest.TestCase):
    def test_encoder_exits_and_is_reaped_after_normal_stream_end(self):
        with encoder_process([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"] ) as child:
            child.stdin.write(b"frames")
        self.assertEqual(child.poll(), 0)
        self.assertTrue(child.stdin.closed)

    def test_encoder_is_stopped_when_frame_production_fails(self):
        with self.assertRaisesRegex(ValueError, "frame failure"):
            with encoder_process([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"] ) as child:
                raise ValueError("frame failure")
        self.assertIsNotNone(child.poll())
        self.assertTrue(child.stdin.closed)

    def test_encoder_flush_timeout_stops_child(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            with encoder_process([sys.executable, "-c", "import time; time.sleep(100)"], flush_timeout=0.01) as child:
                pass
        self.assertIsNotNone(child.poll())

    def test_sigterm_runs_encoder_cleanup(self):
        with self.assertRaises(SystemExit) as error:
            with encoder_process([sys.executable, "-c", "import sys; sys.stdin.buffer.read()"] ) as child:
                interrupt_encoder(signal.SIGTERM, None)
        self.assertEqual(error.exception.code, 143)
        self.assertIsNotNone(child.poll())

    def test_unresponsive_child_is_killed_after_grace_period(self):
        child = mock.Mock(stdin=io.BytesIO())
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired("encoder", 3), -9]
        with mock.patch("scripts.media_encoder_process.subprocess.Popen", return_value=child):
            with self.assertRaisesRegex(ValueError, "frame failure"):
                with encoder_process(["encoder"]):
                    raise ValueError("frame failure")
        child.terminate.assert_called_once()
        child.kill.assert_called_once()

    def test_voice_cache_copies_the_verified_snapshot_even_if_file_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            with wave.open(str(source), "wb") as audio:
                audio.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
                audio.writeframes(b"\0" * 400)
            original = source.read_bytes()
            key = cache.voice_cache_key(text="text", engine_version="test", style_id=3, speed_scale=1.2)
            cache.store_voice(root / "cache", key, source)
            stored_audio, _ = cache._paths(root / "cache", key)
            original_validate = cache._wav_receipt_bytes
            def replace_after_validation(data):
                result = original_validate(data)
                stored_audio.write_bytes(b"changed after validation")
                return result
            with mock.patch.object(cache, "_wav_receipt_bytes", side_effect=replace_after_validation):
                receipt = cache.restore_voice(root / "cache", key, root / "out.wav")
            self.assertIsNotNone(receipt)
            self.assertEqual((root / "out.wav").read_bytes(), original)

    def test_bad_later_dialogue_is_rejected_before_engine_or_first_synthesis(self):
        mission = {"mission_id": "test", "title": "test", "scenes": [{"scene_id": "s1", "dialogue": [
            {"id": "L1", "speaker": "ずんだもん", "voice_text": "first"},
            {"id": "L2", "speaker": "unknown", "voice_text": "later invalid"}]}]}
        args = ["synthesize", "--mission-b64", "unused", "--output-dir", "unused", "--timing-out", "unused"]
        with mock.patch("sys.argv", args), mock.patch.object(voice, "decode_mission", return_value=mission), \
             mock.patch.object(voice, "discover_cast", side_effect=AssertionError("no engine calls")):
            with self.assertRaisesRegex(ValueError, "outside standard cast"):
                voice.main()

    def test_invalid_duration_bounds_fail_before_mission_or_engine(self):
        args = ["synthesize", "--mission-b64", "unused", "--output-dir", "unused", "--timing-out", "unused", "--min-seconds", "nan"]
        with mock.patch("sys.argv", args), mock.patch.object(voice, "decode_mission", side_effect=AssertionError("no work")), \
             mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                voice.main()


if __name__ == "__main__":
    unittest.main()
