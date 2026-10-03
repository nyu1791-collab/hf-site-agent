import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.media_news_pipeline import _render_asset_from_env


ROOT = Path(__file__).resolve().parents[1]


class MediaSmallHostDeploymentTests(unittest.TestCase):
    def test_profile_is_coordinator_only_for_video_rendering_and_preserves_existing_queue(self):
        policy = json.loads((ROOT / "config/media_small_host_policy.json").read_text())
        self.assertEqual(policy["target"]["machine_type"], "e2-small")
        self.assertEqual(policy["target"]["memory_gib"], 2)
        self.assertFalse(policy["target"]["gpu_required"])
        self.assertEqual(policy["resource_controls"]["minimum_workspace_free_bytes"], 2 * 1024**3)
        self.assertFalse(policy["resource_controls"]["automatic_artifact_deletion"])
        self.assertTrue(policy["runtime_layout"]["move_or_reinitialize_existing_database"] is False)
        self.assertFalse(policy["execution"]["coordinator_and_local_renderer_share_one_vm"])
        self.assertTrue(policy["execution"]["coordinator_only_for_video_rendering"])
        self.assertTrue(policy["execution"]["external_render_worker_required_for_video_completion"])
        self.assertFalse(policy["resource_controls"]["local_video_rendering_on_gcp_allowed"])
        self.assertTrue(policy["execution"]["render_timer_enabled_by_default"] is False)
        self.assertTrue(policy["execution"]["preparation_timer_enabled_by_default"])
        self.assertFalse(policy["execution"]["gcp_local_render_unit_allowed"])
        self.assertTrue(policy["execution"]["external_render_live_health_required"])
        self.assertFalse(policy["provider_and_cost_gates"]["paid_fallback_allowed"])
        self.assertEqual(policy["runner_control_plane"]["reboot_audit_operation"], "reboot_audit")
        self.assertIn("LATEST_REMOTE_HEAD", policy["runner_control_plane"]["reboot_audit_requires"])
        self.assertIn("AUTHORITATIVE_RSS_OBSERVED_AFTER_CURRENT_BOOT",
                      policy["runner_control_plane"]["reboot_audit_requires"])
        self.assertIn("AUTHORITATIVE_RSS_OBSERVED_AFTER_CURRENT_BOOT",
                      policy["verification"]["coordinator_24h_ready_requires"])
        self.assertIn("FFPROBE_SUCCESS", policy["verification"]["saved_e2e_result_requires"])

    def test_gcp_activation_does_not_install_local_render_unit(self):
        installer = (ROOT / "scripts/install_gcp_small_host_services.py").read_text()
        units_block = installer.split("UNITS = (", 1)[1].split(")", 1)[0]
        self.assertIn("hf-site-agent-media-source.service", units_block)
        self.assertIn("hf-site-agent-media-source.timer", units_block)
        self.assertIn("hf-site-agent-media-news.service", units_block)
        self.assertIn("hf-site-agent-media-news.timer", units_block)
        self.assertNotIn("hf-site-agent-media-render@.service", units_block)

    def test_user_render_service_is_remote_only(self):
        unit = (ROOT / "deploy/systemd/user/hf-site-agent-media-render@.service").read_text()
        check = (ROOT / "deploy/systemd/user/hf-site-agent-media-render-check.service").read_text()
        for text in (unit, check):
            self.assertIn("EnvironmentFile=%h/.config/hf-site-agent/media-render.env", text)
            active_sections = {
                line.strip().lower()
                for line in text.splitlines()
                if not line.lstrip().startswith("#")
            }
            self.assertNotIn("[install]", active_sections)
        self.assertIn("--remote-render", unit)
        self.assertNotIn("local render", unit.lower())
        self.assertNotIn(" --shell ", unit)
        self.assertNotIn(" --font ", unit)
        self.assertIn("scripts.media_render_transport --check", check)

    def test_user_rss_timer_is_separate_from_preparation_timer(self):
        service = (ROOT / "deploy/systemd/user/hf-site-agent-media-news.service").read_text()
        timer = (ROOT / "deploy/systemd/user/hf-site-agent-media-news.timer").read_text()
        source_service = (ROOT / "deploy/systemd/user/hf-site-agent-media-source.service").read_text()
        source_timer = (ROOT / "deploy/systemd/user/hf-site-agent-media-source.timer").read_text()
        self.assertIn("process-next", service)
        self.assertNotIn("scripts.media_source_daemon", service)
        self.assertIn("VV_CPU_NUM_THREADS=1", service)
        self.assertIn("VOICEVOX_CACHE_DIR=%h/hf-site-agent/runtime/voice-cache", service)
        self.assertIn("OnUnitInactiveSec=5min", timer)
        policy = json.loads((ROOT / "config/media_small_host_policy.json").read_text())
        self.assertEqual(policy["runtime_layout"]["rss_poller_schedule"], "hf-site-agent-media-source.timer")
        self.assertFalse(policy["runtime_layout"]["create_duplicate_source_poller"])
        self.assertIn("scripts.media_source_daemon", source_service)
        self.assertIn("--once", source_service)
        self.assertIn("OnUnitInactiveSec=5min", source_timer)
        self.assertIn("Persistent=true", source_timer)

    def test_render_asset_paths_can_come_from_protected_environment(self):
        with patch.dict(os.environ, {"MEDIA_RENDER_SHELL": "~/approved/shell",
                                     "MEDIA_RENDER_FONT": "/srv/fonts/approved.ttf"}):
            self.assertEqual(_render_asset_from_env("MEDIA_RENDER_SHELL"), Path.home() / "approved/shell")
            self.assertEqual(_render_asset_from_env("MEDIA_RENDER_FONT"), Path("/srv/fonts/approved.ttf"))
        with patch.dict(os.environ, {"MEDIA_RENDER_SHELL": ""}):
            self.assertIsNone(_render_asset_from_env("MEDIA_RENDER_SHELL"))


if __name__ == "__main__":
    unittest.main()
