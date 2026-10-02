import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch, MagicMock

from scripts.install_gcp_small_host_services import preflight, _worktree_has_unapproved_changes, _remote_voicevox_ready


ROOT = Path(__file__).resolve().parents[1]


class GcpSmallHostActivationTests(unittest.TestCase):
    def _case(self, credit=47159, forecast=12000, remote=False, ready=False, forecast_status="AVAILABLE"):
        with tempfile.TemporaryDirectory() as temp:
            root, home = Path(temp) / "repo", Path(temp) / "home"
            root.mkdir()
            home.mkdir()
            name = self._prepared(root, home)
            path = home / ".config/hf-site-agent/cloud-budget.json"
            evidence = json.loads(path.read_text())
            evidence.update(
                remaining_credit_jpy=credit,
                forecast_next_30d_jpy=forecast,
                forecast_status=forecast_status,
            )
            path.write_text(json.dumps(evidence))
            if remote:
                (home / ".local/share/voicevox_engine/linux-cpu-x64").rmdir()
                with (home / ".config/hf-site-agent/media.env").open("a") as env:
                    env.write("VOICEVOX_REMOTE_TUNNEL=1\nVOICEVOX_URL=http://127.0.0.1:50021\n")
            with patch("scripts.install_gcp_small_host_services._remote_voicevox_ready", return_value=ready):
                return preflight(root=root, home=home, instance_name=name, project_id="test-project",
                                 branch="ai-army/provider-v3", free_bytes=3 * 1024**3)

    def test_balance_above_authorized_ceiling_is_not_invalid(self):
        self.assertEqual(self._case(), [])

    def test_forecast_cannot_exceed_credit_or_authorized_ceiling(self):
        for credit, forecast in ((47159, 47050), (1000, 12000), (float("inf"), 0), (47000, float("nan"))):
            with self.subTest(credit=credit, forecast=forecast):
                self.assertIn("LIVE_BILLING_CREDIT_EVIDENCE_MISSING_STALE_OR_INVALID", self._case(credit, forecast))

    def test_insufficient_history_allows_existing_vm_with_positive_fresh_credit(self):
        self.assertEqual(
            self._case(
                credit=47142.06,
                forecast=None,
                forecast_status="UNAVAILABLE_INSUFFICIENT_HISTORY",
            ),
            [],
        )

    def test_unavailable_forecast_requires_exact_status_and_null_value(self):
        for status, forecast in (
            ("UNKNOWN", None),
            ("UNAVAILABLE_INSUFFICIENT_HISTORY", 0),
        ):
            with self.subTest(status=status, forecast=forecast):
                blockers = self._case(forecast=forecast, forecast_status=status)
                self.assertIn("LIVE_BILLING_CREDIT_EVIDENCE_MISSING_STALE_OR_INVALID", blockers)

    def test_activation_blocks_paid_route_render_or_timer_policy_drift(self):
        mutations = (
            ("RESIDENT_NEWS_PAID_ROUTE_POLICY_INVALID", "pipeline_model"),
            ("GCP_REMOTE_ONLY_RENDER_BOUNDARY_INVALID", "local_render"),
            ("GCP_24H_PREPARATION_TIMER_POLICY_INVALID", "timer_disabled"),
        )
        for expected, mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                root, home = Path(temp) / "repo", Path(temp) / "home"
                root.mkdir(); home.mkdir()
                name = self._prepared(root, home)
                if mutation == "pipeline_model":
                    path = root / "config/media_news_pipeline_policy.json"
                    policy = json.loads(path.read_text(encoding="utf-8"))
                    policy["paid_script_generation"]["model"] = "wrong/model"
                    path.write_text(json.dumps(policy), encoding="utf-8")
                else:
                    path = root / "config/media_small_host_policy.json"
                    policy = json.loads(path.read_text(encoding="utf-8"))
                    if mutation == "local_render":
                        policy["resource_controls"]["local_video_rendering_on_gcp_allowed"] = True
                    else:
                        policy["execution"]["preparation_timer_enabled_by_default"] = False
                    path.write_text(json.dumps(policy), encoding="utf-8")
                blockers = preflight(
                    root=root, home=home, instance_name=name, project_id="test-project",
                    branch="ai-army/provider-v3", free_bytes=3 * 1024**3,
                )
                self.assertIn(expected, blockers)

    def test_remote_engine_does_not_require_local_installation(self):
        self.assertEqual(self._case(remote=True, ready=True), [])

    def test_remote_engine_down_still_blocks(self):
        self.assertIn("REMOTE_VOICEVOX_TUNNEL_OR_CAST_UNAVAILABLE", self._case(remote=True))

    def test_remote_probe_rejects_external_urls_before_network(self):
        with patch("scripts.install_gcp_small_host_services.http.client.HTTPConnection") as connection:
            for url in ("https://example.com", "http://127.0.0.1:0", "http://127.0.0.1:65536", "http://127.0.0.1:50021/path"):
                self.assertFalse(_remote_voicevox_ready({"VOICEVOX_URL": url}))
            connection.assert_not_called()

    def test_remote_probe_checks_version_cast_and_redirects(self):
        cast = [{"name": name, "styles": [{"name": "ノーマル", "id": i}]} for i, name in enumerate(("ずんだもん", "四国めたん"))]
        values = {"VOICEVOX_URL": "http://127.0.0.1:50021", "VOICEVOX_EXPECTED_VERSION": "0.0.0"}
        for version, speakers, status, expected in (("0.0.0", cast, 200, True), ("wrong", cast, 200, False), ("0.0.0", [], 200, False), ("0.0.0", cast, 302, False)):
            with self.subTest(version=version, status=status, cast=speakers):
                responses = []
                for payload in (version, speakers):
                    response = MagicMock()
                    response.status = status
                    response.read.return_value = json.dumps(payload).encode()
                    responses.append(response)
                with patch("scripts.install_gcp_small_host_services.http.client.HTTPConnection") as constructor:
                    connection = constructor.return_value
                    connection.getresponse.side_effect = responses
                    self.assertEqual(_remote_voicevox_ready(values), expected)
                    connection.close.assert_called_once()

    def _prepared(self, root: Path, home: Path):
        for directory in ("config", "deploy/systemd/user"):
            (root / directory).mkdir(parents=True, exist_ok=True)
        (home / "hf-site-agent/runtime").mkdir(parents=True, exist_ok=True)
        (home / ".config/hf-site-agent").mkdir(parents=True, exist_ok=True)
        (home / ".local/share/voicevox_engine/linux-cpu-x64").mkdir(parents=True, exist_ok=True)
        host = json.loads((ROOT / "config/media_small_host_policy.json").read_text(encoding="utf-8"))
        pipeline = json.loads((ROOT / "config/media_news_pipeline_policy.json").read_text(encoding="utf-8"))
        ingress = json.loads((ROOT / "config/media_source_ingress_policy.json").read_text(encoding="utf-8"))
        (root / "config/media_small_host_policy.json").write_text(json.dumps(host), encoding="utf-8")
        (root / "config/media_news_pipeline_policy.json").write_text(json.dumps(pipeline), encoding="utf-8")
        (root / "config/media_source_ingress_policy.json").write_text(json.dumps(ingress), encoding="utf-8")
        db = home / "hf-site-agent/runtime/media-queue.sqlite3"
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE queue (id INTEGER PRIMARY KEY)")
            conn.execute("""CREATE TABLE source_feed_state (
                feed_id TEXT PRIMARY KEY, etag TEXT, last_modified TEXT,
                last_checked_at REAL NOT NULL, last_status TEXT NOT NULL
            )""")
            ingress = json.loads((ROOT / "config/media_source_ingress_policy.json").read_text(encoding="utf-8"))
            for feed in ingress["feeds"]:
                if feed.get("enabled") is True:
                    conn.execute(
                        "INSERT INTO source_feed_state(feed_id,etag,last_modified,last_checked_at,last_status) VALUES(?,?,?,?,?)",
                        (feed["feed_id"], None, None, time.time(), "OK"),
                    )
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

    def _git_worktree(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        (root / "config").mkdir(parents=True, exist_ok=True)
        policy = json.loads((ROOT / "config/media_small_host_policy.json").read_text(encoding="utf-8"))
        (root / "config/media_small_host_policy.json").write_text(json.dumps(policy), encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "init", "--quiet"], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Preflight Test"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "config/media_small_host_policy.json"], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "--quiet", "-m", "test policy"], check=True)

    def test_worktree_allows_only_known_untracked_runtime_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            self._git_worktree(root)
            artifacts = (
                "runtime/media-queue.sqlite3",
                "runtime/voice-cache/audio.wav",
                ".media-cache/assets/image.bin",
                "scripts/__pycache__/installer.pyc",
                "tests/__pycache__/test_installer.pyc",
            )
            for relative in artifacts:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"preserve-this-artifact")
            queue = root / "runtime/media-queue.sqlite3"
            before = queue.read_bytes()
            self.assertFalse(_worktree_has_unapproved_changes(root))
            self.assertEqual(queue.read_bytes(), before)

    def test_worktree_still_blocks_unknown_untracked_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            self._git_worktree(root)
            (root / "operator-notes.txt").write_text("unreviewed", encoding="utf-8")
            self.assertTrue(_worktree_has_unapproved_changes(root))

    def test_worktree_blocks_symlinked_allowed_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "repo"
            self._git_worktree(root)
            external = base / "external-runtime"
            external.mkdir()
            (external / "media-queue.sqlite3").write_bytes(b"preserve")
            (root / "runtime").symlink_to(external, target_is_directory=True)
            self.assertTrue(_worktree_has_unapproved_changes(root))

    def test_worktree_does_not_normalize_backslash_into_allowed_path(self):
        if os.name != "posix":
            self.skipTest("The production host is Linux and supports literal backslashes in filenames.")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            self._git_worktree(root)
            (root / r"runtime\escaped-file").write_text("unreviewed", encoding="utf-8")
            self.assertTrue(_worktree_has_unapproved_changes(root))

    def test_worktree_still_blocks_tracked_modifications(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            self._git_worktree(root)
            policy = root / "config/media_small_host_policy.json"
            policy.write_text(policy.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            self.assertTrue(_worktree_has_unapproved_changes(root))

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

    def test_preflight_blocks_when_authoritative_rss_poller_is_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            home = Path(temp) / "home"
            root.mkdir()
            home.mkdir()
            expected_name = self._prepared(root, home)
            db = home / "hf-site-agent/runtime/media-queue.sqlite3"
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE source_feed_state SET last_checked_at=?", (time.time() - 3600,))
            blockers = preflight(
                root=root,
                home=home,
                instance_name=expected_name,
                project_id="test-project",
                branch="ai-army/provider-v3",
                dirty=False,
                free_bytes=3 * 1024**3,
            )
            self.assertIn("AUTHORITATIVE_RSS_POLLER_STALE_OR_MISSING", blockers)

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
