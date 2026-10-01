import json
import os
from pathlib import Path
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
import unittest

from scripts.install_gcp_small_host_services import preflight


ROOT = Path(__file__).resolve().parents[1]


class GcpSmallHostActivationTests(unittest.TestCase):
    def _prepared(self, root: Path, home: Path):
        for directory in ("config", "deploy/systemd/user"):
            (root / directory).mkdir(parents=True, exist_ok=True)
        (home / "hf-site-agent/runtime").mkdir(parents=True, exist_ok=True)
        (home / ".config/hf-site-agent").mkdir(parents=True, exist_ok=True)
        (home / ".local/share/voicevox_engine/linux-cpu-x64").mkdir(parents=True, exist_ok=True)
        host = json.loads((ROOT / "config/media_small_host_policy.json").read_text(encoding="utf-8"))
        pipeline = json.loads((ROOT / "config/media_news_pipeline_policy.json").read_text(encoding="utf-8"))
        (root / "config/media_small_host_policy.json").write_text(json.dumps(host), encoding="utf-8")
        (root / "config/media_news_pipeline_policy.json").write_text(json.dumps(pipeline), encoding="utf-8")
        db = home / "hf-site-agent/runtime/media-queue.sqlite3"
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE queue (id INTEGER PRIMARY KEY)")
        env = home / ".config/hf-site-agent/media.env"
        env.write_text("OPENROUTER_API_KEY=test-placeholder\nVOICEVOX_EXPECTED_VERSION=0.0.0\n", encoding="utf-8")
        os.chmod(env, 0o600)
        billing = home / ".config/hf-site-agent/cloud-budget.json"
        billing.write_text(json.dumps({
            "project_id": "test-project",
            "billing_status": "ACTIVE",
            "remaining_credit_jpy": 47000,
            "credit_expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            "forecast_next_30d_jpy": 12000,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "source": "GOOGLE_CLOUD_BILLING_CONSOLE",
            "auto_top_up_enabled": False,
        }), encoding="utf-8")
        os.chmod(billing, 0o600)
        for unit in (
            "hf-site-agent-media-news.service",
            "hf-site-agent-media-news.timer",
            "hf-site-agent-media-render@.service",
        ):
            (root / "deploy/systemd/user" / unit).write_text("[Unit]\n", encoding="utf-8")
        engine = home / ".local/share/voicevox_engine/linux-cpu-x64"
        engine.mkdir(parents=True, exist_ok=True)
        return host["target"]["expected_instance_name"]

    def test_preflight_accepts_only_existing_vm_and_ready_local_prerequisites(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            home = Path(temp) / "home"
            root.mkdir()
            home.mkdir()
            # Build test fixtures from repository policy, without copying or initializing a live queue.
            expected_name = self._prepared(root, home)
            blockers = preflight(
                root=root,
                home=home,
                instance_name=expected_name,
                project_id="test-project",
                branch="ai-army/provider-v3",
                dirty=False,
                free_bytes=3 * 1024**3,
            )
            self.assertEqual(blockers, [])

    def test_preflight_blocks_without_fresh_project_billing_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            home = Path(temp) / "home"
            root.mkdir()
            home.mkdir()
            expected_name = self._prepared(root, home)
            (home / ".config/hf-site-agent/cloud-budget.json").unlink()
            blockers = preflight(
                root=root,
                home=home,
                instance_name=expected_name,
                project_id="test-project",
                branch="ai-army/provider-v3",
                dirty=False,
                free_bytes=3 * 1024**3,
            )
            self.assertIn("LIVE_BILLING_CREDIT_EVIDENCE_MISSING_STALE_OR_INVALID", blockers)

    def test_preflight_blocks_unknown_vm_and_keeps_publish_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            home = Path(temp) / "home"
            root.mkdir()
            home.mkdir()
            expected_name = self._prepared(root, home)
            blockers = preflight(
                root=root,
                home=home,
                instance_name="different-vm",
                project_id="test-project",
                branch="main",
                dirty=False,
                free_bytes=3 * 1024**3,
            )
            self.assertIn("GCP_INSTANCE_IDENTITY_UNVERIFIED", blockers)
            self.assertIn("CANONICAL_BRANCH_REQUIRED", blockers)


if __name__ == "__main__":
    unittest.main()
