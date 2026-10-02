import json
from pathlib import Path
import shutil
import tempfile
import unittest

from scripts.validate_gcp_coordinator_contract import validate


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "config/media_small_host_policy.json",
    "config/media_news_pipeline_policy.json",
    "config/media_render_worker_policy.json",
    "config/media_source_ingress_policy.json",
    "scripts/install_gcp_small_host_services.py",
    "deploy/systemd/user/hf-site-agent-media-news.service",
    "deploy/systemd/user/hf-site-agent-media-news.timer",
    "deploy/systemd/user/hf-site-agent-media-render@.service",
    "deploy/systemd/user/hf-site-agent-media-render-check.service",
    "deploy/systemd/hf-render-worker.service",
)


class GcpCoordinatorContractTests(unittest.TestCase):
    def _fixture(self, target: Path) -> None:
        for relative in FILES:
            source = ROOT / relative
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    def test_current_repository_contract_passes(self):
        self.assertEqual(validate(ROOT), [])

    def test_local_render_drift_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._fixture(root)
            path = root / "config/media_small_host_policy.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["resource_controls"]["local_video_rendering_on_gcp_allowed"] = True
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertIn("GCP_COORDINATOR_ONLY_BOUNDARY_INVALID", validate(root))

    def test_paid_route_budget_or_model_drift_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._fixture(root)
            path = root / "config/media_news_pipeline_policy.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["paid_script_generation"]["model"] = "unexpected/model"
            value["paid_script_generation"]["automatic_retry_after_request"] = True
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertIn("RESIDENT_NEWS_COST_AND_ROUTE_BOUNDARY_INVALID", validate(root))

    def test_render_transport_must_remain_loopback_and_unprivileged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._fixture(root)
            policy = root / "config/media_render_worker_policy.json"
            value = json.loads(policy.read_text(encoding="utf-8"))
            value["transport"]["worker_bind_host"] = "0.0.0.0"
            value["worker_authority"]["paid_operations"] = True
            policy.write_text(json.dumps(value), encoding="utf-8")
            blockers = validate(root)
            self.assertIn("RENDER_TRANSPORT_BOUNDARY_INVALID", blockers)
            self.assertIn("RENDER_WORKER_AUTHORITY_INVALID", blockers)

    def test_static_repository_flag_cannot_become_live_render_authority(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._fixture(root)
            policy = root / "config/media_render_worker_policy.json"
            value = json.loads(policy.read_text(encoding="utf-8"))
            value["live_connection"]["repository_connection_state_is_authoritative"] = True
            policy.write_text(json.dumps(value), encoding="utf-8")
            self.assertIn("LIVE_RENDER_READINESS_AUTHORITY_INVALID", validate(root))

    def test_preparation_service_cannot_duplicate_the_authoritative_rss_poller(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._fixture(root)
            service = root / "deploy/systemd/user/hf-site-agent-media-news.service"
            text = service.read_text(encoding="utf-8")
            text = text.replace(
                "ExecStart=/usr/bin/python3 -m scripts.media_news_pipeline",
                "ExecStartPre=/usr/bin/python3 -m scripts.media_source_daemon --db /tmp/q --once\n"
                "ExecStart=/usr/bin/python3 -m scripts.media_news_pipeline",
                1,
            )
            service.write_text(text, encoding="utf-8")
            self.assertIn("GCP_PREPARATION_SERVICE_CONTRACT_INVALID", validate(root))

    def test_authoritative_rss_contract_preserves_the_existing_database(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._fixture(root)
            policy = root / "config/media_small_host_policy.json"
            value = json.loads(policy.read_text(encoding="utf-8"))
            value["runtime_layout"]["move_or_reinitialize_existing_database"] = True
            policy.write_text(json.dumps(value), encoding="utf-8")
            self.assertIn("AUTHORITATIVE_RSS_INGRESS_CONTRACT_INVALID", validate(root))

    def test_timer_persistence_or_interval_drift_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._fixture(root)
            timer = root / "deploy/systemd/user/hf-site-agent-media-news.timer"
            timer.write_text(
                timer.read_text(encoding="utf-8")
                .replace("OnUnitInactiveSec=5min", "OnUnitInactiveSec=30min")
                .replace("Persistent=true", "Persistent=false"),
                encoding="utf-8",
            )
            self.assertIn("GCP_PREPARATION_TIMER_CONTRACT_INVALID", validate(root))

    def test_gcp_installer_cannot_start_installing_local_render_unit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._fixture(root)
            installer = root / "scripts/install_gcp_small_host_services.py"
            text = installer.read_text(encoding="utf-8")
            text = text.replace(
                '"hf-site-agent-media-news.timer",',
                '"hf-site-agent-media-news.timer",\n    "hf-site-agent-media-render@.service",',
                1,
            )
            installer.write_text(text, encoding="utf-8")
            self.assertIn("GCP_INSTALLER_UNIT_SCOPE_INVALID", validate(root))

    def test_user_render_unit_comments_are_not_install_sections(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._fixture(root)
            self.assertNotIn("GCP_USER_REMOTE_RENDER_UNIT_CONTRACT_INVALID", validate(root))

            service = root / "deploy/systemd/user/hf-site-agent-media-render@.service"
            service.write_text(
                service.read_text(encoding="utf-8") + "\n[Install]\nWantedBy=default.target\n",
                encoding="utf-8",
            )
            self.assertIn("GCP_USER_REMOTE_RENDER_UNIT_CONTRACT_INVALID", validate(root))


if __name__ == "__main__":
    unittest.main()
