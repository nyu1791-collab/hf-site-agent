"""Offline regression tests; never invoke Gemini, VOICEVOX, or an encoder."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import durable_gemini_research as worker
from scripts import gemini_video_director as director
from scripts.build_gemini4_argon_cloud_video import _completed_job


class VideoCheckpointRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = self.root / "plan.json"
        director.save_json(self.plan, {
            "topic": "Topic",
            "items": [{"item_id": "topic", "title": "Topic",
                       "youtube_urls": [f"https://youtu.be/video{i}" for i in range(3)]}],
        })
        self.job = self.root / "job"
        worker.initialize(self.job, "test-run", self.plan)

    def seed_cache(self):
        cache = self.job / "cache/gemini-video-director"
        analyses = []
        for url in json.loads(self.plan.read_text())["items"][0]["youtube_urls"]:
            analysis = {"source_url": url, "model": director.DEFAULT_MODEL, "summary": "cached",
                        **{field: [] for field in ("takeaways", "timestamps", "visual_beats", "script_notes", "material_limits")}}
            analyses.append(analysis)
            key = director.stable_cache_key(model=director.DEFAULT_MODEL, source_url=url,
                                            topic="Topic", item_id="topic")
            director.save_json(cache / "sources" / (key + ".json"), analysis)
        key = director.synthesis_cache_key(model=director.DEFAULT_MODEL, topic="Topic",
                                           item_id="topic", analyses=analyses)
        director.save_json(cache / "synthesis" / (key + ".json"), self.synthesis())

    def synthesis(self):
        return {"editorial_summary": "cached", **{field: [] for field in (
            "selected_takeaways", "best_source_moments", "dialogue_plan", "scene_plan", "conflicts_or_uncertainty")}}

    def test_all_cached_recovery_needs_no_client_or_provider(self):
        self.seed_cache()
        with mock.patch.object(director, "create_client", side_effect=AssertionError("no ADC/client")):
            state = worker.run_job(self.job)
            self.assertEqual(state["state"], "RESEARCH_READY")
            # Repeated status/recovery must keep the same ready checkpoint.
            self.assertEqual(worker.run_job(self.job), state)
        package = json.loads((self.job / "gemini_research_package.json").read_text())
        self.assertEqual(package["cache_hits"], 3)
        self.assertEqual(package["synthesis_cache_hits"], 1)
        self.assertEqual(package["content_run_id"], "test-run")

    def test_cached_sources_only_initialize_one_synthesis_client(self):
        self.seed_cache()
        for path in (self.job / "cache/gemini-video-director/synthesis").iterdir():
            path.unlink()
        with mock.patch.object(director, "create_client", return_value=object()) as client, \
             mock.patch.object(director, "synthesize_item", return_value=self.synthesis()) as synthesis, \
             mock.patch.object(director, "analyze_one", side_effect=AssertionError("do not reanalyze")):
            self.assertEqual(worker.run_job(self.job)["state"], "RESEARCH_READY")
        client.assert_called_once()
        synthesis.assert_called_once()

    def test_corrupt_inflight_checkpoint_stops_without_resending(self):
        path = self.job / "cache/gemini-video-director/sources/pending.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"partial":')
        state_path = self.job / "STATE.json"
        state = json.loads(state_path.read_text())
        state.update(state="RUNNING", current_stage="YOUTUBE_ANALYSIS", request_cache_path=str(path))
        director.save_json(state_path, state)
        with mock.patch.object(director, "run", side_effect=AssertionError("no resend")):
            self.assertEqual(worker.run_job(self.job)["state"], "UNKNOWN_RESULT")
            self.assertEqual(worker.run_job(self.job)["state"], "UNKNOWN_RESULT")

    def test_changed_config_topic_cannot_reuse_job_identity(self):
        config_path = self.job / "job.json"
        config = json.loads(config_path.read_text())
        config["topic"] = "Different topic"
        director.save_json(config_path, config)
        with self.assertRaisesRegex(RuntimeError, "IMMUTABLE_JOB_TOPIC_MISMATCH"):
            worker.run_job(self.job)

    def test_interrupted_json_write_preserves_prior_success(self):
        path = self.root / "success.json"
        director.save_json(path, {"success": True})
        with mock.patch.object(director.os, "replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                director.save_json(path, {"success": False})
        self.assertEqual(json.loads(path.read_text()), {"success": True})
        self.assertEqual(list(self.root.glob(".checkpoint-*")), [])

    def test_completed_artifact_must_match_saved_hash_and_size(self):
        output = self.root / "Gemini4_Argon_landscape.mp4"
        output.write_bytes(b"finished video")
        completion = {"status": "READY_TO_PUBLISH_INTERNAL_ONLY", "content_run_id": "test-run",
                      "mission_sha256": "mission", "bytes": output.stat().st_size,
                      "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
        director.save_json(self.root / "completion.json", completion)
        self.assertEqual(_completed_job(self.root, "test-run", "mission"), completion)
        # A same-size replacement must also fail, without triggering an encode.
        output.write_bytes(b"tampered video")
        with self.assertRaisesRegex(RuntimeError, "differs from its checkpoint"):
            _completed_job(self.root, "test-run", "mission")

    def test_existing_mp4_without_checkpoint_is_never_overwritten(self):
        output = self.root / "Gemini4_Argon_landscape.mp4"
        output.write_bytes(b"preserve")
        with self.assertRaisesRegex(RuntimeError, "recover explicitly"):
            _completed_job(self.root, "test-run", "mission")
        self.assertEqual(output.read_bytes(), b"preserve")

    def test_malformed_saved_analysis_stops_without_provider_call(self):
        self.seed_cache()
        cached = next((self.job / "cache/gemini-video-director/sources").iterdir())
        director.save_json(cached, [])
        with mock.patch.object(director, "create_client", side_effect=AssertionError("no automatic reanalysis")):
            self.assertEqual(worker.run_job(self.job)["state"], "FAILED")

    def test_wrong_saved_source_identity_stops_without_provider_call(self):
        self.seed_cache()
        cached = next((self.job / "cache/gemini-video-director/sources").iterdir())
        data = json.loads(cached.read_text())
        data["source_url"] = "https://youtu.be/different"
        director.save_json(cached, data)
        with mock.patch.object(director, "create_client", side_effect=AssertionError("no automatic reanalysis")):
            self.assertEqual(worker.run_job(self.job)["state"], "FAILED")


if __name__ == "__main__":
    unittest.main()
