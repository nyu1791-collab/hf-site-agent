import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.media_news_pipeline import _render_asset_from_env


ROOT = Path(__file__).resolve().parents[1]


class MediaSmallHostDeploymentTests(unittest.TestCase):
    def test_profile_is_single_vm_on_demand_and_preserves_existing_queue(self):
        policy = json.loads((ROOT / "config/media_small_host_policy.json").read_text())
        self.assertEqual(policy["target"]["machine_type"], "e2-small")
        self.assertEqual(policy["target"]["memory_gib"], 2)
        self.assertFalse(policy["target"]["gpu_required"])
        self.assertEqual(policy["resource_controls"]["minimum_workspace_free_bytes"], 2 * 1024**3)
        self.assertFalse(policy["resource_controls"]["automatic_artifact_deletion"])
        self.assertTrue(policy["runtime_layout"]["move_or_reinitialize_existing_database"] is False)
        self.assertTrue(policy["execution"]["render_timer_enabled_by_default"] is False)
        self.assertFalse(policy["provider_and_cost_gates"]["paid_fallback_allowed"])

    def test_user_render_unit_runs_local_oneshot_on_existing_runtime_database(self):
        path = ROOT / "deploy/systemd/user/hf-site-agent-media-render@.service"
        unit = path.read_text()
        self.assertIn("Type=oneshot", unit)
        self.assertIn("runtime/media-queue.sqlite3", unit)
        self.assertIn("render --package %h/hf-site-agent/runtime/media-news/%i", unit)
        self.assertNotIn("--remote-render", unit)
        self.assertIn("MemoryMax=1700M", unit)

    def test_user_preparation_timer_is_separate_from_existing_rss_poller(self):
        service = (ROOT / "deploy/systemd/user/hf-site-agent-media-news.service").read_text()
        timer = (ROOT / "deploy/systemd/user/hf-site-agent-media-news.timer").read_text()
        self.assertIn("process-next", service)
        self.assertIn("VV_CPU_NUM_THREADS=1", service)
        self.assertIn("VOICEVOX_CACHE_DIR=%h/hf-site-agent/runtime/voice-cache", service)
        self.assertIn("OnUnitInactiveSec=5min", timer)
        source_service = (ROOT / "deploy/systemd/hf-site-agent-media-source.service").read_text()
        self.assertIn("--interval-seconds 300", source_service)

    def test_local_render_shell_and_font_can_come_from_protected_environment(self):
        with patch.dict(os.environ, {"MEDIA_RENDER_SHELL": "~/approved/shell",
                                     "MEDIA_RENDER_FONT": "/srv/fonts/approved.ttf"}):
            self.assertEqual(_render_asset_from_env("MEDIA_RENDER_SHELL"), Path.home() / "approved/shell")
            self.assertEqual(_render_asset_from_env("MEDIA_RENDER_FONT"), Path("/srv/fonts/approved.ttf"))
        with patch.dict(os.environ, {"MEDIA_RENDER_SHELL": ""}):
            self.assertIsNone(_render_asset_from_env("MEDIA_RENDER_SHELL"))


if __name__ == "__main__":
    unittest.main()
