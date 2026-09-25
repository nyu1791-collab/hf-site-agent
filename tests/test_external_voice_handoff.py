from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import wave

from scripts.external_voice_handoff import VoiceHandoffError, build_handoff
from scripts.free_voice_router import select_voice_route


class ExternalVoiceHandoffTests(unittest.TestCase):
    def _wav(self, directory: str) -> Path:
        path = Path(directory) / "voice.wav"
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x00" * 1600)
        return path

    def test_user_supplied_app_voice_is_edit_ready_without_publish_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build_handoff(
                audio_path=self._wav(tmp),
                source_type="TIKTOK_EDITOR",
                voice_name="approved-app-voice",
                voice_terms_verified=True,
                credit_requirements_satisfied=True,
                commercial_use_verified=False,
            )
        self.assertTrue(report["edit_ready"])
        self.assertFalse(report["publish_ready"])
        selection = select_voice_route(report["route_evidence"])
        self.assertEqual(selection["selected"], "USER_SUPPLIED_APP_VOICE")
        self.assertTrue(selection["edit_ready"])
        self.assertFalse(selection["publish_ready"])
        self.assertIn("commercial_use_verified", selection["publish_blockers"])

    def test_commercially_verified_handoff_can_clear_publish_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = build_handoff(
                audio_path=self._wav(tmp),
                source_type="VOICEVOX",
                voice_name="ずんだもん",
                voice_terms_verified=True,
                credit_requirements_satisfied=True,
                commercial_use_verified=True,
                attribution_text="VOICEVOX:ずんだもん",
            )
        selection = select_voice_route(report["route_evidence"])
        self.assertTrue(selection["publish_ready"])
        self.assertEqual(report["next_stage"], "SUBTITLE_ALIGNMENT_AND_AUDIO_QA")

    def test_paid_generation_and_unknown_source_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = self._wav(tmp)
            with self.assertRaises(VoiceHandoffError):
                build_handoff(
                    audio_path=audio,
                    source_type="UNKNOWN",
                    voice_name="x",
                    voice_terms_verified=True,
                    credit_requirements_satisfied=True,
                    commercial_use_verified=True,
                )
            with self.assertRaises(VoiceHandoffError):
                build_handoff(
                    audio_path=audio,
                    source_type="CAPCUT",
                    voice_name="x",
                    voice_terms_verified=True,
                    credit_requirements_satisfied=True,
                    commercial_use_verified=True,
                    no_paid_generation_used=False,
                )


if __name__ == "__main__":
    unittest.main()
