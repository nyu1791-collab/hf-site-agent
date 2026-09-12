from __future__ import annotations

import unittest

from scripts.replace_narration_audio import NarrationReplaceError, build_ffmpeg_command


class ReplaceNarrationAudioTests(unittest.TestCase):
    def test_command_copies_video_normalizes_audio_and_stops_at_master_duration(self):
        command = build_ffmpeg_command(
            video_path="master.mp4",
            narration_path="voice.wav",
            output_path="out.mp4",
            video_duration=61.0,
        )
        joined = " ".join(command)
        self.assertIn("-c:v copy", joined)
        self.assertIn("loudnorm=I=-16.0:TP=-1.5", joined)
        self.assertIn("atrim=0:61.000", joined)
        self.assertIn("-shortest", command)
        self.assertEqual(command[-1], "out.mp4")

    def test_invalid_duration_fails_closed(self):
        with self.assertRaises(NarrationReplaceError):
            build_ffmpeg_command(
                video_path="master.mp4",
                narration_path="voice.wav",
                output_path="out.mp4",
                video_duration=0,
            )


if __name__ == "__main__":
    unittest.main()
